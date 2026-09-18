# taxcalc-mcp-server/src/taxcalc_mcp_server/tools/llm.py
"""``llm.chat``: ungrounded chat through the W3 D1 cost-tracked proxy.

**Why route chat through a proxy at all, when the caller is already a model.** Because the proxy
is where the money is counted. Every call through it emits a cost line and returns
``X-Cost-Usd``; a tool that called the vendor API directly would spend from the same budget and
appear in none of the W6 D4 cost dashboards. The proxy is also where the rate limit lives, which
is why the 429 mapping below is the single most load-bearing line in this module.

**Why 429 gets its own code.** :func:`taxcalc_mcp_server.errors._map_http` maps it to 4290, and
the W7 D5 agent branches on exactly that number to apply exponential backoff. Folded into the
generic 5030 it would be indistinguishable from "the server broke", and the agent's correct
response to those two is opposite: back off and retry one, stop immediately on the other.

**Two upstream wire shapes, one published contract.** The MCP-facing schema is always
``messages``/``max_tokens`` - that is the shape an LLM client naturally produces and what the
W7 D5 agent emits - and :func:`_wire_shape` picks the body this module actually sends from the
configured :attr:`Settings.llm_proxy_chat_path`:

* ``/v1/chat/completions`` (the OpenAI-compatible shape): the ``messages`` array goes out intact,
  ``max_tokens`` is a real upstream ceiling, and the reply is read from
  ``choices[0].message.content`` with ``usage.prompt_tokens``/``usage.completion_tokens``.
* anything else, default ``/v1/completions`` (this capstone's ``LlmProxyController``): the turns
  are flattened into the single ``prompt`` field that record accepts, tagged with ``feature`` for
  cost attribution, and the reply is read from ``text`` with ``inputTokens``/``outputTokens``.

The default is the second one because it is the route that exists in this repo; the first is
supported so pointing the setting at a generic proxy needs no code change. Keeping the
translation *here* rather than pushing either proxy's shape up into the tool schema is the whole
point of an adapter: the upstream wire format is a deployment detail, and swapping it must not
change what a client sees in ``tools/list``.

**Why ``max_tokens`` is not always an upstream ceiling.** On the chat shape it is forwarded and
enforced by the proxy. On this repo's ``/v1/completions``, ``CompletionRequest`` is a three-field
record with no token ceiling, and Spring Boot ignores unknown JSON properties by default - so a
forwarded ``maxTokens`` would be silently dropped rather than rejected. It is still validated
here (1..4096) and recorded on the span, because a bound the caller declared is worth checking
and worth seeing even when the upstream cannot honour it; claiming it was enforced would be the
only real mistake available.
"""

from typing import Annotated, Final, Literal

from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field

from taxcalc_mcp_server.app import TRACE_PROJECT, ctx, mcp
from taxcalc_mcp_server.errors import _map_http
from taxcalc_mcp_server.numeric import as_minor_units, as_token_count
from taxcalc_mcp_server.observability import COST_SOURCE_PROXY, observe
from taxcalc_mcp_server.tenancy import auth_headers
from taxcalc_mcp_server.tools.orders import TENANT_PATTERN


class ChatMessage(BaseModel):
    """One turn of a conversation."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system"] = Field(description="Who produced this turn.")
    content: str = Field(min_length=1, max_length=8000, description="The turn's text.")


class ChatArgs(BaseModel):
    """Arguments for ``llm.chat``."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(
        min_length=1, max_length=50, description="Conversation so far, oldest first."
    )
    max_tokens: int = Field(
        ge=1, le=4096, description="Ceiling on the generated reply; the proxy bills per token."
    )
    tenant_id: str = Field(pattern=TENANT_PATTERN, description="Billing tenant, e.g. tenant-a.")


