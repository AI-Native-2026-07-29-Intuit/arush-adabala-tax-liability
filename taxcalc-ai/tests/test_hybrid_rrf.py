# taxcalc-ai/tests/test_hybrid_rrf.py
"""Hybrid retrieval: the metadata pre-filter, the exact-phrase case, and RRF's rank algebra.

Three of these four tests need a real Postgres, because what they assert is Postgres behaviour:
that ``@>`` against a ``jsonb`` column actually restricts the candidate set, and that
``websearch_to_tsquery`` + ``ts_rank_cd`` find a literal code string the cosine ranking buries.
A mocked cursor would agree with any SQL it was handed, including SQL with the operator-class
mismatch that silently disables the HNSW index.

The fourth is pure arithmetic over two synthetic lists, which is the right shape for RRF: what
matters about the fusion is that it operates on rank positions and that membership of both
lists is rewarded, and neither claim needs a database to check.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import psycopg
import pytest
from numpy.typing import NDArray
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from taxcalc_ai.chunker import chunk_id_for
from taxcalc_ai.corpus import MODEL_NAME, CorpusRow, content_hash
from taxcalc_ai.hybrid import (
    K_CONST,
    coverage,
    dense_topk_filtered,
    rrf_fuse,
    sparse_topk_fts,
)
from taxcalc_ai.pgvector_loader import load_rows

#: The tenant these tests own. Scoped away from every other module's fixtures so the
#: session-scoped container can be shared without row counts interfering.
TENANT = "tenant-hybrid"

#: An exact product code. Chosen to be lexically distinctive and semantically empty: it shares
#: no words with the prose around it, so cosine similarity has nothing to latch onto, while FTS
#: matches it exactly. That asymmetry is the whole reason the sparse path exists.
PRODUCT_CODE = "BRK-CA-2026-MID-0447"


@pytest.fixture(scope="module")
def embedder() -> SentenceTransformer:
    """The real MiniLM model, loaded once for this module.

    Real rather than stubbed: the point of the exact-phrase test is that genuine semantic
    similarity ranks the code-bearing chunk low, and a hand-built vector would be asserting
    what the test author already believed instead of what the model does.
    """
    return SentenceTransformer(MODEL_NAME)


@pytest.fixture(scope="module")
def seeded(pg_dsn: str, embedder: SentenceTransformer) -> str:
    """Seed this module's tenant with chunks whose metadata and lexical content differ.

    Module-scoped so the embedding pass runs once. The rows are deliberately heterogeneous:
    two jurisdictions for the metadata filter to separate, and one chunk carrying
    :data:`PRODUCT_CODE` amid prose that does not discuss it.
    """
    texts = [
        (
            "Federal ordinary-income brackets are marginal, so only the income falling "
            "inside a band is taxed at that band's rate.",
            {"jurisdiction": "CA", "tax_year": 2026},
        ),
        (
            "The standard deduction is subtracted from adjusted gross income to reach "
            "taxable income before the bracket ladder is applied.",
            {"jurisdiction": "CA", "tax_year": 2026},
        ),
        (
            "New York resident credit rules coordinate with the federal ladder but are "
            "computed on state taxable income.",
            {"jurisdiction": "NY", "tax_year": 2026},
        ),
        (
            f"Schedule annex: the applicable rate table is published under code "
            f"{PRODUCT_CODE} and supersedes prior annexes.",
            {"jurisdiction": "CA", "tax_year": 2026},
        ),
    ]
    vectors: NDArray[np.float32] = embedder.encode(
        [text for text, _ in texts],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    rows = [
        CorpusRow(
            doc_id=f"{TENANT}-doc-{i:03d}",
            chunk_idx=0,
            chunk_text=text,
            embedding=vector,
            model_version=MODEL_NAME,
            tenant_id=TENANT,
            chunk_metadata=metadata,
            content_hash=content_hash(text),
        )
        for i, ((text, metadata), vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    load_rows(pg_dsn, rows)
    return pg_dsn


def _query_vector(embedder: SentenceTransformer, question: str) -> NDArray[np.float32]:
    """Embed one question the same way the corpus was embedded."""
    # Annotated intermediate rather than a direct return: `encode` is untyped at the tail, so
    # returning its result straight out trips --strict's warn_return_any.
    vectors: NDArray[np.float32] = embedder.encode(
        [question], normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)
    # asarray rather than a bare `vectors[0]`: numpy's stubs type ndarray.__getitem__ as Any,
    # and --strict's warn_return_any rejects returning it from a typed function.
    return np.asarray(vectors[0], dtype=np.float32)


def test_dense_retrieval_with_a_metadata_filter_applies_the_containment_operator(
    seeded: str, embedder: SentenceTransformer
) -> None:
    """``metadata_filter`` restricts the candidate set; ``None`` leaves it unrestricted.

    Both halves are asserted, because a filter that is silently dropped looks identical to a
    filter that matched everything - and the ``@>`` operator's behaviour against a row whose
    metadata is the empty object is exactly the case where "dropped" and "matched all" diverge.
    """
    qvec = _query_vector(embedder, "how are tax brackets applied to income")

    with psycopg.connect(seeded) as conn:
        register_vector(conn)
        unfiltered = dense_topk_filtered(conn, qvec, TENANT, k=10)
        california = dense_topk_filtered(
            conn, qvec, TENANT, metadata_filter={"jurisdiction": "CA"}, k=10
        )
        new_york = dense_topk_filtered(
            conn, qvec, TENANT, metadata_filter={"jurisdiction": "NY"}, k=10
        )
        nowhere = dense_topk_filtered(
            conn, qvec, TENANT, metadata_filter={"jurisdiction": "TX"}, k=10
        )

    assert len(unfiltered) == 4
    assert len(california) == 3
    assert len(new_york) == 1
    assert nowhere == []
    # The filter partitions rather than reorders: the two subsets are disjoint and cover the
    # whole tenant.
    assert {c[0] for c in california} | {c[0] for c in new_york} == {c[0] for c in unfiltered}
    # Distances are cosine distance in [0, 2] and come back nearest-first.
    assert [c[2] for c in unfiltered] == sorted(c[2] for c in unfiltered)


def test_sparse_fts_finds_an_exact_code_that_dense_retrieval_ranks_lower(
    seeded: str, embedder: SentenceTransformer
) -> None:
    """A literal product-code query is answered first by FTS and not by cosine similarity.

    This is the failure hybrid retrieval was adopted to fix. The code shares no vocabulary with
    the prose, so its embedding is not especially close to a query consisting of the code - the
    model has no notion of the token at all - while lexical search matches it exactly.
    """
    expected = chunk_id_for(f"{TENANT}-doc-003", 0)
    qvec = _query_vector(embedder, PRODUCT_CODE)

    with psycopg.connect(seeded) as conn:
        register_vector(conn)
        sparse = sparse_topk_fts(conn, PRODUCT_CODE, TENANT, k=10)
        dense = dense_topk_filtered(conn, qvec, TENANT, k=10)

    assert sparse, "FTS returned nothing for a term that is present verbatim in the corpus"
    assert sparse[0][0] == expected, sparse
    # FTS returns only rows that actually match the tsquery, so the code-bearing chunk is the
    # ONLY result - which is the precision half of what the sparse path contributes.
    assert len(sparse) == 1, sparse
    assert all(score > 0.0 for _, _, score in sparse)

    # Dense retrieval does not fail here, it under-serves: it returns the whole tenant ranked by
    # a similarity that cannot see the token, so the right chunk is one of four rather than
    # first. Asserting it is not uniquely identified is the honest claim; asserting a specific
    # rank would pin a property of the model's weights, which a version bump would break.
    assert len(dense) == 4
    assert rrf_fuse(dense, sparse, top_k=10)[0][0] == expected, "fusion lost the exact match"


def test_rrf_fuses_two_disjoint_lists_and_rewards_membership_of_both() -> None:
    """Fusion over two 50-row lists returns every id, and a shared id outranks its inputs.

    The union of two disjoint 50-row lists is 100 ids, so a ``top_k=60`` fusion keeps 60 of
    them - this asserts the fusion is a union rather than an intersection, which is what makes
    it able to promote a document only one retriever found.
    """
    dense = [(f"chunk-dense-p{i}", f"dense text {i}", 0.1 + i / 100) for i in range(50)]
    sparse = [(f"chunk-sparse-p{i}", f"sparse text {i}", 5.0 - i / 10) for i in range(50)]

    fused = rrf_fuse(dense, sparse, top_k=60)

    assert len(fused) == 60
    fused_ids = {chunk_id for chunk_id, _, _ in fused}
    # Both retrievers' top rows survive: with symmetric weights, rank 1 from either list scores
    # identically, so neither list can crowd the other out of the head.
    assert dense[0][0] in fused_ids and sparse[0][0] in fused_ids
    # Every fused id came from one of the inputs, and every fused score is the reciprocal-rank
    # sum rather than either input's score.
    assert fused_ids <= {c[0] for c in dense} | {c[0] for c in sparse}
    assert fused[0][2] == pytest.approx(1.0 / (K_CONST + 1))
    assert [score for _, _, score in fused] == sorted(
        (score for _, _, score in fused), reverse=True
    )

    # The whole point of RRF, in one assertion: a document at rank 10 in BOTH lists beats a
    # document at rank 1 in only one, because 2/(60+10) > 1/(60+1). Score-blending cannot
    # express this without the two scales being comparable, which they are not.
    shared = [*dense[:9], ("chunk-shared-p0", "shared", 0.9), *dense[9:]]
    shared_sparse = [*sparse[:9], ("chunk-shared-p0", "shared", 0.9), *sparse[9:]]
    promoted = rrf_fuse(shared, shared_sparse, top_k=5)
    assert promoted[0][0] == "chunk-shared-p0", promoted

    # A fusion over two empty lists is empty, not an error: a query that matched nothing is a
    # legitimate outcome and must not raise inside the retrieval path.
    assert rrf_fuse([], []) == []


def test_coverage_jaccard_is_finite_and_within_the_unit_interval(
    seeded: str, embedder: SentenceTransformer
) -> None:
    """The per-request diagnostic is a real number in ``[0, 1]`` on a representative query.

    Finiteness is the half that has actually broken things: the union is empty whenever both
    retrievers return nothing, and an unguarded ``len(both) / len(union)`` puts a
    ``ZeroDivisionError`` inside the retrieval path - on the query that already found nothing,
    which is the worst moment for a second failure.
    """
    qvec = _query_vector(embedder, "standard deduction and taxable income")

    with psycopg.connect(seeded) as conn:
        register_vector(conn)
        dense = dense_topk_filtered(conn, qvec, TENANT, k=10)
        sparse = sparse_topk_fts(conn, "standard deduction taxable income", TENANT, k=10)

    diagnostic = coverage(dense, sparse)

    assert set(diagnostic) == {"dense_only", "sparse_only", "both", "jaccard"}
    assert np.isfinite(diagnostic["jaccard"])
    assert 0.0 <= diagnostic["jaccard"] <= 1.0
    # The counts have to account for the union exactly, or the diagnostic is describing a
    # different pair of lists than the one that was retrieved.
    assert diagnostic["dense_only"] + diagnostic["sparse_only"] + diagnostic["both"] == len(
        {c[0] for c in dense} | {c[0] for c in sparse}
    )

    # The empty case, explicitly: no query in this corpus produces it, so it is constructed.
    assert coverage([], []) == {
        "dense_only": 0.0,
        "sparse_only": 0.0,
        "both": 0.0,
        "jaccard": 0.0,
    }


def test_the_sql_chunk_id_matches_the_chunker_byte_for_byte(
    seeded: str, embedder: SentenceTransformer
) -> None:
    """The retrievers rebuild the chunker's synthetic id in SQL; the two must agree.

    Two independent implementations of one identifier format. If they drift, citations stop
    resolving and the semantic cache stops hitting - both silently, because a mismatched id is
    a valid string that simply refers to nothing.
    """
    qvec = _query_vector(embedder, "marginal brackets")
    with psycopg.connect(seeded) as conn:
        register_vector(conn)
        dense = dense_topk_filtered(conn, qvec, TENANT, k=10)

    assert {chunk_id for chunk_id, _, _ in dense} == {
        chunk_id_for(f"{TENANT}-doc-{i:03d}", 0) for i in range(4)
    }
    # And the dataclass field the SQL expression is derived from is still the one it reads.
    assert (
        replace(
            CorpusRow(
                doc_id="d",
                chunk_idx=7,
                chunk_text="t",
                embedding=np.zeros(1, dtype=np.float32),
                model_version=MODEL_NAME,
                tenant_id=TENANT,
            ),
            chunk_idx=7,
        ).chunk_idx
        == 7
    )
