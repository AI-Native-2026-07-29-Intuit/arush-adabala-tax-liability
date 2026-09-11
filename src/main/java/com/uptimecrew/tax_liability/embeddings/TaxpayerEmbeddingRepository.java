package com.uptimecrew.tax_liability.embeddings;

import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.StringJoiner;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

/**
 * Reads and writes {@code taxcalc.taxpayer_embeddings} (W6 D4 Task 3).
 *
 * <p><b>JdbcTemplate rather than Spring Data JPA</b>, unlike every other repository here.
 * Hibernate has no mapping for pgvector's {@code vector} type, so a JPA entity would need a
 * custom {@code UserType} to convert {@code float[]} in both directions - and the interesting
 * query is a nearest-neighbour {@code ORDER BY embedding <=> ?}, which has no JPQL spelling and
 * would end up as a native query regardless. The JPA route would therefore add a custom type
 * mapping in order to reach the same native SQL. This keeps the vector-shaped access in one
 * small, explicit class and leaves the rest of the application on JPA, where JPA earns its keep.
 *
 * <p>Vectors cross the wire as pgvector's text literal ({@code [0.1,0.2,...]}) cast with
 * {@code ?::vector}. The values are still bound as parameters, so this is not string-built SQL -
 * the cast tells Postgres how to read a parameter it would otherwise receive as untyped text.
 */
@Repository
public class TaxpayerEmbeddingRepository {

    private static final String INSERT = """
            INSERT INTO taxcalc.taxpayer_embeddings (id, tenant_id, embedding)
            VALUES (?::uuid, ?, ?::vector)
            """;

    /**
     * Tenant-scoped nearest-neighbour search.
     *
     * <p>{@code <=>} is cosine distance, and it MUST match the {@code vector_cosine_ops} operator
     * class the HNSW index was built with. A query using {@code <->} (L2) or {@code <#>} (inner
     * product) against a cosine index does not fail and does not warn - the planner silently
     * cannot use the index and falls back to a sequential scan, which looks like a capacity
     * problem as the table grows and is actually a one-character mismatch.
     *
     * <p>The {@code tenant_id} filter comes before the ranking for a reason beyond speed: HNSW
     * indexes the vector column alone and knows nothing about tenants, so without this predicate
     * a search returns whichever rows are nearest across every tenant in the table.
     */
    private static final String NEAREST = """
            SELECT id, tenant_id, embedding, inserted_at
            FROM taxcalc.taxpayer_embeddings
            WHERE tenant_id = ?
            ORDER BY embedding <=> ?::vector
            LIMIT ?
            """;

    private static final String FIND_BY_ID = """
            SELECT id, tenant_id, embedding, inserted_at
            FROM taxcalc.taxpayer_embeddings
            WHERE id = ?::uuid
            """;

    private final JdbcTemplate jdbc;

    /**
     * @param jdbc the template to run against; never null
     * @throws NullPointerException if {@code jdbc} is null
     */
    public TaxpayerEmbeddingRepository(JdbcTemplate jdbc) {
        this.jdbc = Objects.requireNonNull(jdbc, "jdbc must not be null");
    }

    /**
     * Insert one embedding.
     *
     * @param embedding the row to write; never null
     * @throws NullPointerException if {@code embedding} is null
     */
    public void save(TaxpayerEmbedding embedding) {
        Objects.requireNonNull(embedding, "embedding must not be null");
        jdbc.update(INSERT, embedding.id(), embedding.tenantId(), toVectorLiteral(embedding.embedding()));
    }

    /**
     * The {@code limit} nearest embeddings to {@code query} within {@code tenantId}, nearest
     * first by cosine distance.
     *
     * @param tenantId tenant to search within; never null or blank
     * @param query    the query vector; must be {@value TaxpayerEmbedding#DIMENSIONS} long
     * @param limit    maximum rows to return; must be positive
     * @return matching rows, nearest first; empty if the tenant has none
     * @throws NullPointerException     if {@code tenantId} or {@code query} is null
     * @throws IllegalArgumentException if {@code tenantId} is blank, {@code limit} is not
     *                                  positive, or {@code query} has the wrong dimension
     */
    public List<TaxpayerEmbedding> findNearest(String tenantId, float[] query, int limit) {
        Objects.requireNonNull(tenantId, "tenantId must not be null");
        Objects.requireNonNull(query, "query must not be null");
        if (tenantId.isBlank()) {
            throw new IllegalArgumentException("tenantId must not be blank");
        }
        if (limit <= 0) {
            throw new IllegalArgumentException("limit must be positive, was " + limit);
        }
        if (query.length != TaxpayerEmbedding.DIMENSIONS) {
            throw new IllegalArgumentException("query must have exactly "
                    + TaxpayerEmbedding.DIMENSIONS + " dimensions, was " + query.length);
        }
        return jdbc.query(NEAREST, MAPPER, tenantId, toVectorLiteral(query), limit);
    }

    /**
     * @param id the row id
     * @return the row, or an empty list if there is none
     */
    public List<TaxpayerEmbedding> findById(String id) {
        Objects.requireNonNull(id, "id must not be null");
        return jdbc.query(FIND_BY_ID, MAPPER, id);
    }

    /** Row count, for tests and diagnostics. */
    public long count() {
        Long n = jdbc.queryForObject("SELECT count(*) FROM taxcalc.taxpayer_embeddings", Long.class);
        return n == null ? 0L : n;
    }

    /**
     * Render a vector as pgvector's text literal, e.g. {@code [0.1,0.2]}.
     *
     * <p>Package-private so it can be unit-tested without a database.
     */
    static String toVectorLiteral(float[] vector) {
        StringJoiner joiner = new StringJoiner(",", "[", "]");
        for (float v : vector) {
            joiner.add(Float.toString(v));
        }
        return joiner.toString();
    }

    /**
     * Parse pgvector's text representation back into a {@code float[]}.
     *
     * <p>The driver hands the {@code vector} column over as its text form, since it is a type
     * the JDBC driver has no built-in mapping for.
     */
    static float[] fromVectorLiteral(String literal) {
        String body = literal.trim();
        if (body.startsWith("[") && body.endsWith("]")) {
            body = body.substring(1, body.length() - 1);
        }
        if (body.isEmpty()) {
            return new float[0];
        }
        String[] parts = body.split(",");
        List<Float> values = new ArrayList<>(parts.length);
        for (String part : parts) {
            values.add(Float.parseFloat(part.trim()));
        }
        float[] out = new float[values.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = values.get(i);
        }
        return out;
    }

    private static final RowMapper<TaxpayerEmbedding> MAPPER = (rs, rowNum) -> {
        Timestamp inserted = rs.getTimestamp("inserted_at");
        return new TaxpayerEmbedding(
                rs.getString("id"),
                rs.getString("tenant_id"),
                fromVectorLiteral(rs.getString("embedding")),
                inserted == null ? null : inserted.toInstant());
    };
}
