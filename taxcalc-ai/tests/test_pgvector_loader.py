# taxcalc-ai/tests/test_pgvector_loader.py
"""Testcontainers-backed tests for the pgvector loader and the extended schema.

These run against a real ``pgvector/pgvector:pg16`` container rather than a mock, because every
property under test is a property of Postgres and not of this Python code. A mocked cursor would
happily accept an ``ON CONFLICT`` clause naming a constraint that does not exist, a ``vector``
parameter that was never adapted, and an HNSW index whose operator class does not match the
query - which are precisely the three defects these tests exist to catch.

The container is session-scoped: the image pull and the initdb are the expensive part, and the
DDL is idempotent (``CREATE ... IF NOT EXISTS``), so one instance serves every test here. Tests
that care about row counts scope themselves by ``tenant_id`` or truncate, rather than assuming
an empty table.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import psycopg
import pytest
from numpy.typing import NDArray
from pgvector.psycopg import register_vector

from taxcalc_ai.corpus import EMBEDDING_DIM, MODEL_NAME, CorpusRow, content_hash
from taxcalc_ai.embedder import ChunkCandidate, pending_candidates
from taxcalc_ai.pgvector_loader import CrossTenantDocIdError, load_rows

#: The index whose use the EXPLAIN assertion is about.
HNSW_INDEX = "doc_chunks_embedding_hnsw"


def _unit_vector(seed: int) -> NDArray[np.float32]:
    """A deterministic unit-length ``float32`` vector - a stand-in for a real embedding.

    Unit length matters: the stored corpus is normalised at write time (see
    :func:`taxcalc_ai.corpus.embed_dataframe`), and cosine distance only stands in for angular
    distance when it is.
    """
    vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    vec[seed % EMBEDDING_DIM] = 1.0
    return vec


def _rows(count: int, tenant_id: str, text_prefix: str = "chunk") -> list[CorpusRow]:
    """Build ``count`` rows for one tenant, in that tenant's own ``doc_id`` namespace.

    The ``doc_id`` carries ``tenant_id`` deliberately, and it is not cosmetic. The table's
    uniqueness key is ``(doc_id, chunk_idx, model_version)`` - it does NOT include
    ``tenant_id`` - so two tenants using the same ``doc_id`` collide on the ``ON CONFLICT``
    arbiter, and the second load updates the first tenant's row instead of inserting its own.
    Sharing a namespace here made three tests fail against a loader that was behaving exactly
    as designed. Since the tenant guard landed, a collision raises
    :class:`~taxcalc_ai.pgvector_loader.CrossTenantDocIdError` rather than silently overwriting -
    see :func:`test_a_cross_tenant_doc_id_collision_raises_instead_of_overwriting` - so these
    per-tenant namespaces now keep the fixtures legal as well as independent.
    """
    return [
        CorpusRow(
            doc_id=f"{tenant_id}-doc-{i // 5:03d}",
            chunk_idx=i % 5,
            chunk_text=f"{text_prefix} {i}",
            embedding=_unit_vector(i),
            model_version=MODEL_NAME,
            tenant_id=tenant_id,
        )
        for i in range(count)
    ]


def _count_for(dsn: str, tenant_id: str) -> int:
    """Count rows belonging to one tenant, so concurrent test data cannot skew the answer."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM doc_chunks WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def test_loading_one_hundred_rows_returns_one_hundred(pg_dsn: str) -> None:
    """A clean load of 100 rows writes 100 rows."""
    tenant = "tenant-load"

    written = load_rows(pg_dsn, _rows(100, tenant))

    assert written == 100
    assert _count_for(pg_dsn, tenant) == 100


def test_loading_the_same_rows_again_is_idempotent(pg_dsn: str) -> None:
    """Re-running a completed load leaves the row count at 100 instead of raising or doubling.

    This is the property that makes "the bulk load died halfway, run it again" a recovery
    procedure rather than an incident. A plain ``INSERT`` would fail here on the first
    already-present row; a load without the ``UNIQUE`` constraint backing the conflict target
    would double the corpus silently.
    """
    tenant = "tenant-idempotent"
    rows = _rows(100, tenant)
    load_rows(pg_dsn, rows)

    second = load_rows(pg_dsn, rows)

    assert second == 100
    assert _count_for(pg_dsn, tenant) == 100


