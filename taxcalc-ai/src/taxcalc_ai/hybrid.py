# taxcalc-ai/src/taxcalc_ai/hybrid.py
"""Hybrid retrieval: dense (pgvector HNSW) + sparse (Postgres FTS), fused by RRF.

W7 D2's retrieval was a single cosine ANN query. It is good at paraphrase and bad at exactness:
a query for a literal bracket code, a form number or a statutory phrase is answered by whatever
chunk is *semantically* nearest, which is often a chunk discussing the same topic without
containing the term. Lexical search is the mirror image - exact on terms, blind to synonyms.
Hybrid retrieval runs both and fuses the results.

**Fusion is on RANK, not on score, and this is the single most important decision in the file.**
The obvious implementation - ``0.5 * cosine + 0.5 * bm25`` - mixes two incomparable scales.
Cosine distance is bounded in ``[0, 2]`` and *smaller* is better; ``ts_rank_cd`` is unbounded,
positive, corpus- and query-length-dependent, and *larger* is better. Any fixed weighting of the
two is a weighting of whichever scale happens to be larger on this query, so the blend's
behaviour changes with the query and with the corpus, invisibly. Rescaling each retriever's
output per request - mapping the window's worst and best onto 0 and 1 - does not fix it either:
it makes every value relative to the best result *in that window*, so the same document scores
differently depending on what else came back, and a window containing one strong hit compresses
everything else to near zero. There is deliberately no such rescaling helper in this module;
its absence is asserted by a grep in the W7 D3 gate, because the helper is the thing somebody
reaches for when RRF's output looks unfamiliar.

Reciprocal Rank Fusion discards the scores entirely and sums ``weight / (k_const + rank)`` over
each retriever's ordering. Rank is the one quantity both retrievers produce on the same scale -
first is first - so the fusion is stable across corpora and query types with no tuning. A
document that appears in *both* lists accumulates two contributions and rises above one that
only appears in either, which is precisely the signal hybrid retrieval exists to capture.

``k_const = 60`` is the constant from the original RRF paper and the value to leave alone. It
damps the difference between adjacent high ranks (1/61 vs 1/62 is a 1.6% gap) so a near-tie at
the top of one list does not swing the fused order; lowering it makes rank 1 dominate, raising
it flattens everything toward a tie. **Tune the per-retriever weights, not ``k``** - the weights
say "trust dense more than sparse on this corpus", which is a claim about the data; ``k`` says
"trust rank 1 more than rank 2", which is a claim about arithmetic.

**Both retrievers pre-filter on ``tenant_id`` before ranking.** For the dense path that filter
also selects the per-tenant partial HNSW index from ``sql/V002``; see that file for why a
partial index beats one global index plus a ``WHERE`` clause. The filter is a security boundary
either way - an ANN index cannot enforce it, so the SQL must.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Final

import numpy as np
import psycopg
from langsmith import traceable
from numpy.typing import NDArray

# TupleRow, not tuple[object, ...]: psycopg's own row type for an unconfigured cursor is
# tuple[Any, ...], and declaring the narrower object element type makes every float()/str()
# call on a fetched column a type error even though the runtime value is correct.
from psycopg.rows import TupleRow

from .corpus import MODEL_NAME

_LOG: Final[logging.Logger] = logging.getLogger("taxcalc_ai.hybrid")

#: The RRF damping constant. 60 from the original paper; see the module docstring for why this
#: is the knob NOT to tune.
K_CONST: Final[int] = 60

#: Candidates each retriever returns before fusion. Deliberately larger than anything returned
#: to a caller: fusion can only promote a document that at least one retriever surfaced, so a
#: narrow per-retriever window throws away the recall that hybrid retrieval was adopted for.
DEFAULT_RETRIEVER_K: Final[int] = 50

#: Size of the fused candidate list handed to MMR. 60 rather than 50 because the union of two
#: 50-row lists is between 50 and 100 rows, and cutting it back to either input's width would
#: discard exactly the documents that only one retriever found.
DEFAULT_FUSED_K: Final[int] = 60

#: The synthetic chunk id, reconstructed in SQL so it matches
#: :func:`taxcalc_ai.chunker.chunk_id_for` byte for byte. Rebuilt rather than selected from
#: ``chunk_metadata`` because a legacy V001 row has empty metadata and would fuse under a NULL
#: id - collapsing every such row into one candidate. Two places build this string and a test
#: pins that they agree.
_CHUNK_ID_EXPR: Final[str] = "'chunk-' || doc_id || '-p' || chunk_idx"


@traceable(run_type="retriever", name="taxcalc_ai.dense_topk_filtered")
def dense_topk_filtered(
    conn: psycopg.Connection[TupleRow],
    query_vec: NDArray[np.float32],
    tenant_id: str,
    metadata_filter: Mapping[str, object] | None = None,
    k: int = DEFAULT_RETRIEVER_K,
    model_version: str = MODEL_NAME,
) -> list[tuple[str, str, float]]:
    """Cosine ANN search, pre-filtered by tenant, model version and optional JSONB metadata.

    The connection is passed in rather than opened here, and that is not merely tidiness: the
    caller registers the pgvector adapter on it once (see :mod:`taxcalc_ai.rag`), and both
    retrievers plus the tenant-isolation assertion in the tests need to share one session.

    ``<=>`` is the cosine operator, matching the ``vector_cosine_ops`` operator class every HNSW
    index in ``sql/V001`` and ``sql/V002`` was built with. Writing ``<->`` (L2) instead does not
    fail and does not warn - the planner simply stops using the index and scans the table.

    :param conn: An open connection with ``register_vector`` already applied.
    :param query_vec: The query embedding, ``float32`` and unit length.
    :param tenant_id: The tenant whose corpus to search. A security boundary: the ``WHERE``
        clause is the only thing enforcing it, because the HNSW index cannot.
    :param metadata_filter: Optional JSONB containment filter, applied with ``@>`` against
        ``chunk_metadata`` so it can use the ``doc_chunks_metadata_gin`` index. ``None`` applies
        no metadata predicate at all, which is different from ``{}`` - the empty object is
        contained by every row and would be a no-op filter that still costs a clause.
    :param k: Candidates to return.
    :param model_version: Which model's vectors to rank. Vectors from two models share the
        384-dimensional space without sharing a meaning, so mixing them ranks confident nonsense.
    :returns: Up to ``k`` ``(chunk_id, chunk_text, distance)`` triples, nearest first.
        ``distance`` is cosine distance - smaller is better - and is returned for diagnostics
        only. Nothing downstream fuses on it; see the module docstring.
    """
    sql = (
        f"SELECT {_CHUNK_ID_EXPR} AS chunk_key, chunk_text, embedding <=> %s AS dist "
        "FROM doc_chunks "
        "WHERE tenant_id = %s AND model_version = %s "
    )
    params: list[object] = [query_vec, tenant_id, model_version]
    if metadata_filter is not None:
        sql += "AND chunk_metadata @> %s::jsonb "
        params.append(json.dumps(dict(metadata_filter)))
    # The vector is bound a second time for the ORDER BY: Postgres cannot order by a select-list
    # alias inside an index scan, so the expression has to be repeated verbatim.
    sql += "ORDER BY embedding <=> %s LIMIT %s"
    params.extend([query_vec, k])

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [(str(r[0]), str(r[1]), float(r[2])) for r in cur.fetchall()]


@traceable(run_type="retriever", name="taxcalc_ai.sparse_topk_fts")
def sparse_topk_fts(
    conn: psycopg.Connection[TupleRow],
    query_text: str,
    tenant_id: str,
    k: int = DEFAULT_RETRIEVER_K,
) -> list[tuple[str, str, float]]:
    """Lexical search over the generated ``chunk_tsv`` column, pre-filtered by tenant.

    Postgres FTS stands in for OpenSearch/BM25 here. It is not the same ranking function -
    ``ts_rank_cd`` is cover-density, not BM25 - but it is the same *kind* of signal (exact term
    match, term proximity, document length) and it needs no second datastore to keep in sync
    with the corpus. The tuple shape is identical to the dense path's so the fusion never has to
    know which retriever produced a row.

    ``websearch_to_tsquery`` rather than ``plainto_tsquery``: it accepts the quoting and
    exclusion syntax users already type (``"bracket code" -amt``) and, critically, it does not
    raise on malformed input. ``to_tsquery`` would turn a stray ``&`` in a user's question into
    a syntax error at query time.

    :param conn: An open connection.
    :param query_text: The raw question. Parsed by Postgres, not pre-tokenised here.
    :param tenant_id: The tenant whose corpus to search.
    :param k: Candidates to return.
    :returns: Up to ``k`` ``(chunk_id, chunk_text, score)`` triples, best first. ``score`` is
        ``ts_rank_cd`` - larger is better - and, like the dense distance, is a diagnostic only.
    """
    sql = (
        f"SELECT {_CHUNK_ID_EXPR} AS chunk_key, chunk_text, ts_rank_cd(chunk_tsv, q) AS score "
        "FROM doc_chunks, websearch_to_tsquery('english', %s) AS q "
        "WHERE tenant_id = %s AND chunk_tsv @@ q "
        "ORDER BY score DESC LIMIT %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (query_text, tenant_id, k))
        return [(str(r[0]), str(r[1]), float(r[2])) for r in cur.fetchall()]


@traceable(run_type="chain", name="taxcalc_ai.rrf_fuse")
def rrf_fuse(
    dense: Sequence[tuple[str, str, float]],
    sparse: Sequence[tuple[str, str, float]],
    k_const: int = K_CONST,
    w_dense: float = 1.0,
    w_sparse: float = 1.0,
    top_k: int = DEFAULT_FUSED_K,
) -> list[tuple[str, str, float]]:
    """Fuse two ranked lists by Reciprocal Rank Fusion.

    Each list contributes ``weight / (k_const + rank)`` per document, with ``rank`` 1-based.
    Raw scores are ignored entirely - see the module docstring for why blending them is the
    trap this function exists to avoid. Weights default to symmetric (1.0/1.0), the baseline;
    they are the knob to tune when an eval set says one retriever is stronger on this corpus.

    :param dense: The dense retriever's output, best first.
    :param sparse: The sparse retriever's output, best first.
    :param k_const: RRF damping constant. Leave at :data:`K_CONST`.
    :param w_dense: Weight on the dense list's contributions.
    :param w_sparse: Weight on the sparse list's contributions.
    :param top_k: How many fused candidates to return.
    :returns: Up to ``top_k`` ``(chunk_id, chunk_text, rrf_score)`` triples, best first.
        ``rrf_score`` is the summed reciprocal-rank contribution - a fusion score, on no
        retriever's scale, and not comparable to a cosine distance or a ``ts_rank_cd``.
    """
    scores: dict[str, float] = defaultdict(float)
    texts: dict[str, str] = {}

    for rank, (chunk_id, text, _score) in enumerate(dense, start=1):
        scores[chunk_id] += w_dense / (k_const + rank)
        texts[chunk_id] = text
    for rank, (chunk_id, text, _score) in enumerate(sparse, start=1):
        scores[chunk_id] += w_sparse / (k_const + rank)
        # setdefault, not assignment: the two retrievers return the same text for the same
        # chunk_id, so this is defensive rather than load-bearing - but if they ever disagree
        # (a replica lagging mid-ingest), the dense text is the one the vector describes.
        texts.setdefault(chunk_id, text)

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    fused = [(chunk_id, texts[chunk_id], score) for chunk_id, score in ordered[:top_k]]

    _LOG.info(
        "hybrid.rrf.fused",
        extra={
            "event": "hybrid.rrf.fused",
            "dense": len(dense),
            "sparse": len(sparse),
            "fused": len(fused),
            "k_const": k_const,
        },
    )
    return fused


def coverage(
    dense: Sequence[tuple[str, str, float]],
    sparse: Sequence[tuple[str, str, float]],
) -> dict[str, float]:
    """Diagnostic: how much the two retrievers agreed on this request.

    Logged on every request rather than sampled, because the Jaccard is the number that tells an
    operator whether hybrid retrieval is still *doing* anything. Near 1.0 means the two
    retrievers are returning the same documents, so the sparse path is paying for itself in
    latency and returning no new recall - the honest response is to turn it off, not to keep it
    for the diagram. Near 0.0 on every query means they disagree completely, which usually means
    one of them is broken (an empty ``chunk_tsv``, a model-version filter excluding the whole
    corpus) rather than that both are contributing.

    It is a *diagnostic*, not a gate: nothing branches on it, because a legitimate query
    distribution contains both kinds of query.

    :param dense: The dense retriever's output.
    :param sparse: The sparse retriever's output.
    :returns: ``dense_only``, ``sparse_only``, ``both`` as counts, and ``jaccard`` as
        ``|both| / |union|``. Floats throughout so the mapping is a single type - these go
        straight into a log line and a LangSmith span attribute.
    """
    dense_ids = {chunk_id for chunk_id, _, _ in dense}
    sparse_ids = {chunk_id for chunk_id, _, _ in sparse}
    both = dense_ids & sparse_ids
    union = dense_ids | sparse_ids
    return {
        "dense_only": float(len(dense_ids - sparse_ids)),
        "sparse_only": float(len(sparse_ids - dense_ids)),
        "both": float(len(both)),
        # `or 1` guards the empty-union case: two retrievers that both returned nothing agree
        # perfectly in a sense nobody means, and 0/0 is not a number a log line can carry.
        "jaccard": float(len(both)) / float(len(union) or 1),
    }
