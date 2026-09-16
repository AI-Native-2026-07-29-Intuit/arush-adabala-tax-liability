# taxcalc-ai/src/taxcalc_ai/rag.py
"""Retrieval surface for the sidecar; ``@traceable`` streams every call to LangSmith.

This is the read half of the corpus that :mod:`taxcalc_ai.corpus` and
:mod:`taxcalc_ai.pgvector_loader` write. W7 D3's RAG lesson, W7 D4's MCP server and W7 D5's
LangGraph orchestrator all consume this one function, so the decisions here are the ones the
rest of the week inherits.

**The cosine operator must match the index's operator class.** The query below uses ``<=>``
because ``doc_chunks_embedding_hnsw`` was built ``USING hnsw (embedding vector_cosine_ops)``.
Writing ``<->`` instead does not fail and does not warn - the planner quietly stops using the
index and scans every row. ``tests/test_pgvector_loader.py`` asserts ``EXPLAIN`` reports an
index scan precisely because that is the only place the mismatch becomes visible.

**Both filters are applied before ranking, and both matter.** ``tenant_id`` is the security
boundary: an HNSW index is an approximate-nearest-neighbour structure over the vector column
alone and cannot enforce it, so without the ``WHERE`` clause a search ranks every tenant's
chunks and returns whichever happened to be nearest. ``model_version`` is the correctness
boundary: vectors from two different models occupy the same 384-dimensional space without
meaning the same thing, so ranking them against each other returns confident nonsense. A corpus
mid-way through a re-embedding contains both, which is exactly when the filter earns its place.

**Credentials come from ``os.environ`` and the check runs at import, not on first call.** A
missing ``LANGSMITH_API_KEY`` discovered on the first retrieval is discovered in production,
under load, on a request that then fails. Discovered at boot it is a container that never passes
its readiness probe - the same defect, found by the deployment instead of by a user. The key is
never a parameter, never a default, and never written to a log.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Final

import numpy as np
import psycopg
import redis
from anthropic import Anthropic
from anthropic.types import TextBlock
from langsmith import traceable
from numpy.typing import NDArray
from pgvector.psycopg import register_vector
from psycopg.rows import TupleRow
from sentence_transformers import SentenceTransformer

from .cache import cache_lookup, cache_store
from .corpus import MODEL_NAME
from .hybrid import (
    DEFAULT_FUSED_K,
    DEFAULT_RETRIEVER_K,
    coverage,
    dense_topk_filtered,
    rrf_fuse,
    sparse_topk_fts,
)
from .rerank import DEFAULT_MMR_K, DEFAULT_RERANK_TOP_K, bge_rerank, mmr_pick

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.rag")

#: The LangSmith credential. Read from the environment only - see the module docstring.
LANGSMITH_API_KEY_ENV: Final[str] = "LANGSMITH_API_KEY"

#: The trace name every retrieval reports under. ``scripts/assert_langsmith_run_visible.py``
#: queries LangSmith for runs carrying exactly this name, so it is a contract with the CI gate
#: rather than a label - renaming it here without renaming it there turns the gate green-by-
#: absence into a failure, which is the right direction for that mistake to break.
RETRIEVER_RUN_NAME: Final[str] = "taxcalc_ai.retrieve_chunks"

#: Default number of chunks returned. Five is small enough to fit a prompt window alongside a
#: question and large enough that ``context_recall`` has something to recall from.
DEFAULT_K: Final[int] = 5


def _require_langsmith_credentials() -> None:
    """Fail the process if the LangSmith key is absent.

    :raises RuntimeError: if :data:`LANGSMITH_API_KEY_ENV` is unset. Raised eagerly at module
        import so a misconfigured deployment dies at boot rather than on its first retrieval.
    """
    if LANGSMITH_API_KEY_ENV not in os.environ:
        # Fail at boot, not on the first call - a cheaper signal, and one the deployment sees.
        raise RuntimeError(
            f"{LANGSMITH_API_KEY_ENV} must be set in env: retrieval is traced, and a retrieval "
            "that silently stops being traced is an observability gap nobody notices"
        )


_require_langsmith_credentials()

#: Loaded once at import. The model is ~80 MB of weights; constructing it per call would make
#: every retrieval pay a cost that has nothing to do with the query. It is also the same model
#: the corpus was embedded with, which is what makes the query vector comparable to the stored
#: ones at all.
_MODEL: Final[SentenceTransformer] = SentenceTransformer(MODEL_NAME)


@traceable(run_type="retriever", name=RETRIEVER_RUN_NAME)
def retrieve_chunks(
    dsn: str,
    question: str,
    k: int = DEFAULT_K,
    tenant_id: str = "tenant-a",
    model_version: str = MODEL_NAME,
) -> list[dict[str, object]]:
    """Embed ``question``, run the cosine ANN search, and return the top ``k`` chunks.

    The ``@traceable`` decorator captures the inputs, the outputs, the latency and the span
    hierarchy into the LangSmith project named by ``LANGSMITH_PROJECT``. It wraps the whole
    function rather than the SQL call alone so the embedding step's latency is inside the span -
    when a retrieval gets slow, "was it the encode or the index" is the first question, and a
    span that starts after the encode cannot answer it.

    :param dsn: libpq connection string for the corpus database.
    :param question: Natural-language query. Embedded with the same model and the same
        ``normalize_embeddings=True`` setting the corpus was written with; a query vector
        normalised differently from the stored ones makes cosine distance measure the wrong thing.
    :param k: How many chunks to return.
    :param tenant_id: The tenant whose corpus to search. A security boundary, not a hint - see
        the module docstring.
    :param model_version: Which model's vectors to rank. Defaults to the model this process
        embeds with, so the query and the candidates always share a geometry.
    :returns: Up to ``k`` dicts carrying ``doc_id``, ``chunk_idx``, ``chunk_text`` and
        ``distance``, nearest first. ``distance`` is cosine distance in ``[0, 2]``, where 0 is
        identical - it is returned rather than hidden so a caller can apply its own relevance
        floor instead of trusting that the ``k``-th result is worth reading.
    """
    # [0] because encode() returns a 2-D array even for a single sentence. .astype(np.float32)
    # for the same reason it is applied on the write path: the stored vectors are float32, and a
    # float64 query vector is either rejected by the adapter or narrowed silently.
    q_vec: NDArray[np.float32] = _MODEL.encode(
        [question],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)[0]

    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            # The cosine operator (<=>) matches the HNSW op-class vector_cosine_ops. The same
            # vector is bound twice - once for the projected distance, once for the ORDER BY -
            # because Postgres cannot order by a select-list alias inside an index scan.
            cur.execute(
                "SELECT doc_id, chunk_idx, chunk_text, embedding <=> %s AS dist "
                "FROM doc_chunks "
                "WHERE tenant_id = %s AND model_version = %s "
                "ORDER BY embedding <=> %s LIMIT %s",
                (q_vec, tenant_id, model_version, q_vec, k),
            )
            results = [
                {
                    "doc_id": r[0],
                    "chunk_idx": int(r[1]),
                    "chunk_text": r[2],
                    "distance": float(r[3]),
                }
                for r in cur.fetchall()
            ]

    _LOG.info(
        "rag.retrieve.completed",
        extra={
            "event": "rag.retrieve.completed",
            "tenant_id": tenant_id,
            "model_version": model_version,
            "k": k,
            "returned": len(results),
        },
    )
    return results


# ---- W7 D3: the RAG 2.0 entry point ---------------------------------------------------------
#
# `retrieve_chunks` above is UNCHANGED and stays. It is the W7 D2 single-cosine baseline, it is
# what `scripts/assert_langsmith_run_visible.py` exercises as the cheapest possible proof that
# tracing works end to end, and it is the "before" column of docs/ragas/w7d3.md. Replacing it
# in place would have deleted the baseline the before-vs-after report is measured against, and
# an A/B rollback would then have had nothing to roll back to.
#
# `retrieve_and_generate` below is the entry point W7 D4's MCP server publishes as a tool and
# W7 D5's LangGraph nodes call. Its signature is pinned TODAY so both of those days are a
# wiring exercise rather than a re-negotiation: keyword-only after `tenant_id` so a new stage
# can be added without breaking a positional caller.

#: Environment flags behind the four pipeline stages. Named here rather than read inline so the
#: eval matrix, the CI workflow and the MCP server all spell them the same way.
RAG_USE_HYBRID_ENV: Final[str] = "RAG_USE_HYBRID"
RAG_USE_MMR_ENV: Final[str] = "RAG_USE_MMR"
RAG_USE_RERANK_ENV: Final[str] = "RAG_USE_RERANK"
RAG_USE_FILTER_ENV: Final[str] = "RAG_USE_FILTER"

#: Values read as "off". Anything else - including the empty string - is on, because the flags
#: default to on and an unset variable must not silently disable a stage.
_FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})

#: The generation model. Pinned here rather than defaulted inside the SDK call so the model in
#: use is a reviewable line in this file.
GENERATION_MODEL: Final[str] = "claude-sonnet-4-5"

#: Token ceiling on the generated answer. Small deliberately: the answer is a grounded summary
#: of six chunks, and a model given room to write an essay writes one that drifts off the
#: context - which shows up as a faithfulness regression, not as a length complaint.
GENERATION_MAX_TOKENS: Final[int] = 512


def flag_from_env(name: str, default: bool = True) -> bool:
    """Read a pipeline stage flag from the environment.

    Defaults to ON for every stage: the four upgrades are the intended configuration, and a
    misspelled variable should leave the pipeline working rather than quietly reverting it to
    the W7 D2 baseline. Turning a stage off is therefore an explicit act.

    :param name: One of the ``RAG_USE_*`` constants.
    :param default: Value when the variable is unset.
    :returns: The flag.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def _build_prompt(query_text: str, context: Sequence[tuple[str, str, float]]) -> str:
    """Render the numbered-context prompt the generator answers from.

    Numbered rather than concatenated, and the instruction is "using only": both exist to make
    ``faithfulness`` measurable. A model given unlabelled context cannot cite, and a grader
    cannot tell a grounded claim from a recalled one.

    :param query_text: The question.
    :param context: The final reranked chunks as ``(chunk_id, chunk_text, score)``.
    :returns: The prompt string.
    """
    parts = [f"[{i}] {text}" for i, (_, text, _) in enumerate(context, start=1)]
    return (
        "Answer the question using only the numbered context below.\n\n"
        + "\n\n".join(parts)
        + f"\n\nQuestion: {query_text}"
    )