def test_do_update_refreshes_text_and_embedding_but_not_created_at(pg_dsn: str) -> None:
    """A re-load corrects changed content while leaving the first-arrival timestamp alone.

    ``created_at`` records when a chunk entered the corpus. Bumping it on every retry would make
    "when did we ingest this" answer "the last time anything was retried", so the ``DO UPDATE``
    set-list deliberately omits it.
    """
    tenant = "tenant-update"
    original = _rows(1, tenant, text_prefix="original")
    load_rows(pg_dsn, original)

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT chunk_text, created_at FROM doc_chunks WHERE tenant_id = %s", (tenant,))
        first = cur.fetchone()
    assert first is not None

    revised = [
        CorpusRow(
            doc_id=original[0].doc_id,
            chunk_idx=original[0].chunk_idx,
            chunk_text="revised text",
            embedding=_unit_vector(42),
            model_version=original[0].model_version,
            tenant_id=tenant,
        )
    ]
    load_rows(pg_dsn, revised)

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT chunk_text, created_at FROM doc_chunks WHERE tenant_id = %s", (tenant,))
        after = cur.fetchone()
    assert after is not None
    assert after[0] == "revised text"
    assert after[1] == first[1]
    assert _count_for(pg_dsn, tenant) == 1


def test_a_new_model_version_lands_beside_the_old_rows(pg_dsn: str) -> None:
    """Re-embedding under a different model adds rows rather than overwriting the old ones.

    ``model_version`` is part of the uniqueness key precisely so a model swap is additive and
    therefore reversible. With ``(doc_id, chunk_idx)`` alone, this second load would silently
    destroy the vectors the currently-serving retrieval depends on.
    """
    tenant = "tenant-modelswap"
    base = _rows(3, tenant)
    load_rows(pg_dsn, base)

    reembedded = [
        CorpusRow(
            doc_id=r.doc_id,
            chunk_idx=r.chunk_idx,
            chunk_text=r.chunk_text,
            embedding=r.embedding,
            model_version="all-MiniLM-L12-v2",
            tenant_id=tenant,
        )
        for r in base
    ]
    load_rows(pg_dsn, reembedded)

    assert _count_for(pg_dsn, tenant) == 6


def test_the_hnsw_index_exists_in_pg_indexes(pg_dsn: str) -> None:
    """The DDL actually created the HNSW index, with the cosine operator class.

    The operator class is asserted from the index definition rather than assumed: an index built
    with ``vector_l2_ops`` exists under the same name, satisfies a name-only check, and is
    unusable by the ``<=>`` query.
    """
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", (HNSW_INDEX,))
        row = cur.fetchone()

    assert row is not None, f"{HNSW_INDEX} was not created by the DDL"
    indexdef = str(row[0])
    assert "USING hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


def test_explain_shows_the_cosine_ann_query_using_the_hnsw_index(pg_dsn: str) -> None:
    """``EXPLAIN`` reports an Index Scan on the HNSW index, not a Seq Scan.

    This is the assertion that catches an operator-class mismatch, which is invisible any other
    way: a query written with ``<->`` against a ``vector_cosine_ops`` index does not fail and
    does not warn - the planner silently falls back to scanning every row, and the symptom
    surfaces months later as "search got slow as the corpus grew".

    ``enable_seqscan = off`` is set for this statement because the planner is right on a table
    this small: a sequential scan over a few hundred test rows genuinely beats an index. Turning
    it off asks the planner the question this test is actually about - *can* this query use the
    HNSW index - rather than the question of which is cheaper at toy scale. A mismatched operator
    class still produces a Seq Scan even with the setting off, because then the index is not a
    candidate at all.
    """
    load_rows(pg_dsn, _rows(20, "tenant-explain"))
    query_vec = _unit_vector(7)

    with psycopg.connect(pg_dsn) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute(
                "EXPLAIN SELECT doc_id, chunk_idx, embedding <=> %s AS dist "
                "FROM doc_chunks ORDER BY embedding <=> %s LIMIT 5",
                (query_vec, query_vec),
            )
            plan = "\n".join(str(r[0]) for r in cur.fetchall())

    assert f"Index Scan using {HNSW_INDEX}" in plan, plan
    assert "Seq Scan" not in plan, plan


def test_loading_no_rows_is_a_no_op(pg_dsn: str) -> None:
    """An empty corpus returns 0 without opening a connection or raising.

    ``load_corpus`` can legitimately filter every row out; that is an outcome, not an error.
    """
    assert load_rows(pg_dsn, []) == 0


