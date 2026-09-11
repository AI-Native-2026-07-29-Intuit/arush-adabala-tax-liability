package com.uptimecrew.tax_liability.embeddings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

/**
 * {@link TaxpayerEmbeddingRepository}'s vector-literal conversion and argument validation, without
 * a database (W6 D4 Task 3).
 *
 * <p>{@link TaxpayerEmbeddingsRepoIT} covers the SQL against real Postgres. What it cannot cover
 * cheaply is the guard clauses - each needs a deliberately-wrong call - and the text conversion
 * both directions, which is where a rounding or formatting mistake would silently corrupt a stored
 * vector rather than fail.
 */
class TaxpayerEmbeddingRepositoryTest {

    private static TaxpayerEmbeddingRepository repository() {
        // Never executed against: every case here is rejected by a guard before any SQL runs.
        return new TaxpayerEmbeddingRepository(new JdbcTemplate());
    }

    private static float[] validVector() {
        return new float[TaxpayerEmbedding.DIMENSIONS];
    }

    @Test
    void rendersPgvectorTextLiteral() {
        assertThat(TaxpayerEmbeddingRepository.toVectorLiteral(new float[] {0.1f, -0.2f, 3.0f}))
                .isEqualTo("[0.1,-0.2,3.0]");
        assertThat(TaxpayerEmbeddingRepository.toVectorLiteral(new float[0])).isEqualTo("[]");
    }

    @Test
    void parsesPgvectorTextLiteral() {
        assertThat(TaxpayerEmbeddingRepository.fromVectorLiteral("[0.1,-0.2,3.0]"))
                .containsExactly(0.1f, -0.2f, 3.0f);
        assertThat(TaxpayerEmbeddingRepository.fromVectorLiteral("[]")).isEmpty();
        // Whitespace and a missing wrapper both occur across driver/pgvector versions.
        assertThat(TaxpayerEmbeddingRepository.fromVectorLiteral("  [1.0, 2.0]  "))
                .containsExactly(1.0f, 2.0f);
        assertThat(TaxpayerEmbeddingRepository.fromVectorLiteral("1.0,2.0"))
                .containsExactly(1.0f, 2.0f);
    }

    /**
     * The round trip must be exact. pgvector stores 4-byte floats, so anything that went through a
     * double and back could differ in the last bit - and a silently-altered vector changes search
     * ranking without ever failing.
     */
    @Test
    void literalRoundTripIsExact() {
        float[] original = {0.0f, 1.0f, -1.0f, 0.123456f, 1e-8f, -3.4028235e38f};

        float[] round = TaxpayerEmbeddingRepository.fromVectorLiteral(
                TaxpayerEmbeddingRepository.toVectorLiteral(original));

        assertThat(round).isEqualTo(original);
    }

    @Test
    void rejectsAQueryVectorOfTheWrongDimension() {
        assertThatThrownBy(() -> repository().findNearest("acme", new float[7], 5))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("1024");
    }

    @Test
    void rejectsANonPositiveLimit() {
        assertThatThrownBy(() -> repository().findNearest("acme", validVector(), 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("limit");
        assertThatThrownBy(() -> repository().findNearest("acme", validVector(), -1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("limit");
    }

    @Test
    void rejectsABlankTenant() {
        assertThatThrownBy(() -> repository().findNearest("  ", validVector(), 5))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("tenantId");
    }

    @Test
    void rejectsNullArguments() {
        assertThrows(NullPointerException.class, () -> repository().findNearest(null, validVector(), 5));
        assertThrows(NullPointerException.class, () -> repository().findNearest("acme", null, 5));
        assertThrows(NullPointerException.class, () -> repository().save(null));
        assertThrows(NullPointerException.class, () -> repository().findById(null));
        assertThrows(NullPointerException.class, () -> new TaxpayerEmbeddingRepository(null));
    }
}
