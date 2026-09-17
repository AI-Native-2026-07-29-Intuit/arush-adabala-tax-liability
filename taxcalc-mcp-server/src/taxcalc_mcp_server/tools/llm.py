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

**This capstone's proxy is not the generic one.** The Java service's ``LlmProxyController``
serves ``POST /v1/completions`` taking ``{prompt, model, feature}``, not a ``/v1/chat/completions``
endpoint taking a ``messages`` array. The MCP-facing schema keeps the ``messages`` shape anyway -
that is the shape an LLM client naturally produces, and it is what the W7 D5 agent will emit -
and this module translates at the boundary. Putting the translation here rather than pushing the
proxy's shape up into the tool schema is the whole point of an adapter: the upstream's wire
format is an implementation detail, and changing it should not change the published contract.
"""

from decimal import Decimal
from typing import Annotated, Final, Literal

from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field

from taxcalc_mcp_server.app import ctx, mcp
from taxcalc_mcp_server.errors import _map_http
from taxcalc_mcp_server.observability import observe
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


def _flatten(messages: list[ChatMessage]) -> str:
    """Render a message list as the single prompt this capstone's proxy accepts.

    Role-labelled and newline-separated rather than concatenated, so the model can still tell a
    system instruction from a user turn. Lossy by nature - the proxy's contract has one prompt
    field - but lossy in a way that preserves the distinction that matters.

    :param messages: The conversation, oldest first.
    :returns: The flattened prompt.
    """
    return "\n\n".join(f"{m.role}: {m.content}" for m in messages)


@traceable(name="llm.chat", project_name="taxcalc-mcp-server")
async def _chat(args: ChatArgs) -> dict[str, object]:
    """Forward a conversation to the LLM proxy. Traced; called only by the tool handler.

    :param args: Validated arguments.
    :returns: A :class:`ChatReply` dumped in JSON mode.
    :raises McpError: 4290 on a proxy rate limit - the code the W7 D5 agent backs off on - and
        4030 when the JWT lacks the proxy's scope.
    """
    c = ctx()
    async with observe("llm.chat", args.tenant_id) as span:
        span["turns"] = len(args.messages)

        # An absolute URL through the SAME client as the order calls: the base_url is the order
        # service, so this one line is what keeps a second connection pool, a second timeout
        # policy and a second thing to close from existing.
        url = f"{c.settings.normalised_llm_proxy_url()}{c.settings.llm_proxy_chat_path}"
        r = await c.http.post(
            url,
            json={
                "prompt": _flatten(args.messages),
                "maxTokens": args.max_tokens,
                "feature": COST_FEATURE,
            },
            headers=auth_headers(c.settings.bearer_jwt.get_secret_value(), args.tenant_id),
        )
        span["http_status"] = r.status_code
        # The proxy reports the call's cost in a response header. Echoed into this server's own
        # structured log - as integer minor units, per the W6 D4 money discipline, never a
        # float - so the Grafana dashboard can aggregate MCP-attributed spend without joining
        # against the proxy's logs. A missing header means "the proxy did not price this call",
        # which is a 0 here and a question for the proxy's own metrics, not an error for the
        # caller. Recorded BEFORE the status check so a rate-limited call still reports what it
        # cost - a 429 that billed is exactly the case an operator wants to see.
        span["cost_usd_minor"] = _cost_minor(r.headers.get("X-Cost-Usd"))
        if r.status_code != 200:
            raise _map_http(r.status_code, r.text)

        body = r.json()
        reply = ChatReply(
            text=body.get("text", ""),
            model=body.get("modelId", ""),
            input_tokens=int(body.get("inputTokens", 0)),
            output_tokens=int(body.get("outputTokens", 0)),
        )
        return reply.model_dump(mode="json")


def _cost_minor(header: str | None) -> int:
    """Parse ``X-Cost-Usd`` into integer minor units (cents).

    Integer, not float, for the same reason refunds are ``Decimal``: this number is summed across
    every request in a dashboard, and summing floats accumulates error in the direction nobody
    audits. Parsed defensively because a missing or malformed header must never fail a tool call
    that otherwise succeeded - the caller got their answer, and a log field is not worth an error.

    :param header: The raw header value, or ``None``.
    :returns: Cents, rounded down; ``0`` when absent or unparseable.
    """
    if not header:
        return 0
    try:
        return int(Decimal(header) * 100)
    except (ArithmeticError, ValueError):
        return 0


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
