-- taxcalc-ai/sql/V001__doc_chunks.sql
-- Week 7 Day 2 Task 2: the extended pgvector schema the sidecar's retrieval reads from.
--
-- Apply once per database. The `vector` extension already exists from W6 D4, which created
-- taxcalc.taxpayer_embeddings for the Java service; this table is the Python sidecar's own
-- corpus and is deliberately separate from it. They hold different things at different
-- geometries - taxpayer_embeddings is vector(1024) from bge-large-en-v1.5 over taxpayer
-- records, doc_chunks is vector(384) from all-MiniLM-L6-v2 over document text - so merging
-- them would mean one column that cannot be one dimension.
--
-- NUMBERED V001, NOT V6. This file is NOT a Flyway migration and must not be placed under
-- src/main/resources/db/migration/. Flyway keys applied migrations by version and validates
-- checksums across the whole history, so dropping a sidecar-owned file into the Java service's
-- migration path would make the Python project's schema changes able to fail the Java service's
-- context startup. The sidecar applies this DDL itself (tests/test_pgvector_loader.py and
-- tests/test_great_expectations_suite.py both read and execute it); the V001 prefix is the
-- sidecar's own ordering, starting from its own beginning.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS doc_chunks (
    -- BIGSERIAL here, and it is the one place in this project that a bare integer id is
    -- correct. The project convention (String UUIDs or prefixed synthetic ids) exists so that
    -- identifiers link to other systems and survive arithmetic mistakes. chunk_id links to
    -- nothing: it is a physical row handle that no API returns and no other table references.
    -- The identifier that does travel is doc_id, which is TEXT and carries its prefix.
    chunk_id      BIGSERIAL    PRIMARY KEY,
    doc_id        TEXT         NOT NULL,
    chunk_idx     INTEGER      NOT NULL,
    chunk_text    TEXT         NOT NULL,
    embedding     vector(384)  NOT NULL,
    -- model_version is what makes a model swap additive rather than destructive. Vectors from
    -- two different models occupy the same geometric space without meaning the same thing, so
    -- ranking them against each other returns confident nonsense. Carrying the model on the row
    -- lets the read path filter (WHERE model_version = %s) and lets a re-embedding run land
    -- beside the old vectors instead of on top of them.
    model_version TEXT         NOT NULL,
    tenant_id     TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- The idempotency key the loader's ON CONFLICT clause resolves on. Without this constraint
    -- the ON CONFLICT has no arbiter index and the INSERT fails outright - so this line and
    -- the loader's conflict target are one contract, not two independent choices.
    --
    -- model_version is part of the key for the reason above: (doc_id, chunk_idx) alone would
    -- make a re-embedding under a new model overwrite the old vectors rather than sit beside
    -- them, turning a reversible experiment into a destructive one.
    UNIQUE (doc_id, chunk_idx, model_version)
);

-- Plain b-tree for the "fetch this document's chunks" access path, which is a lookup rather
-- than a similarity search and would otherwise scan.
CREATE INDEX IF NOT EXISTS doc_chunks_doc_id_idx
    ON doc_chunks (doc_id);

-- Compound index on the two columns every retrieval filters by before it ranks by distance.
-- An HNSW index cannot do that filtering - it is an approximate-nearest-neighbour structure
-- over the vector column alone - so without this a tenant-scoped search reads every tenant's
-- rows and discards the ones it does not want. Column order is (tenant_id, model_version)
-- because tenant_id is the more selective of the two and a b-tree can use a leading-column
-- prefix, so this index also serves a tenant-only filter.
CREATE INDEX IF NOT EXISTS doc_chunks_tenant_model_idx
    ON doc_chunks (tenant_id, model_version);

-- HNSW over cosine distance.
--
-- THE OPERATOR CLASS MUST MATCH THE QUERY OPERATOR. vector_cosine_ops indexes for `<=>`
-- (cosine). A query written with `<->` (L2) or `<#>` (inner product) against this index does
-- not fail and does not warn - the planner simply cannot use the index and falls back to a
-- sequential scan over every row. The symptom is "search got slow as the corpus grew", which
-- reads like a capacity problem and is a one-character mismatch. tests/test_pgvector_loader.py
-- asserts EXPLAIN reports an Index Scan for exactly this reason, which is the only way the
-- mismatch shows up as a failure rather than as a latency graph.
--
-- m = 16, ef_construction = 64 are pgvector's defaults, stated explicitly rather than
-- inherited: they are the knobs trading index build time and memory against recall, and a
-- future tuning pass should see the current values in the diff instead of having to know what
-- the defaults were at the version this was created under.
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_hnsw
    ON doc_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
