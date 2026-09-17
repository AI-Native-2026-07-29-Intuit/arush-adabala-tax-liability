-- taxcalc-ai/sql/V002__rag2_metadata_and_partial_indexes.sql
-- Week 7 Day 3 Task 1: the RAG 2.0 schema additions on top of V001's doc_chunks.
--
-- Applied ONLINE and safe with concurrent reads. Existing V001 rows remain valid: both new
-- columns are either defaulted (chunk_metadata) or nullable (content_hash), and every index
-- below is created CONCURRENTLY, so no statement here takes an ACCESS EXCLUSIVE lock for
-- longer than the catalogue update itself.
--
-- NUMBERED V002 FOR THE SAME REASON V001 IS NOT V6: this file is NOT a Flyway migration and
-- must not be moved under src/main/resources/db/migration/. The sidecar applies its own DDL
-- (tests/conftest.py's pg_dsn fixture executes V001 then V002); the numbering is the sidecar's
-- own ordering, independent of the Java service's migration history.
--
-- CREATE INDEX CONCURRENTLY CANNOT RUN INSIDE A TRANSACTION BLOCK. psycopg3 opens an implicit
-- transaction on the first statement of a connection, so the fixture and the Airflow DAG apply
-- this file with autocommit=True. Applied inside a transaction Postgres raises
-- "CREATE INDEX CONCURRENTLY cannot run inside a transaction block" - a loud failure, which is
-- the right direction for that mistake to break, but it is why the callers set autocommit.

ALTER TABLE doc_chunks
    -- NOT NULL DEFAULT '{}'::jsonb rather than nullable: every read path that filters metadata
    -- uses the containment operator (@>), and `NULL @> '{"k":"v"}'` is NULL, not false - so a
    -- nullable column would make a metadata filter silently drop every legacy row instead of
    -- matching none of them. An empty object matches no filter and breaks nothing.
    ADD COLUMN IF NOT EXISTS chunk_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Nullable deliberately. This is the idempotent-re-embed key: the pre-embed gate skips the
    -- model call when the stored content_hash AND model_version both match the incoming chunk.
    -- A legacy V001 row has no hash, so it compares unequal to everything and gets re-embedded
    -- once - which is the correct outcome for a row whose content provenance is unknown. A
    -- DEFAULT '' would instead claim "this row's content hashes to the empty string", which is
    -- a lie the gate would act on.
    ADD COLUMN IF NOT EXISTS content_hash text;

-- GIN index for arbitrary JSONB key/value pre-filtering.
--
-- jsonb_path_ops, NOT the default jsonb_ops. jsonb_path_ops indexes only the hashed
-- key-PATH-to-value pairs, so it is materially smaller and faster than jsonb_ops (which also
-- indexes every key on its own) - at the cost of supporting fewer operators. The only operator
-- the application uses against this column is path-equality containment (@>), which is exactly
-- what jsonb_path_ops covers. Switching to `?` (key-exists) later would need jsonb_ops back.
CREATE INDEX CONCURRENTLY IF NOT EXISTS doc_chunks_metadata_gin
    ON doc_chunks USING gin (chunk_metadata jsonb_path_ops);

-- Per-tenant PARTIAL HNSW indexes, one per tenant, replacing reliance on the single global
-- HNSW from V001 for tenant-scoped queries.
--
-- WHY PARTIAL BEATS ONE GLOBAL INDEX PLUS A WHERE FILTER. HNSW is an approximate-nearest-
-- neighbour graph over the vector column alone; it cannot see tenant_id. A query written as
-- `WHERE tenant_id = %s ORDER BY embedding <=> %s LIMIT k` against the global index therefore
-- walks the graph over EVERY tenant's vectors, collects its ef_search candidates from the
-- whole corpus, and only then discards the ones belonging to other tenants. The result is not
-- wrong, it is under-recalled: the k rows returned are the nearest surviving members of a
-- candidate pool that was never tenant-specific, so recall degrades as the number of tenants
-- grows, silently, with no plan change to notice. A partial index contains only one tenant's
-- vectors, so ef_search candidates are all eligible and recall matches the unfiltered case.
--
-- m = 24, ef_construction = 128: above V001's pgvector defaults (16 / 64). A partial index
-- holds a fraction of the rows, so the extra build time and memory are affordable, and a
-- denser graph is what buys back recall on the smaller per-tenant graph.
--
-- The tenant list is enumerated rather than generated. Postgres has no "partial index per
-- distinct value" construct; a new tenant needs a new CREATE INDEX CONCURRENTLY, which is an
-- online operation and belongs in the onboarding runbook. Until one exists, that tenant's
-- queries fall back to the global V001 HNSW - degraded recall, not an error.
CREATE INDEX CONCURRENTLY IF NOT EXISTS doc_chunks_tenant_a_hnsw
    ON doc_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 128)
    WHERE tenant_id = 'tenant-a';

CREATE INDEX CONCURRENTLY IF NOT EXISTS doc_chunks_tenant_b_hnsw
    ON doc_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 128)
    WHERE tenant_id = 'tenant-b';

CREATE INDEX CONCURRENTLY IF NOT EXISTS doc_chunks_tenant_c_hnsw
    ON doc_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 128)
    WHERE tenant_id = 'tenant-c';

-- Postgres full-text search column + GIN index: the BM25-shaped sparse retriever's index.
--
-- GENERATED ALWAYS AS ... STORED, not a trigger and not an application-maintained column. The
-- tsvector is a pure function of chunk_text, and the two must never disagree: a sparse index
-- built over stale text ranks documents by what they used to say. A generated column makes
-- that impossible by construction - Postgres recomputes it on every INSERT and UPDATE of
-- chunk_text, including the loader's ON CONFLICT DO UPDATE path, with nothing for the
-- application to remember to do.
--
-- 'english' is pinned as a literal rather than left to default_text_search_config, because
-- GENERATED ALWAYS requires an IMMUTABLE expression and to_tsvector(regconfig, text) is only
-- immutable in its two-argument form. The single-argument to_tsvector(text) reads a GUC, is
-- merely STABLE, and is rejected here - which is fortunate, since a corpus whose stemming
-- depends on a session setting would index differently depending on who connected.
ALTER TABLE doc_chunks
    ADD COLUMN IF NOT EXISTS chunk_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;

CREATE INDEX CONCURRENTLY IF NOT EXISTS doc_chunks_tsv_gin
    ON doc_chunks USING gin (chunk_tsv);
