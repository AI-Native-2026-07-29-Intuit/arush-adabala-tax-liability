package com.uptimecrew.tax_liability.embeddings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.UUID;

import com.uptimecrew.tax_liability.TestImages;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import org.flywaydb.core.Flyway;

/**
 * pgvector storage and nearest-neighbour search, against a real Postgres (W6 D4 Task 3).
 *
 * <h2>Deterministic by construction, not by tolerance</h2>
 *
 * <p>The seed vectors are axis-aligned unit vectors - {@code e0 = [1,0,0,...]},
 * {@code e1 = [0,1,0,...]} and so on. That makes every expected result derivable by hand rather
 * than measured: the cosine distance between two distinct axes is exactly 1, and between a vector
 * and itself exactly 0. So "the nearest neighbour of e0 is e0" is a statement about geometry, not
 * an observation about this model on this day.
 *
 * <p>No {@link java.util.Random}, no wall-clock, and <b>no call to the embedding service</b>. A
 * test that embedded real text would depend on a model's exact output floats, which change with
 * a model version, a quantisation setting or a hardware backend - and it would then need a
 * tolerance, at which point it stops asserting correctness and starts asserting "close enough to
 * whatever it did last time". The embedding service has its own contract test; this one is about
 * the schema, the index and the query.
 *
 * <h2>The index assertion is the point of the whole test</h2>
 *
 * <p>{@link #usesTheHnswIndexAndNotASeqScan()} exists because the failure it catches is
 * invisible: an operator-class mismatch between the index and the query does not error and does
 * not warn - the planner just stops using the index. The symptom appears months later as "search
 * got slow", which reads like a capacity problem.
 */
@Testcontainers
class TaxpayerEmbeddingsRepoIT {

    /**
     * No {@code withReuse(true)}, and that is a correction rather than an omission.
     *
     * <p>The first draft set it, reasoning that this container runs Flyway's whole migration set
     * on start and the suite re-runs often. It passed 11/11 in isolation and then failed the very
     * next full-suite run with {@code initializationError} - a {@link java.net.ConnectException}
     * from Flyway in {@code @BeforeAll}, because the reused container from the isolated run had
     * been reaped in between and left a stale reuse entry pointing at nothing.
     *
     * <p>That is the worst failure shape available: it depends on what ran before it, so it is
     * invisible when you run the class you are working on and appears only in the full suite. No
     * other integration test in this repository uses reuse, and buying a few seconds at the cost
     * of order-dependent flakiness is a bad trade anywhere - particularly in the one suite whose
     * entire selling point is that its results are derivable rather than observed.
     */
    @Container
    static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>(TestImages.POSTGRES);

    private static final String TENANT = "acme";
    private static final String OTHER_TENANT = "globex";

    private static JdbcTemplate jdbc;
    private TaxpayerEmbeddingRepository repository;

    @BeforeAll
    static void migrate() {
        DriverManagerDataSource ds = new DriverManagerDataSource(
                PG.getJdbcUrl(), PG.getUsername(), PG.getPassword());
        ds.setDriverClassName("org.postgresql.Driver");
        // The real migration set, not a hand-written CREATE TABLE: the point is to prove the
        // committed V5 works on a real server, including CREATE EXTENSION and both indexes. A
        // bespoke schema here would test a schema nothing ever deploys.
        //
        // NO .schemas("taxcalc"), deliberately - the application does not set it either (V1 runs
        // its own CREATE SCHEMA IF NOT EXISTS taxcalc, and spring.flyway has no schema property),
        // so setting it here would configure Flyway differently from every environment this
        // migration actually runs in. It was set in the first draft, and the divergence was not
        // cosmetic: it moved the vector extension into the taxcalc schema and broke all five
        // query-side tests with `type "vector" does not exist` while every schema-shape assertion
        // still passed. A test harness that configures the tool differently from production can
        // manufacture failures like that, and - worse - can hide the real ones.
        Flyway.configure()
                .dataSource(ds)
                .locations("classpath:db/migration")
                .load()
                .migrate();
        jdbc = new JdbcTemplate(ds);
    }

    /**
     * TRUNCATE rather than a transactional rollback: the HNSW index is a real structure that
     * INSERTs mutate, and a test that rolled back would leave each case reasoning about an index
     * built from rows the next case cannot see.
     */
    @BeforeEach
    void truncate() {
        jdbc.execute("TRUNCATE TABLE taxcalc.taxpayer_embeddings");
        repository = new TaxpayerEmbeddingRepository(jdbc);
    }