def test_a_cross_tenant_doc_id_collision_raises_instead_of_overwriting(pg_dsn: str) -> None:
    """A second tenant claiming an existing ``doc_id`` fails loudly and changes nothing.

    ``tenant_id`` is not part of ``UNIQUE (doc_id, chunk_idx, model_version)``, so it is not part
    of the ``ON CONFLICT`` arbiter either. Without a guard, tenant-b's load would take the
    ``DO UPDATE`` branch and rewrite tenant-a's ``chunk_text`` and ``embedding`` while leaving
    ``tenant_id`` alone - one tenant's content stored under another's label, which the
    tenant-scoped read path would then serve to the wrong tenant. An INSERT that reports success
    and leaks data across a tenant boundary is the worst shape a bug can take here.

    The ``WHERE doc_chunks.tenant_id = EXCLUDED.tenant_id`` clause makes that row not match, the
    update affects zero rows, and the count shortfall becomes this exception. Both halves are
    asserted: that it raises, and that the incumbent's row is untouched afterwards.
    """
    shared_doc = "shared-doc-001"

    def row_for(tenant: str, text: str) -> CorpusRow:
        """One chunk under the shared ``doc_id``, differing only in tenant and text."""
        return CorpusRow(
            doc_id=shared_doc,
            chunk_idx=0,
            chunk_text=text,
            embedding=_unit_vector(1),
            model_version=MODEL_NAME,
            tenant_id=tenant,
        )

    load_rows(pg_dsn, [row_for("tenant-first", "first tenant content")])

    with pytest.raises(CrossTenantDocIdError, match="already owned by a different tenant"):
        load_rows(pg_dsn, [row_for("tenant-second", "second tenant content")])

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tenant_id, chunk_text FROM doc_chunks WHERE doc_id = %s", (shared_doc,))
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "tenant-first", "the incumbent's ownership is intact"
    assert rows[0][1] == "first tenant content", "and so is the incumbent's content"


def test_a_cross_tenant_collision_rolls_back_the_whole_batch(pg_dsn: str) -> None:
    """One bad row aborts the batch; the good rows beside it are not left half-applied.

    The guard is checked before ``conn.commit()`` precisely so a partially-applied load is not a
    reachable state. A batch that wrote nine of ten rows and raised would be far harder to
    recover from than one that wrote none, because "run it again" would no longer be safe advice
    without first working out which nine landed.
    """
    tenant, intruder = "tenant-rollback", "tenant-rollback-intruder"
    load_rows(pg_dsn, _rows(3, tenant))
    before = _count_for(pg_dsn, tenant)

    # Nine clean rows in a fresh namespace, plus one that collides with the incumbent above.
    clean = _rows(9, intruder)
    colliding = CorpusRow(
        doc_id=f"{tenant}-doc-000",
        chunk_idx=0,
        chunk_text="intruder content",
        embedding=_unit_vector(2),
        model_version=MODEL_NAME,
        tenant_id=intruder,
    )

    with pytest.raises(CrossTenantDocIdError):
        load_rows(pg_dsn, [*clean, colliding])

    assert _count_for(pg_dsn, intruder) == 0, "the nine clean rows were rolled back too"
    assert _count_for(pg_dsn, tenant) == before, "the incumbent is untouched"


def test_the_same_tenant_reloading_its_own_rows_is_still_idempotent(pg_dsn: str) -> None:
    """The guard does not break the ordinary retry path it sits on top of.

    ``WHERE doc_chunks.tenant_id = EXCLUDED.tenant_id`` is true for every same-tenant re-load, so
    the update matches, the row count holds, and no exception is raised. Worth pinning: a guard
    that also blocked legitimate retries would have quietly removed the idempotency the loader
    exists to provide.
    """
    tenant = "tenant-guard-idempotent"
    rows = _rows(20, tenant)
    load_rows(pg_dsn, rows)

    assert load_rows(pg_dsn, rows) == 20
    assert _count_for(pg_dsn, tenant) == 20


# ---- W7 D3: the V002 additions and the idempotent re-embed gate -----------------------------


def test_v002_added_the_metadata_hash_and_tsv_columns_with_their_indexes(pg_dsn: str) -> None:
    """``sql/V002`` is applied by the fixture and left the table in the shape RAG 2.0 needs.

    A schema assertion rather than a behaviour one, because three of these objects have no
    behaviour this suite can otherwise reach: a partial HNSW index is only *chosen* by the
    planner once the corpus is large enough for a sequential scan to look expensive, and the
    generated ``chunk_tsv`` column is invisible until a full-text query runs. Asserting they
    exist is what catches a migration that was edited into silence.
    """
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_nullable, is_generated FROM information_schema.columns "
            "WHERE table_name = 'doc_chunks' AND column_name = ANY(%s)",
            (["chunk_metadata", "content_hash", "chunk_tsv"],),
        )
        columns = {str(row[0]): (str(row[1]), str(row[2])) for row in cur.fetchall()}

        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'doc_chunks'",
        )
        indexes = {str(row[0]) for row in cur.fetchall()}

    # chunk_metadata is NOT NULL because `NULL @> '{...}'` is NULL, not false - a nullable
    # column would make a metadata filter drop legacy rows rather than simply not match them.
    assert columns["chunk_metadata"] == ("NO", "NEVER"), columns
    # content_hash is nullable on purpose: a legacy V001 row has no hash, and "unknown" must not
    # be spelled as a value the gate would treat as a match. See taxcalc_ai.embedder.
    assert columns["content_hash"] == ("YES", "NEVER"), columns
    # chunk_tsv is GENERATED so the sparse index can never rank stale text.
    assert columns["chunk_tsv"][1] == "ALWAYS", columns

    assert {
        "doc_chunks_metadata_gin",
        "doc_chunks_tenant_a_hnsw",
        "doc_chunks_tenant_b_hnsw",
        "doc_chunks_tenant_c_hnsw",
        "doc_chunks_tsv_gin",
    } <= indexes, sorted(indexes)


