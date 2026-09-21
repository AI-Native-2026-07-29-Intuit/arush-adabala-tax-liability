# taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/synthesis.py
"""Synthesis agent: a streamed, forced tool call returning a validated :class:`FinalAnswer`.

**Structured output is the contract, not a formatting preference.** Everything downstream of this
node - the SSE bridge's ``2:`` data event, the React client rendering citations, the trajectory
eval asserting on an answer substring, the RAGAS sampler scoring faithfulness against the cited
context - reads *fields*. A free-text reply makes every one of those a parsing problem, and a
parser over model prose is a parser that works until the model phrases something differently.
A forced call to the :data:`TOOL_NAME` tool, whose schema is generated from
:class:`FinalAnswer`, moves that failure from "wrong field silently" to "validation error,
retried, then raised".

**Why this node streams, and why that cost it Instructor.** The ``0:`` channel of the W4 D4
data-stream protocol exists so tokens appear as they are written, and it was dead: the bridge
filtered ``on_chat_model_stream``, which ``astream_events`` emits only for LangChain
``BaseChatModel`` runnables, and every model call here goes through the Anthropic SDK. The real
graph emitted ``on_chain_*`` and nothing else, so the client waited in silence and then received
the whole answer at once.

Instructor could not fix that on its own. Its partial-streaming path never emits the usage hook -
the hook fires on a response whose ``usage`` does not exist until the stream has been consumed -
so adopting it would have produced a synthesis call that streams **or** one that is billed. The
per-request ceiling is not optional, so this node now owns its stream: ``messages.stream()``
gives the deltas *and* ``get_final_message()``, which carries exact usage for every attempt.

Two readers of one stream, with the tolerant one confined to the cosmetic job. The prose deltas
come from a partial parse of the arguments JSON as it arrives (see :func:`_growing_text`); the
answer itself comes from the SDK's own parse of the completed message. A bug in the incremental
parse stutters the UI and cannot corrupt an answer.

:data:`MAX_RETRIES` is what makes the retry a *repair* rather than a re-roll: the Pydantic
validation error is fed back to the model as a ``tool_result``, so the next attempt is told which
field was wrong and why. Two rather than five because a model that cannot satisfy this schema
twice is not going to on the fifth attempt, and each attempt is a paid call against a per-request
budget - which is why every attempt is recorded, not just the one that succeeded.

``ConfigDict(extra="forbid")`` on both models is the other half. Without it a model that invents
``"sources"`` alongside ``"citations"`` validates cleanly, the invented field is dropped, and the
answer ships with an empty citation list and no error anywhere. Forbidding extras turns that into
a validation error the retry can actually fix.

**The refusal path is a prompt instruction backed by a schema field, and it needs both.** When
``docs`` and ``tool_results`` are both empty - every retrieval leg timed out, or the corpus has
nothing - the model is told to set ``confidence`` below 0.4, return a refusal in ``text``, and
cite nothing. The ``confidence`` field is what makes that machine-checkable: a caller, the eval
suite, and the RAGAS sampler can all branch on a number rather than trying to detect an apology
in prose. A prompt alone would produce a polite refusal that *looks* like an answer to everything
downstream.

Fabricated citations are the specific failure this guards. A model asked to cite with nothing to
cite from will invent plausible document ids, and a fabricated ``doc_id`` is worse than no
citation at all: it renders as a real source in the UI and survives every check that only
verifies the field is present.
"""

from __future__ import annotations

import contextlib
from typing import Any, Final, cast

import jiter
from anthropic import AsyncAnthropic
from anthropic.types import Message, ToolUseBlock
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.deps import AgentNode, budget_guard
from taxcalc_agent_svc.nodes._deadline import deadline
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState

#: Confidence at or below which the answer is treated as a refusal. Named so the prompt, the
#: eval suite and any downstream consumer branch on one constant rather than three copies of
#: ``0.4`` that can drift apart.
REFUSAL_CONFIDENCE: Final[float] = 0.4

#: Ceiling on the synthesis completion. The largest of the three because this is the node that
#: actually writes the answer.
MAX_TOKENS: Final[int] = 1024

#: Repair attempts on a Pydantic validation failure. See the module docstring.
MAX_RETRIES: Final[int] = 2

#: Name of the single tool the model is forced to call. The model returns its answer by calling
#: this tool, which is how a *schema* rather than a *request* governs the reply: the SDK parses
#: the tool's arguments and this module validates them, so a malformed answer is a validation
#: error at a known place instead of a parse of model prose.
TOOL_NAME: Final[str] = "final_answer"

