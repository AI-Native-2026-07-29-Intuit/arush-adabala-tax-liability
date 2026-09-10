package com.uptimecrew.tax_liability.embeddings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Assumptions;
import org.springframework.web.client.RestClient;

/**
 * {@link EmbeddingsClient} against a real Hugging Face TEI service (W6 D4 Task 3 prep).
 *
 * <p>{@link TaxpayerEmbeddingsRepoIT} deliberately makes no embedding call - its vectors are
 * axis-aligned so the geometry is hand-derivable. That leaves exactly one thing untested: whether
 * this client can read what TEI actually returns. TEI's {@code /embed} answers with a JSON array
 * OF arrays (one vector per input), so a single-input request comes back nested one level deep -
 * a shape easy to get wrong and impossible to catch without the real service.
 *
 * <p><b>Skipped unless a TEI service is reachable</b> at {@code taxcalc.embeddings.url} (default
 * {@code http://localhost:8088/embed}), so this never fails a build on a machine that has no
 * embedding service running.
 *
 * <p>Standing one up locally, without the Hub - see README, "Running TEI behind a TLS-intercepting
 * proxy":
 * <pre>{@code
 * docker run -p 8088:80 -v "$HOME/tei-models:/models:ro" \
 *   ghcr.io/huggingface/text-embeddings-inference:cpu-1.5 --model-id /models/bge-large-en-v1.5
 * }</pre>
 */
class EmbeddingsClientLiveIT {

    private static final String EMBED_URL =
            System.getProperty("taxcalc.embeddings.url", "http://localhost:8088/embed");

    @BeforeAll
    static void requireService() {
        boolean reachable;
        try {
            HttpResponse<String> response = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(2))
                    .build()
                    .send(HttpRequest.newBuilder(URI.create(EMBED_URL.replace("/embed", "/health")))
                            .timeout(Duration.ofSeconds(2))
                            .GET()
                            .build(), HttpResponse.BodyHandlers.ofString());
            reachable = response.statusCode() == 200;
        } catch (Exception ex) {
            reachable = false;
        }
        Assumptions.assumeTrue(reachable,
                "no TEI embeddings service at " + EMBED_URL + " - skipping the live embedding test");
    }

    private static EmbeddingsClient client() {
        return new EmbeddingsClient(RestClient.builder(), new ObjectMapper(), EMBED_URL);
    }

    /**
     * The dimension assertion is the one that matters: {@value TaxpayerEmbedding#DIMENSIONS} is
     * fixed in the {@code vector(1024)} column at DDL time, so a model whose output geometry
     * differs cannot be stored at all. Catching that here names the cause; catching it at the
     * INSERT names a column and a type.
     */
    @Test
    void embedsTextIntoA1024DimensionVector() {
        float[] vector = client().embed("AGI 120000, single filer, standard deduction");

        assertThat(vector).hasSize(TaxpayerEmbedding.DIMENSIONS);

        boolean anyNonZero = false;
        boolean allInUnitRange = true;
        for (float v : vector) {
            anyNonZero |= v != 0.0f;
            allInUnitRange &= v >= -1.0f && v <= 1.0f;
        }
        // A real embedding is not all zeros; an all-zero vector would sail through the dimension
        // check and then sit at cosine distance 1 from everything, ranking arbitrarily.
        assertThat(anyNonZero).as("embedding must not be all zeros").isTrue();
        // bge-large-en-v1.5 returns L2-normalised vectors, so every component is within [-1, 1].
        assertThat(allInUnitRange).as("every component of an L2-normalised vector is in [-1, 1]").isTrue();
    }

    /**
     * The vector is usable as-is by the repository - which is the actual contract between these
     * two classes, and the reason this test lives next to the repo test rather than in isolation.
     */
    @Test
    void producedVectorIsAcceptedByTheDomainType() {
        float[] vector = client().embed("Schedule C net profit 48000, SE tax applies");

        TaxpayerEmbedding embedding =
                new TaxpayerEmbedding(java.util.UUID.randomUUID().toString(), "acme", vector, null);

        assertThat(embedding.embedding()).hasSize(TaxpayerEmbedding.DIMENSIONS);
    }

    /** Semantically closer texts must score closer - a sanity check on the model, not the plumbing. */
    @Test
    void semanticallySimilarTextsAreCloserThanUnrelatedOnes() {
        EmbeddingsClient client = client();
        float[] a = client.embed("taxpayer owes federal income tax on wage income");
        float[] b = client.embed("federal income tax liability on salary earnings");
        float[] unrelated = client.embed("the migratory patterns of arctic terns");

        assertThat(cosineDistance(a, b))
                .as("two paraphrases of the same tax concept should be closer than an unrelated sentence")
                .isLessThan(cosineDistance(a, unrelated));
    }

    @Test
    void rejectsBlankInput() {
        assertThatThrownBy(() -> client().embed("  "))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("blank");
    }

    private static double cosineDistance(float[] x, float[] y) {
        double dot = 0;
        double nx = 0;
        double ny = 0;
        for (int i = 0; i < x.length; i++) {
            dot += (double) x[i] * y[i];
            nx += (double) x[i] * x[i];
            ny += (double) y[i] * y[i];
        }
        return 1.0 - dot / (Math.sqrt(nx) * Math.sqrt(ny));
    }
}