class ChatReply(BaseModel):
    """The pre-shaped reply.

    ``model`` is kept because a caller comparing two answers needs to know whether they came
    from the same model; the token counts are kept because they are how a caller notices its own
    prompt growing. The proxy's full response carries more - resolved model id, feature tag,
    internal request id - and none of it changes what the caller does next, so none of it is
    spent from the model's context window.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    input_tokens: int
    output_tokens: int


#: The ``feature`` tag every call from this server carries into the proxy's cost accounting. The
#: proxy groups spend by it, so a flat string here is what makes "how much did the MCP surface
#: cost this month" a query rather than an investigation.
COST_FEATURE: Final[str] = "mcp.llm.chat"

_DESC_CHAT: Final[str] = (
    "Send a conversation to the cost-tracked LLM proxy and return the reply, the model that "
    "produced it, and the input/output token counts. Use this for open-ended generation, "
    "rewriting, summarising or reasoning that needs no access to the tenant's documents and no "
    "access to order data. Do NOT use it to answer questions about the tenant's own filings, "
    "policies or indexed documents - the reply would be ungrounded and uncitable; call "
    "rag.retrieve_and_generate instead. Do NOT use it to read or change an order. "
    "Example: messages=[{'role':'user','content':'Summarise this deduction rule.'}], "
    "max_tokens=256, tenant_id='tenant-a' returns the summary with its token counts."
)

_CHAT_ARGS: Final = ChatArgs.model_fields


#: Path suffix that marks an upstream as speaking the OpenAI-compatible chat shape. Matched on
#: the suffix rather than the whole path so a proxy mounted under a prefix
#: (``/internal/v1/chat/completions``) is still recognised.
CHAT_COMPLETIONS_SUFFIX: Final[str] = "/chat/completions"


def _wire_shape(path: str) -> Literal["chat", "completions"]:
    """Decide which upstream body shape the configured proxy path expects.

    Derived from the path rather than carried in a second setting, because two settings that must
    agree are two settings that can disagree: a deployment pointed at ``/v1/chat/completions``
    with the shape flag left on ``completions`` would send a ``prompt`` field to an endpoint that
    reads ``messages`` and get an empty completion with a 200, which is the worst available
    failure - billed, logged as success, and wrong.

    :param path: The configured :attr:`Settings.llm_proxy_chat_path`.
    :returns: ``"chat"`` for an OpenAI-compatible endpoint, ``"completions"`` otherwise.
    """
    return "chat" if path.rstrip("/").endswith(CHAT_COMPLETIONS_SUFFIX) else "completions"


def _flatten(messages: list[ChatMessage]) -> str:
    """Render a message list as the single prompt this capstone's proxy accepts.

    Role-labelled and newline-separated rather than concatenated, so the model can still tell a
    system instruction from a user turn. Lossy by nature - the ``/v1/completions`` contract has
    one prompt field - but lossy in a way that preserves the distinction that matters. Only the
    ``completions`` shape needs this; the chat shape forwards the turns intact.

    :param messages: The conversation, oldest first.
    :returns: The flattened prompt.
    """
    return "\n\n".join(f"{m.role}: {m.content}" for m in messages)


def _request_body(args: ChatArgs, shape: Literal["chat", "completions"]) -> dict[str, object]:
    """Build the upstream JSON body for the shape this deployment speaks.

    :param args: Validated arguments.
    :param shape: From :func:`_wire_shape`.
    :returns: The body to post.
    """
    if shape == "chat":
        return {
            "messages": [{"role": m.role, "content": m.content} for m in args.messages],
            "max_tokens": args.max_tokens,
        }
    return {
        "prompt": _flatten(args.messages),
        "feature": COST_FEATURE,
    }


def _parse_reply(body: dict[str, object], shape: Literal["chat", "completions"]) -> ChatReply:
    """Read the upstream response into the published DTO.

    Defensive ``.get`` chains rather than indexing throughout: a proxy that answers 200 with a
    body missing ``usage`` should cost the caller a zero token count, not an exception that
    surfaces as a 5030 and hides the answer it did return.

    :param body: The decoded JSON response.
    :param shape: From :func:`_wire_shape`.
    :returns: The pre-shaped reply.
    """
    if shape == "chat":
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        message = first.get("message", {}) if isinstance(first, dict) else {}
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return ChatReply(
            text=str(message.get("content", "")) if isinstance(message, dict) else "",
            model=str(body.get("model", "")),
            input_tokens=as_token_count(usage.get("prompt_tokens", 0)),
            output_tokens=as_token_count(usage.get("completion_tokens", 0)),
        )
    # `resolvedModel` first, `model` as the fallback: `CompletionResponse` carries both, and the
    # resolved dated snapshot ("claude-haiku-4-5-20251001") is the one that answers "did these
    # two replies come from the same model". The bare id the caller asked for cannot.
    return ChatReply(
        text=str(body.get("text", "")),
        model=str(body.get("resolvedModel") or body.get("model", "")),
        input_tokens=as_token_count(body.get("inputTokens", 0)),
        output_tokens=as_token_count(body.get("outputTokens", 0)),
    )


@traceable(name="llm.chat", project_name=TRACE_PROJECT)
async def _chat(args: ChatArgs) -> dict[str, object]:
    """Forward a conversation to the LLM proxy. Traced; called only by the tool handler.

    :param args: Validated arguments.
    :returns: A :class:`ChatReply` dumped in JSON mode.
    :raises McpError: 4290 on a proxy rate limit - the code the W7 D5 agent backs off on - and
        4030 when the JWT lacks the proxy's scope.

    The request and reply shapes come from :func:`_wire_shape`, so this body is the same whether
    the deployment points at ``/v1/completions`` or ``/v1/chat/completions``.
    """
    c = ctx()
    async with observe("llm.chat", args.tenant_id) as span:
        span["turns"] = len(args.messages)

        # An absolute URL through the SAME client as the order calls: the base_url is the order
        # service, so this one line is what keeps a second connection pool, a second timeout
        # policy and a second thing to close from existing.
        path = c.settings.llm_proxy_chat_path
        shape = _wire_shape(path)
        # Recorded so an operator reading a span knows which body actually went out. Without it,
        # "the reply text was empty" and "we spoke the wrong dialect at this endpoint" look
        # identical in the logs.
        span["wire_shape"] = shape
        span["max_tokens"] = args.max_tokens

        url = f"{c.settings.normalised_llm_proxy_url()}{path}"
        r = await c.http.post(
            url,
            json=_request_body(args, shape),
            headers=auth_headers(c.settings.bearer_jwt.get_secret_value(), args.tenant_id),
        )
        span["http_status"] = r.status_code
        # The proxy reports the call's cost in a response header. Echoed into this server's own
        # structured log - as integer minor units, per the W6 D4 money discipline, never an
        # inexact binary value - so Grafana can aggregate MCP-attributed spend without joining
        # against the proxy's logs. A missing header means "the proxy did not price this call",
        # which is a 0 here and a question for the proxy's own metrics, not an error for the
        # caller. Recorded BEFORE the status check so a rate-limited call still reports what it
        # cost - a 429 that billed is exactly the case an operator wants to see.
        span["cost_usd_minor"] = as_minor_units(r.headers.get("X-Cost-Usd"))
        span["cost_source"] = COST_SOURCE_PROXY
        if r.status_code != 200:
            raise _map_http(r.status_code, r.text)

        body = r.json()
        return _parse_reply(body if isinstance(body, dict) else {}, shape).model_dump(mode="json")


@mcp.tool(name="llm.chat", description=_DESC_CHAT)
async def llm_chat(
    messages: Annotated[list[ChatMessage], _CHAT_ARGS["messages"]],
    max_tokens: Annotated[int, _CHAT_ARGS["max_tokens"]],
    tenant_id: Annotated[str, _CHAT_ARGS["tenant_id"]],
) -> dict[str, object]:
    """Chat through the cost-tracked proxy. The MCP boundary for :func:`_chat`.

    :param messages: Conversation so far, oldest first.
    :param max_tokens: Ceiling on the generated reply.
    :param tenant_id: Billing tenant.
    :returns: The reply as a JSON-safe dict.
    """
    return await _chat(
        ChatArgs(messages=messages, max_tokens=max_tokens, tenant_id=tenant_id)
    )
