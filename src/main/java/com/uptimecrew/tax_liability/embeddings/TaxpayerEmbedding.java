package com.uptimecrew.tax_liability.embeddings;

import java.time.Instant;
import java.util.Objects;

/**
 * One stored taxpayer embedding (W6 D4 Task 3): a 1024-dimension vector, the tenant it belongs
 * to, and when it was written.
 *
 * <p>The vector is a {@code float[]} rather than a {@code double[]} or a list of
 * {@link java.math.BigDecimal}, and that is not a violation of the project's no-floating-point
 * money rule - this is not money. It is the native storage type: pgvector stores {@code real}
 * (4-byte float) components, so a wider Java type would be converted down on write and would
 * only create the impression of precision the database does not keep. Distances computed from
 * these values rank results; they are never summed into a total anybody is charged.
 *
 * @param id         UUID primary key, as a String per the project's identifier convention
 * @param tenantId   owning tenant; every read path filters on this before ranking by distance
 * @param embedding  the 1024-dimension vector
 * @param insertedAt when the row was written, or null for an instance not yet persisted
 */
public record TaxpayerEmbedding(String id, String tenantId, float[] embedding, Instant insertedAt) {

    /** The dimension the {@code vector(1024)} column and bge-large-en-v1.5 both fix. */
    public static final int DIMENSIONS = 1024;

    /**
     * @throws NullPointerException     if {@code id}, {@code tenantId} or {@code embedding} is null
     * @throws IllegalArgumentException if {@code id} or {@code tenantId} is blank, or the vector
     *                                  is not exactly {@value #DIMENSIONS} long. The dimension is
     *                                  checked here rather than left to Postgres because the
     *                                  database's error names a column and a type, not the model
     *                                  that produced a wrong-sized vector - and a dimension
     *                                  mismatch almost always means the embedding service was
     *                                  swapped for one with different output geometry.
     */
    public TaxpayerEmbedding {
        Objects.requireNonNull(id, "id must not be null");
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(embedding, "embedding must not be null");
        if (id.isBlank()) {
            throw new IllegalArgumentException("id must not be blank");
        }
        if (tenantId.isBlank()) {
            throw new IllegalArgumentException("tenantId must not be blank");
        }
        if (embedding.length != DIMENSIONS) {
            throw new IllegalArgumentException(
                    "embedding must have exactly " + DIMENSIONS + " dimensions, was " + embedding.length
                            + " - the vector(1024) column matches bge-large-en-v1.5's output geometry");
        }
        embedding = embedding.clone();
    }

    /**
     * {@inheritDoc}
     *
     * <p>Overridden because the record's generated accessor would hand out the internal array,
     * letting a caller mutate a value that is supposed to be immutable.
     */
    @Override
    public float[] embedding() {
        return embedding.clone();
    }

    /**
     * {@inheritDoc}
     *
     * <p>The generated {@code equals} compares arrays by identity, so two records holding equal
     * vectors would not be equal. Overridden together with {@link #hashCode()} to compare by
     * content, which is what every test and every cache lookup expects.
     */
    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof TaxpayerEmbedding other)) {
            return false;
        }
        return id.equals(other.id)
                && tenantId.equals(other.tenantId)
                && java.util.Arrays.equals(embedding, other.embedding)
                && Objects.equals(insertedAt, other.insertedAt);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, tenantId, java.util.Arrays.hashCode(embedding), insertedAt);
    }

    /** Deliberately does not print 1024 floats. */
    @Override
    public String toString() {
        return "TaxpayerEmbedding[id=" + id + ", tenantId=" + tenantId
                + ", embedding=<" + embedding.length + " dims>, insertedAt=" + insertedAt + "]";
    }
}