#: Custom-event name carrying one prose delta to the SSE bridge.
#:
#: **Why a custom event and not ``on_chat_model_stream``.** That event is emitted only for
#: LangChain ``BaseChatModel`` runnables, and this service calls the Anthropic SDK directly - so
#: the bridge's text channel had nothing to carry and the real graph emitted ``on_chain_*`` and
#: nothing else. Adopting a LangChain model would not have fixed it either: under a forced tool
#: call the streamed tokens are fragments of the arguments JSON, so the deltas would have been
#: ``{"text": "Exc`` rather than prose. Extracting the prose is required either way, and once it
#: is extracted a named custom event says exactly what it carries.
ANSWER_DELTA_EVENT: Final[str] = "answer_delta"

#: The answer text a deadline-exceeded synthesis carries. Exactly the sentinel string and nothing
#: appended to it, so a consumer can compare rather than substring-match: the SSE bridge, the eval
#: suite and this module's own tests all recognise the degradation by ``text == DEADLINE_TEXT``,
#: and a prose tail on the end would make every one of them a ``startswith`` that a later reword
#: quietly breaks. The explanation a human needs is in ``confidence=0.0`` and in the
#: ``deadline_exceeded`` metadata on the span, which is where a machine can act on it.
DEADLINE_TEXT: Final[str] = "[deadline exceeded]"


class Citation(BaseModel):
    """One supporting document reference.

    :ivar doc_id: The cited document. Must come from the supplied context; see the module
        docstring on fabricated citations.
    :ivar quote: The supporting span. Bounded at both ends on purpose - a floor of 10 characters
        rejects a token-long "quote" that supports nothing, and a ceiling of 240 rejects a model
        that pastes a whole chunk back in place of choosing the relevant sentence.
    """

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    quote: str = Field(min_length=10, max_length=240)