    // ------------------------------------------------------------ the schema

    @Test
    void migrationCreatesTheVectorExtension() {
        List<String> extensions = jdbc.queryForList(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'", String.class);

        assertThat(extensions).containsExactly("vector");
    }

    @Test
    void migrationCreatesBothIndexes() {
        List<String> indexes = jdbc.queryForList(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'taxpayer_embeddings'", String.class);

        assertThat(indexes).contains("taxpayer_embeddings_hnsw", "taxpayer_embeddings_tenant");
    }

    /**
     * The index's operator class must be {@code vector_cosine_ops}, because the query uses
     * {@code <=>}. Asserted from the catalog rather than trusted from the migration text: this is
     * the pairing whose mismatch is silent.
     */
    @Test
    void hnswIndexUsesCosineOperatorClass() {
        String definition = jdbc.queryForObject(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'taxpayer_embeddings_hnsw'", String.class);

        assertThat(definition).contains("hnsw").contains("vector_cosine_ops");
    }

    @Test
    void embeddingColumnIsExactly1024Dimensions() {
        // A vector of the wrong size is rejected by the column, which is the backstop behind
        // TaxpayerEmbedding's own dimension check.
        float[] tooShort = new float[512];
        assertThatThrownBy(() -> new TaxpayerEmbedding(UUID.randomUUID().toString(), TENANT, tooShort, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("1024");
    }

    // ------------------------------------------------------ store and search

    @Test
    void storesAndReadsBackTheExactVector() {
        float[] vector = axis(7);
        String id = UUID.randomUUID().toString();

        repository.save(new TaxpayerEmbedding(id, TENANT, vector, null));
        List<TaxpayerEmbedding> found = repository.findById(id);

        assertThat(found).hasSize(1);
        // Exact equality, not a tolerance: pgvector stores 4-byte floats and these values are
        // exactly representable, so anything other than an exact round-trip is a real bug.
        assertThat(found.get(0).embedding()).isEqualTo(vector);
        assertThat(found.get(0).tenantId()).isEqualTo(TENANT);
        assertThat(found.get(0).insertedAt()).isNotNull();
    }

    /**
     * Nearest-neighbour by construction: querying with {@code e3} must return the row that IS
     * {@code e3} first, at cosine distance exactly 0. Every other seeded axis is at distance
     * exactly 1, so the ordering is not a near-tie that could flip.
     */
    @Test
    void findsTheNearestNeighbourByConstruction() {
        for (int i = 0; i < 5; i++) {
            repository.save(new TaxpayerEmbedding(UUID.randomUUID().toString(), TENANT, axis(i), null));
        }

        List<TaxpayerEmbedding> nearest = repository.findNearest(TENANT, axis(3), 3);

        assertThat(nearest).hasSize(3);
        assertThat(nearest.get(0).embedding()).isEqualTo(axis(3));
    }

    /** The distance is exactly 0 and exactly 1 - stated as SQL so the geometry claim is checked. */
    @Test
    void cosineDistanceIsExactlyZeroToItselfAndOneToAnotherAxis() {
        Double toSelf = jdbc.queryForObject(
                "SELECT ?::vector <=> ?::vector",
                Double.class, literal(axis(0)), literal(axis(0)));
        Double toOtherAxis = jdbc.queryForObject(
                "SELECT ?::vector <=> ?::vector",
                Double.class, literal(axis(0)), literal(axis(1)));

        assertThat(toSelf).isEqualTo(0.0d);
        assertThat(toOtherAxis).isEqualTo(1.0d);
    }

    /**
     * Tenant isolation. HNSW indexes the vector column alone and knows nothing about tenants, so
     * without the WHERE clause this query would happily return another tenant's row as the
     * closest match - here, an exact match that the caller must not see.
     */
    @Test
    void nearestNeighbourNeverCrossesATenantBoundary() {
        repository.save(new TaxpayerEmbedding(UUID.randomUUID().toString(), OTHER_TENANT, axis(3), null));
        repository.save(new TaxpayerEmbedding(UUID.randomUUID().toString(), TENANT, axis(9), null));

        List<TaxpayerEmbedding> nearest = repository.findNearest(TENANT, axis(3), 5);

        assertThat(nearest).hasSize(1);
        assertThat(nearest).allSatisfy(row -> assertThat(row.tenantId()).isEqualTo(TENANT));
        // The exact match exists in the table and is deliberately not returned.
        assertThat(repository.count()).isEqualTo(2);
    }

    /**
     * The HNSW index must be USABLE by the cosine query - and must NOT be usable by an L2 query.
     *
     * <p>The first draft of this test seeded 300 rows and asserted the plan used the index with
     * planner settings left alone. It failed, and the failure was the test's fault rather than
     * the schema's: at 300 rows Postgres correctly costs a sort below an index scan, so the plan
     * was {@code Sort}, not {@code Seq Scan}, and not the index. Scaling the row count until the
     * planner changes its mind would make the assertion a statement about table size.
     *
     * <p>{@code enable_seqscan}/{@code enable_sort = off} are used here, and the original comment
     * arguing they "force the answer" was wrong. They are soft preferences, not overrides:
     * Postgres applies a large cost penalty but still falls back to a sequential scan when no
     * index can serve the query. So they remove the cost-based noise while leaving the
     * correctness signal intact - which is exactly what this test needs to isolate.
     *
     * <p>The {@code <->} case is the positive control that makes the {@code <=>} assertion mean
     * something. The index is built {@code vector_cosine_ops}; an L2 query cannot use it even
     * with seq scans penalised. Without this half, the test would pass just as happily against an
     * index built with the wrong operator class.
     */
    @Test
    void hnswIndexServesCosineQueriesAndNotL2Queries() {
        for (int i = 0; i < 300; i++) {
            repository.save(new TaxpayerEmbedding(UUID.randomUUID().toString(), TENANT, axis(i % 1024), null));
        }
        jdbc.execute("ANALYZE taxcalc.taxpayer_embeddings");

        String cosinePlan = explainWithSeqScanDiscouraged("<=>");
        String l2Plan = explainWithSeqScanDiscouraged("<->");

        assertThat(cosinePlan)
                .as("an operator-class mismatch between index and query is SILENT - the planner "
                        + "simply stops using the index. Cosine plan was:%n%s", cosinePlan)
                .contains("taxpayer_embeddings_hnsw");

        assertThat(l2Plan)
                .as("positive control: a vector_cosine_ops index must NOT serve an L2 (<->) "
                        + "query, or this test would pass against a wrongly-built index. "
                        + "L2 plan was:%n%s", l2Plan)
                .doesNotContain("taxpayer_embeddings_hnsw");
    }

    /**
     * {@code EXPLAIN} the nearest-neighbour query using {@code operator}, with sequential scans
     * and sorts penalised.
     *
     * <p>Runs every statement on ONE connection via a {@code ConnectionCallback}: {@code SET} is
     * session-scoped, and this suite's {@link DriverManagerDataSource} hands out a fresh
     * connection per operation, so issuing the SET through {@code jdbc.execute} would apply it to
     * a connection that is closed before the EXPLAIN runs - silently leaving the planner at its
     * defaults and making this test assert nothing.
     */
    private String explainWithSeqScanDiscouraged(String operator) {
        return jdbc.execute((org.springframework.jdbc.core.ConnectionCallback<String>) connection -> {
            try (java.sql.Statement st = connection.createStatement()) {
                st.execute("SET enable_seqscan = off");
                st.execute("SET enable_sort = off");
                StringBuilder plan = new StringBuilder();
                try (java.sql.ResultSet rs = st.executeQuery(
                        "EXPLAIN SELECT id FROM taxcalc.taxpayer_embeddings ORDER BY embedding "
                                + operator + " '" + literal(axis(5)) + "'::vector LIMIT 5")) {
                    while (rs.next()) {
                        plan.append(rs.getString(1)).append('\n');
                    }
                }
                return plan.toString();
            }
        });
    }

    // ------------------------------------------------------------- contracts

    @Test
    void rejectsAQueryVectorOfTheWrongDimension() {
        assertThatThrownBy(() -> repository.findNearest(TENANT, new float[3], 5))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("1024");
    }

    @Test
    void rejectsANonPositiveLimit() {
        assertThatThrownBy(() -> repository.findNearest(TENANT, axis(0), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("limit");
    }

    /**
     * A unit vector along dimension {@code i}: 1 at index {@code i}, 0 everywhere else. Distances
     * between these are exact, which is what makes every expectation in this suite hand-derivable.
     */
    private static float[] axis(int i) {
        float[] v = new float[TaxpayerEmbedding.DIMENSIONS];
        v[i % TaxpayerEmbedding.DIMENSIONS] = 1.0f;
        return v;
    }

    private static String literal(float[] v) {
        return TaxpayerEmbeddingRepository.toVectorLiteral(v);
    }
}
