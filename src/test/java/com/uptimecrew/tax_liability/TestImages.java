package com.uptimecrew.tax_liability;

import org.testcontainers.utility.DockerImageName;

/**
 * Container images shared by every Testcontainers-backed test (W6 D4 Task 3).
 *
 * <p>Introduced because {@code V5__create_taxpayer_embeddings.sql} made the Postgres image a
 * correctness property rather than a preference, and it was previously a string literal repeated
 * in eight test classes. Flyway runs the full migration set on every one of those containers, so
 * the day the image and the migrations disagree, all eight fail at once with a message about an
 * extension - and eight places have to be found and changed together.
 */
public final class TestImages {

    /**
     * Postgres for every integration test.
     *
     * <p><b>pgvector/pgvector:pg16, not postgres:16-alpine.</b> This is not a preference: V5 runs
     * {@code CREATE EXTENSION IF NOT EXISTS vector}, and a stock Postgres image does not ship the
     * extension. Measured, not assumed - on postgres:16-alpine the migration fails with
     * {@code ERROR: extension "vector" is not available ... Could not open extension control file
     * .../vector.control}, and every full-context test fails at ApplicationContext startup rather
     * than in an assertion, which makes the cause much less obvious than the count of failures
     * suggests.
     *
     * <p>Note that {@code IF NOT EXISTS} does not help here and is worth understanding: it
     * suppresses the error when the extension is already <em>created</em>, not when it is not
     * <em>installed</em> on the server. The files either exist on disk or they do not.
     *
     * <p>The image is the same PostgreSQL 16, plus the extension - so this moves the test
     * database TOWARD production (RDS Postgres, where pgvector is available) rather than away
     * from it. The cost is a larger image than the alpine variant; the thing bought is that
     * `ddl-auto: validate` and the migration set are now exercised against a database that can
     * actually run them.
     *
     * <p>{@code asCompatibleSubstituteFor("postgres")} is required: Testcontainers checks the
     * image name against the module's expected name and refuses an unrecognised one outright, so
     * without it this fails at container start with a name-mismatch error rather than anything
     * about vectors.
     */
    public static final DockerImageName POSTGRES =
            DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres");

    private TestImages() {
        throw new AssertionError("TestImages is not instantiable");
    }
}