class FinalAnswer(BaseModel):
    """The typed answer every consumer of this service reads.

    :ivar text: The answer, or the refusal when there was nothing to ground it in.
    :ivar citations: Supporting references. Defaults to empty, which is the correct value on the
        refusal path - ``default_factory=list`` rather than ``default=[]`` because a mutable
        default is shared across every instance ever constructed.
    :ivar confidence: 0-1. At or below :data:`REFUSAL_CONFIDENCE` the answer is a refusal.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


_SYSTEM: Final[str] = (
    "You are the answer assistant for the Taxpayer support stack. "
    "Tenant context is supplied; cite only from the supplied docs. If neither docs nor "
    f"tool_results support a grounded answer, set confidence below {REFUSAL_CONFIDENCE} "
    "and return a refusal in the text field. Never fabricate citations."
)


def build_user_prompt(state: AgentState) -> str:
    """Render the evidence the model is allowed to answer from.

    The document count is stated explicitly rather than left for the model to infer from the
    list, because "0 documents" is the signal that triggers the refusal path and a model reading
    an empty list often narrates around it instead.

    :param state: The graph state.
    :returns: The user turn.
    """
    docs = state.get("docs") or []
    tool_results = state.get("tool_results") or {}
    return (
        f"Tenant: {state.get('tenant_id', '')}\n"
        f"Question: {state.get('question', '')}\n"
        f"Docs ({len(docs)}): {docs or ''}\n"
        f"Tool results: {tool_results or ''}"
    )


def _tool_schema() -> dict[str, Any]:
    """Render :class:`FinalAnswer` as the one tool the model may call.

    Generated from the model rather than written out, so a field added to :class:`FinalAnswer`
    reaches the model automatically. A hand-copied schema is a second source of truth that drifts
    silently: the model keeps answering in the old shape and the new field is simply never
    populated.

    :returns: An Anthropic tool definition.
    """
    return {
        "name": TOOL_NAME,
        "description": "Return the grounded answer, its citations and a confidence score.",
        "input_schema": FinalAnswer.model_json_schema(),
    }


def _growing_text(buffer: str) -> str:
    """Extract the ``text`` field from a partially-received arguments JSON.

    The model streams its tool arguments as JSON fragments, so mid-stream the buffer is invalid
    JSON - ``{"text": "Exclusive and regu`` - and ``json.loads`` raises on every chunk until the
    last. ``jiter``'s ``trailing-strings`` partial mode parses exactly that shape, returning the
    prefix of the string it has so far, which is what makes a token-by-token reveal possible.

    **Nothing downstream of the stream depends on this.** The answer itself is taken from
    ``get_final_message()``, which the SDK has parsed completely; this function only decides what
    the *deltas* look like. A bug here shows a cosmetic stutter in the UI and cannot corrupt an
    answer, which is the reason the incremental parse is allowed to be best-effort at all.

    :param buffer: The arguments JSON received so far.
    :returns: The answer text so far, empty when the buffer has not yet reached that field.
    """
    try:
        parsed = jiter.from_json(buffer.encode(), partial_mode="trailing-strings")
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    text = parsed.get("text")
    return text if isinstance(text, str) else ""


async def _emit_delta(delta: str, config: RunnableConfig | None) -> None:
    """Send one prose delta to the SSE bridge, or silently do nothing.

    ``config`` is passed explicitly rather than left to ``adispatch_custom_event`` to discover
    from a context variable. The node body runs inside a task created by the ``@deadline``
    wrapper, and a dispatch that depends on ambient context is a dispatch that works until
    something between here and the graph schedules differently.

    **A stream that cannot be dispatched is not an error.** Outside a LangGraph run - the smoke
    script, the trajectory eval, a unit test calling this node directly - there is no parent run
    to attach an event to, and LangChain raises. Failing the answer because its *cosmetic*
    token-by-token reveal had nowhere to go would trade the whole response for a progress bar.

    :param delta: The newly generated characters.
    :param config: The node's runnable config, carrying the callbacks the event is dispatched on.
    """
    # Suppressing "Unable to dispatch an adhoc event without a parent run id" - see above.
    with contextlib.suppress(RuntimeError):
        await adispatch_custom_event(ANSWER_DELTA_EVENT, {"delta": delta}, config=config)


async def _stream_once(
    client: AsyncAnthropic,
    settings: Settings,
    messages: list[dict[str, Any]],
    guard: BudgetGuard,
    config: RunnableConfig | None,
) -> tuple[Message, dict[str, Any]]:
    """Make one streamed completion, emitting prose deltas as they arrive.

    The deltas are emitted from a *partial* parse of the arguments JSON while the final answer is
    taken from the SDK's own parse of the completed message. Two readers of one stream, with the
    tolerant one confined to the cosmetic job.

    :param client: The tagged Anthropic client.
    :param settings: Validated configuration.
    :param messages: The conversation so far - one user turn, plus the repair turns on a retry.
    :param guard: The request's cost ceiling, credited with this attempt's usage.
    :param config: The node's runnable config, for dispatching the deltas.
    :returns: The completed message and the tool arguments the model produced.
    """
    buffer = ""
    sent = 0
    async with client.messages.stream(
        model=settings.model,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM,
        messages=cast(Any, messages),
        tools=cast(Any, [_tool_schema()]),
        # Forced, not merely offered. Left to choose, a model asked a question it cannot ground
        # will sometimes reply in prose instead of calling the tool, and the answer arrives in a
        # shape nothing downstream reads.
        tool_choice={"type": "tool", "name": TOOL_NAME},
    ) as stream:
        async for event in stream:
            if event.type != "content_block_delta":
                continue
            delta = event.delta
            fragment = getattr(delta, "partial_json", None)
            if not fragment:
                continue
            buffer += fragment
            text = _growing_text(buffer)
            if len(text) > sent:
                # Only the NEWLY arrived characters. The client appends each delta to what it has
                # already rendered, so re-sending the whole prefix would repeat the answer once
                # per token.
                await _emit_delta(text[sent:], config)
                sent = len(text)
        final = await stream.get_final_message()

    # Recorded per ATTEMPT, inside the loop that may repair. Billing only the successful attempt
    # would under-count exactly the requests that cost the most - the ones the model got wrong
    # first - and those are the requests a cost alarm most needs to see.
    guard.record_call(final)

    arguments: dict[str, Any] = {}
    for block in final.content:
        if isinstance(block, ToolUseBlock) and block.name == TOOL_NAME:
            arguments = cast(dict[str, Any], block.input)
            break
    return final, arguments


async def _synthesis(
    state: AgentState, config: RunnableConfig | None, settings: Settings
) -> dict[str, Any]:
    """Produce the typed answer, streaming its prose as it is written.

    Undecorated on purpose - :func:`make_synthesis_node` applies both decorators in the order
    that matters. See :mod:`taxcalc_agent_svc.nodes._deadline`.

    **The repair loop is written out rather than delegated.** It is the same contract Instructor
    provided - re-ask with the validation error attached, at most :data:`MAX_RETRIES` times - and
    it is here because this node now owns its stream. Instructor cannot stream and report usage
    at once: its partial-streaming path never emits the usage hook, because the hook fires on a
    response object whose ``usage`` does not exist until the stream has been consumed. Keeping it
    would have meant a synthesis call that streams or a synthesis call that is billed, and the
    budget ceiling is not optional.

    :param state: The graph state.
    :param config: The LangGraph config carrying the request's budget guard.
    :param settings: Validated configuration.
    :returns: A partial state carrying ``answer`` as serialised JSON, this node's spend, and its
        name appended to ``visited_nodes``.
    :raises BudgetExceeded: when the per-request ceiling is reached before a call.
    :raises ValidationError: when the model cannot produce a valid answer within the retries.
    """
    guard = budget_guard(config)
    spent_before = guard.spent_usd_e5

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value() or None,
        default_headers={"X-Agent": "synthesis"},
    )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": build_user_prompt(state)}
    ]
    answer: FinalAnswer | None = None
    last_error: ValidationError | None = None

    for _ in range(MAX_RETRIES + 1):
        # Checked before EVERY attempt, not once before the first. A repair is a paid call like
        # any other, and a loop that checked the ceiling once could spend three completions
        # against a budget that was exhausted after the first.
        guard.check_or_raise()
        final, arguments = await _stream_once(client, settings, messages, guard, config)
        try:
            answer = FinalAnswer.model_validate(arguments)
            break
        except ValidationError as exc:
            last_error = exc
            # The error is fed back verbatim, which is what makes the next attempt a REPAIR
            # rather than a re-roll: the model is told which field was wrong and why, instead of
            # being asked the same question again and left to guess differently.
            messages.append({"role": "assistant", "content": final.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": _tool_use_id(final),
                            "is_error": True,
                            "content": f"Validation failed: {exc}. Call {TOOL_NAME} again, "
                            "corrected.",
                        }
                    ],
                }
            )

    if answer is None:
        # Raised, not degraded into a low-confidence answer. A model that cannot satisfy this
        # schema three times is not producing something worth rendering, and the SSE bridge's
        # internal error code says so honestly.
        raise last_error if last_error is not None else RuntimeError("no answer produced")

    return {
        # Serialised rather than held as a model instance. The state is checkpointed to Postgres
        # on every super-step, and a JSON string round-trips through that unchanged; a Pydantic
        # object would depend on the checkpointer's serde reconstructing the exact class, which
        # breaks the first time this model gains a field between a checkpoint and its resume.
        "answer": answer.model_dump_json(),
        "cost_usd_e5": guard.spent_usd_e5 - spent_before,
        "visited_nodes": ["synthesis_agent"],
    }


def _tool_use_id(message: Message) -> str:
    """Find the id of the tool call being repaired.

    :param message: The completion whose tool call failed validation.
    :returns: The tool_use id, or an empty string when the model called no tool.
    """
    for block in message.content:
        if isinstance(block, ToolUseBlock):
            return block.id
    return ""


def make_synthesis_node(
    settings: Settings,
) -> AgentNode:
    """Build the deadline-bounded synthesis node.

    **The sentinel is a serialised** :class:`FinalAnswer` **whose text is exactly**
    :data:`DEADLINE_TEXT`, **not the bare string.** ``answer`` is the one state slot every consumer
    reads, and every one of them reads it as JSON - the SSE bridge's
    ``_final_answer_payload`` parses it to build the ``2:`` data event. A bare marker string would
    take the bridge's malformed-answer branch, so the client would get a text-only payload with no
    ``confidence`` field to branch on and no way to distinguish a timeout from a model that
    answered badly. Wrapping the same string in a valid answer with ``confidence=0.0`` makes the
    degradation both renderable and machine-checkable, and keeps the text comparable by equality.

    :param settings: Validated configuration supplying the deadline and the project name.
    :returns: The node callable LangGraph registers as ``synthesis_agent``.
    """
    timeout_answer = FinalAnswer(
        text=DEADLINE_TEXT, citations=[], confidence=0.0
    ).model_dump_json()

    # @traceable OUTERMOST, @deadline applied FIRST. See make_api_node and _deadline.py.
    @traceable(name="synthesis_agent", project_name=settings.langsmith_project)
    @deadline(seconds=settings.deadline_synthesis_s, sentinel={"answer": timeout_answer})
    async def synthesis_node(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        """Write the grounded answer under this node's deadline.

        :param state: The graph state.
        :param config: The LangGraph config carrying the request's budget guard.
        :returns: A partial state carrying ``answer``.
        """
        return await _synthesis(state, config, settings)

    # cast, with a reason. `@traceable` returns a wrapper declared as `(*args, **kwargs)`, so the
    # named-parameter shape `AgentNode` (and LangGraph's own `_NodeWithConfig`) requires is erased
    # at the type level even though `functools.wraps` preserves it at runtime. The cast restores
    # the fact the decorator loses; the alternative is annotating every `add_node` call `Any`,
    # which turns off checking for the one argument most worth checking.
    return cast(AgentNode, synthesis_node)
