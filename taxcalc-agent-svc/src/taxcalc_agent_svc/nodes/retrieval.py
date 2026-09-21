# taxcalc-agent-svc/src/taxcalc_agent_svc/nodes/retrieval.py
"""Retrieval agent: a Claude query rewrite in front of the W7 D3 hybrid+rerank pipeline.

**This node wraps; it does not reimplement.** The retrieval itself is
:func:`taxcalc_ai.rag.retrieve_and_generate` - BM25 plus dense vectors fused with RRF, MMR
diversification, and a bge cross-encoder rerank - shipped, tested and RAGAS-gated back on W7 D3.
The node's own job is two things the pipeline does not do: rewrite the user's question into a
retrieval query, and pre-shape the result into the three fields the rest of the graph needs.

**Why a query rewrite at all.** A conversational question ("what about refunds on that one?")
carries its meaning in the conversation, not in its own words, and a retriever matching on those
words retrieves nothing useful. One cheap Claude call turns it into a standalone query. The
rewrite is best-effort: a failed or empty rewrite falls back to the original question rather than
failing the node, because a slightly worse query beats no retrieval at all.

**The reranker is chosen here and acted on three modules away.** ``RERANKER`` selects the local
bge cross-encoder or Cohere ``rerank-3.5``; this node exports it, and
:func:`taxcalc_ai.rerank.rerank_candidates` dispatches on it. Both halves matter, and the earlier
version of this node had only the first: it exported a differently-named variable nothing read, so
selecting Cohere changed the environment and not the retrieval.

**The pipeline is synchronous, so it runs on a worker thread.** ``asyncio.to_thread`` keeps an
80 MB cross-encoder forward pass off the event loop. Calling it inline would block every other
in-flight request on this pod for the duration of one rerank - which, under the SSE transport, is
every other open stream stalling in lockstep.

**Only three fields per document cross into the state, and the omissions are deliberate.** The
pipeline's chunks carry the full chunk text and its embedding metadata. Both are large, neither
is read by synthesis - which cites by ``doc_id`` and reads the pipeline's generated text - and
every byte in the state is a byte the ``PostgresSaver`` writes to a checkpoint row on *every*
super-step. Carrying embeddings through the state would make each checkpoint hundreds of
kilobytes of numbers nothing reads. This is the W7 D4 Section 9 discipline applied one layer up.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Final, cast

from anthropic import AsyncAnthropic
from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from taxcalc_agent_svc.budgets import BudgetGuard
from taxcalc_agent_svc.deps import AgentNode, budget_guard
from taxcalc_agent_svc.nodes._deadline import deadline
from taxcalc_agent_svc.settings import Settings
from taxcalc_agent_svc.state import AgentState

#: Documents kept after the rerank. Eight rather than the pipeline's own six because synthesis
#: refuses on thin context, and the two extra candidates cost nothing here - they are already
#: ranked - while giving the refusal path something to work with on a marginal question.
TOP_K: Final[int] = 8

#: Ceiling on the rewrite completion. A standalone search query is a dozen tokens; a budget large
#: enough for prose would invite the model to write some.
REWRITE_MAX_TOKENS: Final[int] = 128

_REWRITE_SYSTEM: Final[str] = (
    "Rewrite the user's question as a single standalone search query for a document retrieval "
    "system. Resolve pronouns and references using the question alone. Reply with the query "
    "text and nothing else - no preamble, no quotes, no explanation."
)

#: Environment variable naming the reranker the sidecar should use. The literal name
#: :data:`taxcalc_ai.rerank.RERANKER_ENV` reads - unprefixed, and duplicated here rather than
#: imported because importing ``taxcalc_ai.rerank`` pulls ``sentence_transformers`` in at module
#: scope, which is the whole reason the pipeline import below is deferred. The duplication is
#: gated: ``tests/test_nodes.py`` asserts this constant equals the sidecar's, so the two names
#: cannot drift into the dead write this replaced.
RERANKER_ENV: Final[str] = "RERANKER"


async def rewrite_query(
    question: str, client: AsyncAnthropic, settings: Settings, guard: BudgetGuard
) -> str:
    """Rewrite a conversational question into a standalone retrieval query.

    Best-effort by contract. Any failure - a refusal, an empty reply, a transport error - returns
    the original question. A node that failed the whole request because the *optional*
    optimisation ahead of retrieval did not work would be trading a slightly worse answer for no
    answer, which is never the right trade for a step whose only purpose is to improve recall.

    **The guard is passed in so the call can be BILLED, not merely permitted.** An earlier version
    took only the client, so this call was checked against the ceiling and then never added to it:
    retrieval spend stayed at zero for the life of the request, the node reported
    ``cost_usd_e5: 0``, and on a docs-only question - retrieval into synthesis, no tool leg - the
    tally never left zero and the dollar ceiling could not fire at all. A budget that is checked
    but not fed is a budget that only ever reads zero.

    Best-effort applies to the *rewrite*, not to the accounting. The call is recorded before the
    reply is inspected, so a response that arrives and is then discarded as unusable is still paid
    for in the tally - because it was paid for at Anthropic.

    :param question: The user's question.
    :param client: The tagged Anthropic client.
    :param settings: Validated configuration.
    :param guard: The request's cost ceiling, updated with this call's usage.
    :returns: The rewritten query, or ``question`` unchanged if the rewrite produced nothing
        usable.
    """
    try:
        resp = await client.messages.create(
            model=settings.model,
            max_tokens=REWRITE_MAX_TOKENS,
            system=_REWRITE_SYSTEM,
            messages=[{"role": "user", "content": question}],
        )
    except Exception:  # the rewrite is optional by contract - see the docstring
        return question
    # Before the reply is parsed, and outside the try: a call that reached Anthropic costs money
    # whether or not its text turns out to be usable, and `record_call` cannot raise.
    guard.record_call(resp)
    # getattr rather than `b.text`: the SDK's content union has a dozen block types and only
    # TextBlock carries `.text`. Filtering on `.type` narrows it at runtime but not for the
    # type checker, and a cast would assert something the SDK does not guarantee.
    parts = [
        str(getattr(b, "text", ""))
        for b in resp.content
        if getattr(b, "type", None) == "text"
    ]
    rewritten = " ".join(parts).strip()
    return rewritten or question


def _record_pipeline_usage(result: dict[str, Any], guard: BudgetGuard) -> None:
    """Bill the pipeline's generation call to this request's guard.

    The pipeline constructs its own Anthropic client and returns a dictionary, so its completion
    never passes through this process as a response object - which is exactly why it went
    unbilled. :func:`taxcalc_ai.rag.retrieve_and_generate` now reports ``usage`` for the call it
    made, and this reads it.

    **A missing ``usage`` means zero, not unknown.** The pipeline omits the key on a semantic
    cache hit, where no completion happened and nothing should be charged. Treating the absence
    as an error would fail the cheapest possible request; treating it as an unknown to estimate
    would invent spend that did not occur.

    :param result: The pipeline's raw result.
    :param guard: The request's cost ceiling.
    """
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return
    guard.record_usage(
        int(usage.get("input_tokens", 0) or 0),
        int(usage.get("output_tokens", 0) or 0),
    )


def shape_docs(result: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    """Reduce the pipeline's citations to the three fields the graph carries.

    Tolerant of shape: a pipeline that renames a key degrades one field rather than failing the
    whole retrieval, because a citation with an imperfect score is far better than an answer lost
    to a parse error over a display value.

    :param result: The pipeline's raw result.
    :param top_k: How many documents to keep.
    :returns: Documents as ``chunk_id`` / ``doc_id`` / ``score``.
    """
    raw = result.get("citations", [])
    if not isinstance(raw, list):
        return []
    docs: list[dict[str, Any]] = []
    for item in raw[:top_k]:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id", ""))
        docs.append(
            {
                "chunk_id": chunk_id,
                "doc_id": doc_id_of(chunk_id),
                "score": float(item.get("score", 0.0) or 0.0),
            }
        )
    return docs


def doc_id_of(chunk_id: str) -> str:
    """Recover the document id from a chunk id.

    The corpus builds chunk ids as ``chunk-{doc_id}-p{chunk_idx}`` (see
    :mod:`taxcalc_ai.chunker`), so the document id is what remains after stripping the prefix and
    the page suffix. Returns the id unchanged when it does not match that shape, for the same
    tolerance reason as :func:`shape_docs`.

    :param chunk_id: The chunk identifier.
    :returns: The document id, or ``chunk_id`` unchanged.
    """
    if not chunk_id.startswith("chunk-") or "-p" not in chunk_id:
        return chunk_id
    return chunk_id.removeprefix("chunk-").rsplit("-p", 1)[0]


async def _retrieval(
    state: AgentState, config: RunnableConfig | None, settings: Settings
) -> dict[str, Any]:
    """Rewrite, retrieve, and pre-shape.

    Undecorated on purpose - :func:`make_retrieval_node` applies both decorators, in the order
    that matters. See :mod:`taxcalc_agent_svc.nodes._deadline`.

    :param state: The graph state.
    :param config: The LangGraph config carrying the request's budget guard.
    :param settings: Validated configuration.
    :returns: A partial state carrying ``docs``, this node's spend, and its name appended to
        ``visited_nodes``.
    """
    guard = budget_guard(config)
    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value() or None,
        default_headers={"X-Agent": "retrieval"},
    )

    spent_before = guard.spent_usd_e5
    guard.check_or_raise()
    query = await rewrite_query(state["question"], client, settings, guard)

    # Set for the sidecar, which reads its reranker choice from this variable - see RERANKER_ENV.
    # Assigned here rather than at import so the value comes from settings that were validated at
    # boot: by the time the sidecar reads it, "bge" or "cohere" is the only thing it can say, and
    # a Cohere selection has already been checked to have a credential behind it.
    os.environ[RERANKER_ENV] = settings.reranker

    # to_thread because the pipeline is synchronous CPU work - see the module docstring. The
    # import is deferred for the same reason the MCP server defers it: at module scope it loads
    # an 80 MB encoder and raises without a LangSmith key, which would make importing this node
    # for a schema test require both.
    from taxcalc_agent_svc.retrievers import run_pipeline

    # Checked again, because the pipeline GENERATES. Its answer text is a second paid Claude
    # call - the retrieval agent makes two - and a ceiling verified once before the cheap rewrite
    # is a ceiling that never guards the expensive half.
    guard.check_or_raise()
    result = await asyncio.to_thread(run_pipeline, query, state["tenant_id"], TOP_K)
    _record_pipeline_usage(result, guard)

    return {
        "docs": shape_docs(result, TOP_K),
        "cost_usd_e5": guard.spent_usd_e5 - spent_before,
        "visited_nodes": ["retrieval_agent"],
    }


def make_retrieval_node(
    settings: Settings,
) -> AgentNode:
    """Build the deadline-bounded retrieval node.

    :param settings: Validated configuration supplying the deadline and the project name.
    :returns: The node callable LangGraph registers as ``retrieval_agent``.
    """

    # @traceable OUTERMOST, @deadline applied FIRST. See make_api_node and _deadline.py.
    @traceable(name="retrieval_agent", project_name=settings.langsmith_project)
    @deadline(seconds=settings.deadline_retrieval_s, sentinel={"docs": []})
    async def retrieval_node(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, Any]:
        """Retrieve from the tenant's corpus under this node's deadline.

        :param state: The graph state.
        :param config: The LangGraph config carrying the request's budget guard.
        :returns: A partial state carrying ``docs``.
        """
        return await _retrieval(state, config, settings)

    # cast, with a reason. `@traceable` returns a wrapper declared as `(*args, **kwargs)`, so the
    # named-parameter shape `AgentNode` (and LangGraph's own `_NodeWithConfig`) requires is erased
    # at the type level even though `functools.wraps` preserves it at runtime. The cast restores
    # the fact the decorator loses; the alternative is annotating every `add_node` call `Any`,
    # which turns off checking for the one argument most worth checking.
    return cast(AgentNode, retrieval_node)