@traceable(run_type="chain", name="taxcalc_ai.retrieve_and_generate")
def retrieve_and_generate(
    query_text: str,
    tenant_id: str,
    *,
    anthropic: Anthropic,
    conn: psycopg.Connection[TupleRow],
    r: redis.Redis,
    metadata_filter: Mapping[str, object] | None = None,
    model_name: str = GENERATION_MODEL,
    use_hybrid: bool | None = None,
    use_mmr: bool | None = None,
    use_rerank: bool | None = None,
    use_filter: bool | None = None,
) -> dict[str, object]:
    """The RAG 2.0 pipeline: cache, hybrid retrieve, fuse, diversify, rerank, generate.

    Five stages plus the semantic-cache check, in this order:

    1. :func:`taxcalc_ai.cache.cache_lookup` - a hit returns immediately, so a repeated question
       costs one Redis GET instead of the whole pipeline.
    2. :func:`taxcalc_ai.hybrid.dense_topk_filtered` and
       :func:`taxcalc_ai.hybrid.sparse_topk_fts`, both tenant-pre-filtered.
    3. :func:`taxcalc_ai.hybrid.rrf_fuse` - rank fusion, never score blending.
    4. :func:`taxcalc_ai.rerank.mmr_pick` - 60 candidates down to 20, redundancy removed.
    5. :func:`taxcalc_ai.rerank.bge_rerank` - 20 down to 6 under a 300 ms soft deadline.

    Clients are injected rather than constructed here, which is what makes the function
    testable and what makes a caller own its own connection pooling. The three of them
    (``anthropic``, ``conn``, ``r``) are the only I/O this function performs beyond the
    embedding, which is local.

    :param query_text: The question.
    :param tenant_id: The requesting tenant. A security boundary at three layers: the SQL
        pre-filter, the cache key, and the citation check inside the cache lookup.
    :param anthropic: A constructed Anthropic client.
    :param conn: An open psycopg connection. ``register_vector`` is applied here, so a caller
        need not remember to.
    :param r: A Redis client for the semantic cache.
    :param metadata_filter: JSONB containment filter, applied only when ``use_filter``.
    :param model_name: Generation model.
    :param use_hybrid: Run the sparse retriever and fuse. ``None`` reads
        :data:`RAG_USE_HYBRID_ENV`. With it off, the dense list alone is carried forward - the
        W7 D2 behaviour.
    :param use_mmr: Diversify with MMR. ``None`` reads :data:`RAG_USE_MMR_ENV`.
    :param use_rerank: Cross-encode. ``None`` reads :data:`RAG_USE_RERANK_ENV`.
    :param use_filter: Apply ``metadata_filter``. ``None`` reads :data:`RAG_USE_FILTER_ENV`.
    :returns: ``text``, ``citations`` (each carrying ``chunk_id``, ``chunk_text``, ``score`` and
        ``tenant_id``), ``rerank_timed_out``, and the ``coverage`` diagnostic. The
        ``tenant_id`` on each citation is not decoration - it is what the cache's
        defence-in-depth check reads on every subsequent hit.
    """
    hybrid_on = flag_from_env(RAG_USE_HYBRID_ENV) if use_hybrid is None else use_hybrid
    mmr_on = flag_from_env(RAG_USE_MMR_ENV) if use_mmr is None else use_mmr
    rerank_on = flag_from_env(RAG_USE_RERANK_ENV) if use_rerank is None else use_rerank
    filter_on = flag_from_env(RAG_USE_FILTER_ENV) if use_filter is None else use_filter

    q_vec: NDArray[np.float32] = _MODEL.encode(
        [query_text],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)[0]

    cached = cache_lookup(r, q_vec, tenant_id)
    if cached is not None:
        return cached

    register_vector(conn)
    dense = dense_topk_filtered(
        conn,
        q_vec,
        tenant_id,
        metadata_filter=metadata_filter if filter_on else None,
        k=DEFAULT_RETRIEVER_K,
    )
    sparse = (
        sparse_topk_fts(conn, query_text, tenant_id, k=DEFAULT_RETRIEVER_K) if hybrid_on else []
    )
    # With hybrid off, the dense list is carried forward untouched rather than fused with an
    # empty list. Fusing with [] would be arithmetically harmless but would replace the cosine
    # distances with RRF scores, making the "before" column of the eval matrix report a metric
    # the W7 D2 baseline never produced.
    fused = (
        rrf_fuse(dense, sparse, top_k=DEFAULT_FUSED_K)
        if hybrid_on
        else list(dense[:DEFAULT_FUSED_K])
    )
    diversified = (
        mmr_pick(q_vec, fused, _MODEL, k=DEFAULT_MMR_K) if mmr_on else fused[:DEFAULT_MMR_K]
    )
    if rerank_on:
        reranked, timed_out = bge_rerank(query_text, diversified, top_k=DEFAULT_RERANK_TOP_K)
    else:
        reranked, timed_out = diversified[:DEFAULT_RERANK_TOP_K], False

    message = anthropic.messages.create(
        model=model_name,
        max_tokens=GENERATION_MAX_TOKENS,
        messages=[{"role": "user", "content": _build_prompt(query_text, reranked)}],
    )
    # The SDK's content blocks are a union (text, tool_use, thinking); only a TextBlock has
    # `.text`. Narrowing rather than indexing blindly keeps --strict satisfied and, more to the
    # point, means a future response shape returns an empty answer instead of raising inside
    # the generation step.
    first = message.content[0] if message.content else None
    text = first.text if isinstance(first, TextBlock) else ""

    answer: dict[str, object] = {
        "text": text,
        "citations": [
            {
                "chunk_id": chunk_id,
                "chunk_text": chunk_text,
                "score": score,
                "tenant_id": tenant_id,
            }
            for chunk_id, chunk_text, score in reranked
        ],
        "rerank_timed_out": timed_out,
        "coverage": coverage(dense, sparse),
    }
    cache_store(r, q_vec, tenant_id, answer)

    _LOG.info(
        "rag.generate.completed",
        extra={
            "event": "rag.generate.completed",
            "tenant_id": tenant_id,
            "dense": len(dense),
            "sparse": len(sparse),
            "fused": len(fused),
            "context": len(reranked),
            "rerank_timed_out": timed_out,
        },
    )
    return answer
