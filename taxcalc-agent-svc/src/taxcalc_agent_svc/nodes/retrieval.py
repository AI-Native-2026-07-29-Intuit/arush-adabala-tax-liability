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

#: Environment variable naming the reranker the sidecar should use. Set from
#: :attr:`~taxcalc_agent_svc.settings.Settings.reranker` at call time rather than read by the
#: sidecar from an env var nobody validates, so the value is checked once at process boot.
RERANKER_ENV: Final[str] = "TAXCALC_AI_RERANKER"


async def rewrite_query(question: str, client: AsyncAnthropic, settings: Settings) -> str:
    """Rewrite a conversational question into a standalone retrieval query.

    Best-effort by contract. Any failure - a refusal, an empty reply, a transport error - returns
    the original question. A node that failed the whole request because the *optional*
    optimisation ahead of retrieval did not work would be trading a slightly worse answer for no
    answer, which is never the right trade for a step whose only purpose is to improve recall.

    :param question: The user's question.
    :param client: The tagged Anthropic client.
    :param settings: Validated configuration.
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
    query = await rewrite_query(state["question"], client, settings)

    # Set for the sidecar, which reads its reranker choice from the environment. Assigned here
    # rather than at import so the value comes from validated settings and a test can drive the
    # node with a different reranker without reaching into os.environ itself.
    os.environ[RERANKER_ENV] = settings.reranker

    # to_thread because the pipeline is synchronous CPU work - see the module docstring. The
    # import is deferred for the same reason the MCP server defers it: at module scope it loads
    # an 80 MB encoder and raises without a LangSmith key, which would make importing this node
    # for a schema test require both.
    from taxcalc_agent_svc.retrievers import run_pipeline

    result = await asyncio.to_thread(run_pipeline, query, state["tenant_id"], TOP_K)

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
