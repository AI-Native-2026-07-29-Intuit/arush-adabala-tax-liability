# taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/synthesis.py
"""Synthesis agent: Instructor + Claude returning a validated :class:`FinalAnswer`.

**Structured output is the contract, not a formatting preference.** Everything downstream of this
node - the SSE bridge's ``2:`` data event, the React client rendering citations, the trajectory
eval asserting on an answer substring, the RAGAS sampler scoring faithfulness against the cited
context - reads *fields*. A free-text reply makes every one of those a parsing problem, and a
parser over model prose is a parser that works until the model phrases something differently.
``response_model=FinalAnswer`` moves that failure from "wrong field silently" to "validation
error, retried, then raised".

``max_retries=2`` is what makes the retry a *repair* rather than a re-roll: Instructor feeds the
Pydantic validation error back to the model, so the second attempt is told which field was wrong
and why. Two rather than five because a model that cannot satisfy this schema twice is not going
to on the fifth attempt, and each attempt is a paid call against a per-request budget.

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

from typing import Any, Final, cast

import instructor
from anthropic import AsyncAnthropic
from langchain_core.runnables import RunnableConfig
from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field

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

#: Instructor repair attempts on a Pydantic validation failure. See the module docstring.
MAX_RETRIES: Final[int] = 2


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


async def _synthesis(
    state: AgentState, config: RunnableConfig | None, settings: Settings
) -> dict[str, Any]:
    """Produce the typed answer.

    Undecorated on purpose - :func:`make_synthesis_node` applies both decorators in the order
    that matters. See :mod:`taxcalc_agent_svc.nodes._deadline`.

    :param state: The graph state.
    :param config: The LangGraph config carrying the request's budget guard.
    :param settings: Validated configuration.
    :returns: A partial state carrying ``answer`` as serialised JSON, this node's spend, and its
        name appended to ``visited_nodes``.
    :raises BudgetExceeded: when the per-request ceiling is reached before the call.
    """
    guard = budget_guard(config)
    guard.check_or_raise()
    spent_before = guard.spent_usd_e5

    client = instructor.from_anthropic(
        AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value() or None,
            default_headers={"X-Agent": "synthesis"},
        )
    )

    answer: FinalAnswer = await client.messages.create(
        model=settings.model,
        max_tokens=MAX_TOKENS,
        max_retries=MAX_RETRIES,
        response_model=FinalAnswer,
        messages=[
            {"role": "user", "content": [{"type": "text", "text": _SYSTEM}]},
            {"role": "user", "content": build_user_prompt(state)},
        ],
    )

    return {
        # Serialised rather than held as a model instance. The state is checkpointed to Postgres
        # on every super-step, and a JSON string round-trips through that unchanged; a Pydantic
        # object would depend on the checkpointer's serde reconstructing the exact class, which
        # breaks the first time this model gains a field between a checkpoint and its resume.
        "answer": answer.model_dump_json(),
        "cost_usd_e5": guard.spent_usd_e5 - spent_before,
        "visited_nodes": ["synthesis_agent"],
    }


def make_synthesis_node(
    settings: Settings,
) -> AgentNode:
    """Build the deadline-bounded synthesis node.

    The sentinel is a *valid* :class:`FinalAnswer` rather than an empty dict, and that matters:
    ``answer`` is the one field every consumer reads, so a timeout that left it unset would make
    the SSE bridge emit no ``2:`` event at all and the client hang waiting for one. A refusal
    with zero confidence is a degradation the client can render; a missing answer is not.

    :param settings: Validated configuration supplying the deadline and the project name.
    :returns: The node callable LangGraph registers as ``synthesis_agent``.
    """
    timeout_answer = FinalAnswer(
        text="[deadline exceeded] The answer could not be produced within the time budget.",
        citations=[],
        confidence=0.0,
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
