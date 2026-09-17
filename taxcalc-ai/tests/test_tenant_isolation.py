# taxcalc-ai/tests/test_tenant_isolation.py
"""Tenant isolation, asserted against the DATABASE rather than against the request parameter.

The distinction is the entire value of this file. A test that checks the returned rows' tenant
by reading the ``tenant_id`` the caller passed in proves nothing: it is comparing a value to
itself. This test takes the chunk ids that came back, looks up their ``tenant_id`` **in the
table**, and asserts every one of them belongs to the requesting tenant. That is the only form
of the assertion that can fail when the ``WHERE`` clause is dropped.

The three tenants are seeded with the SAME query-relevant content, deliberately. A leak only
shows up when another tenant holds something the ranking would want: seed them with unrelated
text and the dense search returns the right rows for the wrong reason, and the test passes
against a retriever with no tenant filter at all.
"""

from __future__ import annotations

import numpy as np
import psycopg
import pytest
from numpy.typing import NDArray
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from taxcalc_ai.corpus import MODEL_NAME, CorpusRow, content_hash
from taxcalc_ai.hybrid import dense_topk_filtered, sparse_topk_fts
from taxcalc_ai.pgvector_loader import load_rows

#: The three tenants. Matching the per-tenant partial HNSW indexes in ``sql/V002`` so the query
#: under test takes the same index path production would.
TENANTS = ("tenant-a", "tenant-b", "tenant-c")

#: One sentence, seeded for every tenant. Identical content across tenants is what makes a
#: missing ``WHERE`` clause detectable - see the module docstring.
SHARED_TEXT = (
    "Nexus thresholds determine when a business must register and remit in a jurisdiction, "
    "and the economic nexus test is evaluated per state on prior-year receipts."
)

#: The question. Deliberately phrased to match :data:`SHARED_TEXT` for both retrievers: the
#: dense path ranks it by similarity, the sparse path by term overlap.
QUESTION = "nexus thresholds"


@pytest.fixture(scope="module")
def three_tenant_corpus(pg_dsn: str) -> str:
    """Seed the same query-relevant document into all three tenants.

    ``doc_id`` is namespaced per tenant because ``doc_chunks``'s uniqueness key is
    ``(doc_id, chunk_idx, model_version)`` and does NOT include ``tenant_id`` - sharing a
    ``doc_id`` across tenants collides on the ``ON CONFLICT`` arbiter and raises
    :class:`~taxcalc_ai.pgvector_loader.CrossTenantDocIdError`. That guard is W7 D2's, and the
    namespacing here is what keeps these fixtures legal rather than a workaround.
    """
    model = SentenceTransformer(MODEL_NAME)
    vectors: NDArray[np.float32] = model.encode(
        [SHARED_TEXT] * len(TENANTS),
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)

    load_rows(
        pg_dsn,
        [
            CorpusRow(
                doc_id=f"{tenant}-iso-doc-000",
                chunk_idx=0,
                chunk_text=SHARED_TEXT,
                embedding=vector,
                model_version=MODEL_NAME,
                tenant_id=tenant,
                chunk_metadata={"suite": "tenant-isolation"},
                content_hash=content_hash(SHARED_TEXT),
            )
            for tenant, vector in zip(TENANTS, vectors, strict=True)
        ],
    )
    return pg_dsn


def _database_side_tenants(dsn: str, chunk_ids: list[str]) -> set[str]:
    """Look up, in the table, which tenants own the chunks identified by ``chunk_ids``.

    The chunk id is the synthetic ``chunk-{doc_id}-p{chunk_idx}`` string the retrievers build in
    SQL, so it is decomposed the same way here rather than parsed: the ``WHERE`` reconstructs the
    expression and compares, which means a change to the id format fails this lookup loudly
    instead of silently matching nothing (and therefore passing).

    :param dsn: libpq connection string.
    :param chunk_ids: Chunk ids returned by a retriever.
    :returns: The distinct ``tenant_id`` values those rows carry in the database.
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT tenant_id FROM doc_chunks "
            "WHERE ('chunk-' || doc_id || '-p' || chunk_idx) = ANY(%s)",
            (chunk_ids,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def test_a_tenant_a_query_returns_no_other_tenants_chunks(three_tenant_corpus: str) -> None:
    """Every chunk a ``tenant-a`` query returns belongs to ``tenant-a`` in the table.

    Both retrievers are checked, because they enforce the boundary with two different
    predicates against two different indexes, and either could be dropped independently. The
    dense path's filter also selects the per-tenant partial HNSW index; the sparse path's is a
    plain b-tree-backed restriction ahead of the GIN match.
    """
    dsn = three_tenant_corpus
    model = SentenceTransformer(MODEL_NAME)
    qvec_raw: NDArray[np.float32] = model.encode(
        [QUESTION], normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)
    qvec = np.asarray(qvec_raw[0], dtype=np.float32)

    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        dense = dense_topk_filtered(conn, qvec, "tenant-a", k=20)
        sparse = sparse_topk_fts(conn, QUESTION, "tenant-a", k=20)

    assert dense, "the dense retriever returned nothing; the fixture did not seed"
    assert sparse, "the sparse retriever returned nothing; chunk_tsv is not populated"

    # The assertion that matters: the tenant is read from the TABLE, not from the parameter.
    assert _database_side_tenants(dsn, [c[0] for c in dense]) == {"tenant-a"}
    assert _database_side_tenants(dsn, [c[0] for c in sparse]) == {"tenant-a"}


def test_the_other_tenants_hold_the_same_content_so_a_leak_would_be_detectable(
    three_tenant_corpus: str,
) -> None:
    """The fixture is adversarial: all three tenants hold the same query-relevant sentence.

    Without this, the previous test is vacuous. A corpus where only ``tenant-a`` has relevant
    content passes against a retriever with no tenant filter whatsoever, because the ranking
    alone happens to return the right rows. This asserts the trap is actually baited, and that
    an unfiltered query really would cross the boundary.
    """
    dsn = three_tenant_corpus
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, count(*) FROM doc_chunks WHERE chunk_text = %s GROUP BY 1",
            (SHARED_TEXT,),
        )
        seeded = {str(row[0]): int(row[1]) for row in cur.fetchall()}

        # The unfiltered form of the same search - what the retriever would do with its WHERE
        # clause removed. It returns all three tenants, which is what the filter prevents.
        cur.execute(
            "SELECT DISTINCT tenant_id FROM doc_chunks "
            "WHERE chunk_tsv @@ websearch_to_tsquery('english', %s)",
            (QUESTION,),
        )
        unfiltered = {str(row[0]) for row in cur.fetchall()}

    assert set(TENANTS) <= set(seeded), seeded
    assert all(count >= 1 for tenant, count in seeded.items() if tenant in TENANTS)
    assert set(TENANTS) <= unfiltered, unfiltered
