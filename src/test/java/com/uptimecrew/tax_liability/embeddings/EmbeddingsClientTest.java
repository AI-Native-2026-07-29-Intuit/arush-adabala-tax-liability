package com.uptimecrew.tax_liability.embeddings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;

/**
 * {@link EmbeddingsClient}'s parsing and validation, without standing up the service (W6 D4
 * Task 3).
 *
 * <p>{@link EmbeddingsClientLiveIT} covers the real TEI round-trip but skips wherever no service
 * is reachable - which is every CI run. That would leave the response-shape handling untested
 * exactly where it is most likely to regress unnoticed, so the parsing is package-private and
 * exercised here directly.
 *
 * <p>The shape being pinned: TEI's {@code /embed} returns a JSON array OF arrays, one vector per
 * input, so a single-input request still comes back nested one level deep. Reading that as a flat
 * array is the obvious mistake and produces a confusing downstream dimension error rather than a
 * parse error.
 */
class EmbeddingsClientTest {

    private static EmbeddingsClient client() {
        return new EmbeddingsClient(RestClient.builder(), new ObjectMapper(), "http://unused.invalid/embed");
    }

    @Test
    void parsesTeiNestedArrayResponse() {
        float[] vector = client().parseFirstVector("[[0.1, -0.2, 0.3]]");

        assertThat(vector).containsExactly(0.1f, -0.2f, 0.3f);
    }

    /** A batch response returns several vectors; this client asks for one and takes the first. */
    @Test
    void takesTheFirstVectorFromABatchResponse() {
        float[] vector = client().parseFirstVector("[[1.0, 2.0], [9.0, 9.0]]");

        assertThat(vector).containsExactly(1.0f, 2.0f);
    }

    @Test
    void rejectsAnEmptyOrMissingBody() {
        assertThatThrownBy(() -> client().parseFirstVector(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("empty body");
        assertThatThrownBy(() -> client().parseFirstVector("   "))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("empty body");
    }

    @Test
    void rejectsAResponseCarryingNoVectors() {
        assertThatThrownBy(() -> client().parseFirstVector("[]"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no vectors");
    }

    @Test
    void rejectsAMalformedBody() {
        assertThatThrownBy(() -> client().parseFirstVector("not json"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("could not parse");
    }

    @Test
    void rejectsNullOrBlankInputBeforeMakingAnyCall() {
        // Blank input is rejected before the HTTP call, so this passes despite the URL being
        // deliberately unroutable - which is also the assertion that the guard runs first.
        assertThrows(NullPointerException.class, () -> client().embed(null));
        assertThatThrownBy(() -> client().embed("   "))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("blank");
    }

    @Test
    void rejectsNullConstructorArguments() {
        assertThrows(NullPointerException.class,
                () -> new EmbeddingsClient(null, new ObjectMapper(), "http://x/embed"));
        assertThrows(NullPointerException.class,
                () -> new EmbeddingsClient(RestClient.builder(), null, "http://x/embed"));
        assertThrows(NullPointerException.class,
                () -> new EmbeddingsClient(RestClient.builder(), new ObjectMapper(), null));
    }
}
