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
from typing import Final

import numpy as np
import psycopg
from langsmith import traceable
from numpy.typing import NDArray
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from .corpus import MODEL_NAME

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
