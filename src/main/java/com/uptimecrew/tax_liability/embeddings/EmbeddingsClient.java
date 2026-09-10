package com.uptimecrew.tax_liability.embeddings;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * Turns taxpayer text into a 1024-dimension vector, using the self-hosted Hugging Face Text
 * Embeddings Inference service running in the cluster (W6 D4 Task 3).
 *
 * <p><b>This is the one part of the AI stack that costs nothing per call.</b> No API key, no AWS
 * service, no third party - {@code bge-large-en-v1.5} runs on the cluster's own CPU, so the
 * marginal cost of an embedding is compute the cluster is already paying for. That is the
 * contrast worth holding next to
 * {@link com.uptimecrew.tax_liability.llm.cost.CostMiddleware}: the LLM plane needs per-request
 * cost accounting because each call bills an external party, while this path needs capacity
 * planning instead. Same "AI feature" label, entirely different cost shape, and only one of them
 * belongs in the cost log.
 *
 * <p>It is also the reason the pgvector column is {@code vector(1024)}: that is this model's
 * output geometry, fixed at DDL time. Swapping to a model with a different dimension is a new
 * column and a backfill, not a config change.
 */
@Component
public class EmbeddingsClient {

    private static final Logger LOG = LoggerFactory.getLogger(EmbeddingsClient.class);

    private final RestClient restClient;
    private final ObjectMapper mapper;
    private final String embedUrl;

    /**
     * @param builder  RestClient builder; never null
     * @param mapper   JSON mapper; never null
     * @param embedUrl the TEI {@code /embed} endpoint. Defaults to the in-cluster Service DNS
     *                 name; overridden by {@code EMBEDDINGS_URL} in the Deployment.
     * @throws NullPointerException if any argument is null
     */
    public EmbeddingsClient(RestClient.Builder builder, ObjectMapper mapper,
            @Value("${taxcalc.embeddings.url:http://embeddings.taxcalc-dev.svc.cluster.local/embed}")
            String embedUrl) {
        Objects.requireNonNull(builder, "builder must not be null");
        this.mapper = Objects.requireNonNull(mapper, "mapper must not be null");
        this.embedUrl = Objects.requireNonNull(embedUrl, "embedUrl must not be null");
        this.restClient = builder.build();
    }

    /**
     * Embed one piece of text.
     *
     * <p>TEI's {@code /embed} takes {@code {"inputs": "..."}} and returns a JSON array of arrays -
     * one vector per input - so a single-input request still comes back nested one level deep.
     *
     * @param text the text to embed; never null or blank
     * @return a {@value TaxpayerEmbedding#DIMENSIONS}-dimension vector
     * @throws NullPointerException     if {@code text} is null
     * @throws IllegalArgumentException if {@code text} is blank
     * @throws IllegalStateException    if the service returns no vector, or one whose dimension
     *                                  does not match the {@code vector(1024)} column. The
     *                                  dimension is checked here rather than at the INSERT
     *                                  because the database's error names a column and a type,
     *                                  which does not point at the actual cause - a swapped
     *                                  model.
     */
    public float[] embed(String text) {
        Objects.requireNonNull(text, "text must not be null");
        if (text.isBlank()) {
            throw new IllegalArgumentException("text must not be blank");
        }

        String body = restClient.post()
                .uri(embedUrl)
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of("inputs", text))
                .retrieve()
                .body(String.class);

        float[] vector = parseFirstVector(body);
        if (vector.length != TaxpayerEmbedding.DIMENSIONS) {
            throw new IllegalStateException("embeddings service returned " + vector.length
                    + " dimensions, expected " + TaxpayerEmbedding.DIMENSIONS
                    + " - the taxpayer_embeddings.embedding column is vector("
                    + TaxpayerEmbedding.DIMENSIONS + "), matching bge-large-en-v1.5. A different "
                    + "dimension means the model was changed, which needs a migration, not a retry.");
        }
        LOG.debug("embedded chars={} dims={}", text.length(), vector.length);
        return vector;
    }

    /**
     * Extract the first vector from TEI's nested-array response.
     *
     * <p>Package-private so the parsing is testable without standing the service up.
     */
    float[] parseFirstVector(String json) {
        if (json == null || json.isBlank()) {
            throw new IllegalStateException("embeddings service returned an empty body");
        }
        try {
            List<List<Double>> vectors = mapper.readValue(json, new com.fasterxml.jackson.core.type.TypeReference<>() { });
            if (vectors.isEmpty()) {
                throw new IllegalStateException("embeddings service returned no vectors");
            }
            List<Double> first = vectors.get(0);
            float[] out = new float[first.size()];
            for (int i = 0; i < out.length; i++) {
                out[i] = first.get(i).floatValue();
            }
            return out;
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("could not parse embeddings response", ex);
        }
    }
}