def test_the_pre_embed_gate_skips_unchanged_chunks_and_keeps_changed_ones(pg_dsn: str) -> None:
    """The gate is the difference between an idempotent write and a cheap re-run.

    ``ON CONFLICT DO UPDATE`` already made a second load harmless; it did not make it free,
    because the embedding it overwrote had to be computed first. This asserts the three cases
    that matter: an unchanged chunk is dropped from the batch, a chunk whose text moved is
    kept, and a model swap keeps everything regardless of text.
    """
    tenant = "tenant-gate"
    # Hashes set explicitly: `_rows` is the W7 D2 helper and leaves content_hash unset, which
    # is the legacy shape the next test covers. Here the stored rows must already carry a hash,
    # because a NULL one is precisely the case that does NOT skip.
    rows = [replace(r, content_hash=content_hash(r.chunk_text)) for r in _rows(3, tenant)]
    load_rows(pg_dsn, rows)

    unchanged = [
        ChunkCandidate(
            doc_id=r.doc_id,
            chunk_idx=r.chunk_idx,
            chunk_text=r.chunk_text,
            tenant_id=tenant,
        )
        for r in rows
    ]

    # All three already stored at this model version with a matching hash: nothing to embed.
    assert pending_candidates(pg_dsn, unchanged, model_version=MODEL_NAME) == []

    # One chunk's text moved. Only that one comes back.
    edited = list(unchanged)
    edited[1] = ChunkCandidate(
        doc_id=unchanged[1].doc_id,
        chunk_idx=unchanged[1].chunk_idx,
        chunk_text=unchanged[1].chunk_text + " (revised)",
        tenant_id=tenant,
    )
    assert pending_candidates(pg_dsn, edited, model_version=MODEL_NAME) == [edited[1]]

    # A model swap re-embeds everything even though no text changed: two models' vectors share
    # a geometry without sharing a meaning, so a skip here would leave the corpus mixed.
    assert pending_candidates(pg_dsn, unchanged, model_version="some-other-model") == unchanged


def test_a_row_written_before_v002_has_no_hash_and_is_re_embedded_once(pg_dsn: str) -> None:
    """A ``NULL`` stored hash compares unequal to everything, so the row is re-embedded once.

    This is the legacy-row path. Defaulting ``content_hash`` to ``''`` instead of leaving it
    nullable would have made the gate assert that such a row's content hashes to the empty
    string - and it would then skip that row forever, serving a vector whose provenance nobody
    can establish.
    """
    tenant = "tenant-legacy"
    rows = _rows(1, tenant)
    # A V001-shaped write: the same row, with content_hash left unset.
    load_rows(pg_dsn, [replace(rows[0], content_hash=None)])

    candidate = ChunkCandidate(
        doc_id=rows[0].doc_id,
        chunk_idx=rows[0].chunk_idx,
        chunk_text=rows[0].chunk_text,
        tenant_id=tenant,
    )

    assert pending_candidates(pg_dsn, [candidate], model_version=MODEL_NAME) == [candidate]

    # After one pass the hash is stored, so the next run skips it.
    load_rows(pg_dsn, [replace(rows[0], content_hash=candidate.content_hash)])
    assert pending_candidates(pg_dsn, [candidate], model_version=MODEL_NAME) == []


def test_chunk_metadata_round_trips_as_jsonb_and_is_containment_queryable(pg_dsn: str) -> None:
    """Metadata written by the loader is queryable with the ``@>`` operator the pre-filter uses.

    The write and the read have to agree on the *shape*, not just the column: a metadata payload
    stored as a JSON string rather than an object would still be a valid ``jsonb`` value and
    would match no containment filter at all.
    """
    tenant = "tenant-meta"
    row = replace(_rows(1, tenant)[0], chunk_metadata={"jurisdiction": "CA", "tax_year": 2026})
    load_rows(pg_dsn, [row])

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM doc_chunks WHERE tenant_id = %s AND chunk_metadata @> %s::jsonb",
            (tenant, '{"jurisdiction": "CA"}'),
        )
        matched = cur.fetchone()
        assert matched is not None and int(matched[0]) == 1

        cur.execute(
            "SELECT count(*) FROM doc_chunks WHERE tenant_id = %s AND chunk_metadata @> %s::jsonb",
            (tenant, '{"jurisdiction": "NY"}'),
        )
        unmatched = cur.fetchone()
        assert unmatched is not None and int(unmatched[0]) == 0
