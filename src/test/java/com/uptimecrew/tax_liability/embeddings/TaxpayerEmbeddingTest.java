package com.uptimecrew.tax_liability.embeddings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.time.Instant;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * The {@link TaxpayerEmbedding} value contract (W6 D4 Task 3).
 *
 * <p>Pure unit tests - no container, no Spring. {@link TaxpayerEmbeddingsRepoIT} proves the
 * vector reaches Postgres and comes back; this proves the type refuses to be constructed wrong in
 * the first place, which is the cheaper place to catch a model swap.
 */
class TaxpayerEmbeddingTest {

    private static final String ID = UUID.randomUUID().toString();

    private static float[] validVector() {
        float[] v = new float[TaxpayerEmbedding.DIMENSIONS];
        v[0] = 1.0f;
        return v;
    }

    @Test
    void constructsAndExposesEveryField() {
        Instant at = Instant.ofEpochMilli(1_700_000_000_000L);

        TaxpayerEmbedding embedding = new TaxpayerEmbedding(ID, "acme", validVector(), at);

        assertThat(embedding.id()).isEqualTo(ID);
        assertThat(embedding.tenantId()).isEqualTo("acme");
        assertThat(embedding.embedding()).hasSize(TaxpayerEmbedding.DIMENSIONS);
        assertThat(embedding.insertedAt()).isEqualTo(at);
    }

    /**
     * The dimension check is the one that earns its keep: a wrong-sized vector almost always means
     * the embedding service was swapped for a model with different output geometry, and the
     * database's own error names a column and a type rather than the cause.
     */
    @ParameterizedTest(name = "a {0}-dimension vector is rejected")
    @ValueSource(ints = {0, 1, 512, 768, 1023, 1025})
    void rejectsAnyVectorThatIsNot1024Dimensions(int dimensions) {
        float[] wrong = new float[dimensions];

        assertThatThrownBy(() -> new TaxpayerEmbedding(ID, "acme", wrong, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("1024")
                .hasMessageContaining("bge-large-en-v1.5");
    }

    @Test
    void rejectsNullFields() {
        assertThrows(NullPointerException.class,
                () -> new TaxpayerEmbedding(null, "acme", validVector(), null));
        assertThrows(NullPointerException.class,
                () -> new TaxpayerEmbedding(ID, null, validVector(), null));
        assertThrows(NullPointerException.class,
                () -> new TaxpayerEmbedding(ID, "acme", null, null));
    }

    @Test
    void rejectsBlankIdAndTenant() {
        assertThatThrownBy(() -> new TaxpayerEmbedding("  ", "acme", validVector(), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("id");
        assertThatThrownBy(() -> new TaxpayerEmbedding(ID, "  ", validVector(), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("tenantId");
    }

    /**
     * The record's generated accessor would hand out the internal array. Both the constructor and
     * the accessor copy, so a caller cannot mutate a stored vector - which would otherwise change
     * a row's meaning after it was written, silently and at a distance.
     */
    @Test
    void isImmutableOnBothTheWayInAndTheWayOut() {
        float[] source = validVector();
        TaxpayerEmbedding embedding = new TaxpayerEmbedding(ID, "acme", source, null);

        source[0] = 99.0f;                       // mutate the caller's array after construction
        assertThat(embedding.embedding()[0]).isEqualTo(1.0f);

        embedding.embedding()[0] = 42.0f;        // mutate what the accessor handed back
        assertThat(embedding.embedding()[0]).isEqualTo(1.0f);
    }

    /**
     * The generated {@code equals} compares arrays by identity, so two records holding equal
     * vectors would not be equal. Overridden to compare by content, which is what a cache lookup
     * or an assertion expects.
     */
    @Test
    void equalityComparesVectorsByContentNotIdentity() {
        TaxpayerEmbedding a = new TaxpayerEmbedding(ID, "acme", validVector(), null);
        TaxpayerEmbedding b = new TaxpayerEmbedding(ID, "acme", validVector(), null);

        assertThat(a).isEqualTo(b).hasSameHashCodeAs(b);
        assertThat(a).isEqualTo(a);
        assertThat(a).isNotEqualTo(null).isNotEqualTo("not an embedding");
    }

    @Test
    void differsWhenAnyFieldDiffers() {
        TaxpayerEmbedding base = new TaxpayerEmbedding(ID, "acme", validVector(), null);
        float[] other = new float[TaxpayerEmbedding.DIMENSIONS];
        other[5] = 1.0f;

        assertThat(base).isNotEqualTo(new TaxpayerEmbedding(UUID.randomUUID().toString(), "acme", validVector(), null));
        assertThat(base).isNotEqualTo(new TaxpayerEmbedding(ID, "globex", validVector(), null));
        assertThat(base).isNotEqualTo(new TaxpayerEmbedding(ID, "acme", other, null));
        assertThat(base).isNotEqualTo(new TaxpayerEmbedding(ID, "acme", validVector(), Instant.EPOCH));
    }

    /** 1024 floats in a log line helps nobody. */
    @Test
    void toStringSummarisesTheVectorRatherThanPrintingIt() {
        String text = new TaxpayerEmbedding(ID, "acme", validVector(), null).toString();

        assertThat(text).contains(ID).contains("acme").contains("<1024 dims>");
        assertThat(text.length()).isLessThan(200);
    }
}
