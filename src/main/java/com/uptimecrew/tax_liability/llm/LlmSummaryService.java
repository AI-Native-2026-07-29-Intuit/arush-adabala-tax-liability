package com.uptimecrew.tax_liability.llm;

import java.io.IOException;
import java.io.InputStream;
import java.util.Objects;
import java.util.Set;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion.VersionFlag;
import com.networknt.schema.ValidationMessage;
import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Scope;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

/**
 * Backs the {@code summarizeTaxpayer} GraphQL mutation (W3 D4 Task 3). A two-stage contract: the
 * raw {@link ChatResponse} content is parsed into the record (schema-shape via Jackson), then
 * {@link #validate} re-checks the same record against a hand-written JSON Schema in
 * {@code resources/schemas/}. The two checks aren't redundant - parsing only guarantees the
 * response deserializes into the record's field shape, not that its values are sane (a made-up
 * {@code riskBand} or a negative {@code jurisdictionCount} would still pass); the JSON Schema step
 * enforces the actual value constraints, so a future model release that drifts those values fails
 * loudly here instead of shipping a malformed summary.
 *
 * <p>W3 D5 Task 3 wraps the LLM call in a manual {@code llm.summarize} span (kept manual rather
 * than relying on auto-instrumentation, since Spring AI's Anthropic client isn't auto-instrumented
 * by the OTel Spring Boot starter) so the segment is visible in Jaeger as a child of whatever
 * resolver/controller span is current, carrying {@code llm.model} / {@code llm.tokens.in} /
 * {@code llm.tokens.out} attributes for cost attribution.
 */
@Service
public class LlmSummaryService {

    private static final Logger LOG = LoggerFactory.getLogger(LlmSummaryService.class);

    private static final AttributeKey<String> ATTR_MODEL = AttributeKey.stringKey("llm.model");
    private static final AttributeKey<String> ATTR_AGGREGATE_ID = AttributeKey.stringKey("llm.input.aggregate_id");
    private static final AttributeKey<Long> ATTR_TOKENS_IN = AttributeKey.longKey("llm.tokens.in");
    private static final AttributeKey<Long> ATTR_TOKENS_OUT = AttributeKey.longKey("llm.tokens.out");

    private final ChatClient chatClient;
    private final TaxpayerReadModelRepository readModelRepository;
    private final ObjectMapper mapper;
    private final JsonSchema schema;
    private final Tracer tracer;
    private final String configuredModel;

    public LlmSummaryService(ChatClient.Builder builder, TaxpayerReadModelRepository readModelRepository,
            ObjectMapper mapper, OpenTelemetry openTelemetry,
            @Value("${spring.ai.anthropic.chat.options.model}") String configuredModel) {
        Objects.requireNonNull(builder, "builder must not be null");
        this.readModelRepository =
                Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
        this.mapper = Objects.requireNonNull(mapper, "mapper must not be null");
        Objects.requireNonNull(openTelemetry, "openTelemetry must not be null");
        this.configuredModel = Objects.requireNonNull(configuredModel, "configuredModel must not be null");
        this.chatClient = builder.build();
        this.schema = loadSchema();
        this.tracer = openTelemetry.getTracer("com.uptimecrew.tax_liability.llm");
    }

    public TaxpayerSummary summarize(String id) {
        Objects.requireNonNull(id, "id must not be null");
        TaxpayerReadModel taxpayer = readModelRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("unknown id " + id));

        String prompt = "Summarise this taxpayer as a JSON object matching the TaxpayerSummary schema. "
                + "Output JSON only, no prose. Domain data: " + taxpayer;

        Span span = tracer.spanBuilder("llm.summarize")
                .setSpanKind(SpanKind.CLIENT)
                .setAttribute(ATTR_MODEL, configuredModel)
                .setAttribute(ATTR_AGGREGATE_ID, id)
                .startSpan();
        try (Scope ignored = span.makeCurrent()) {
            ChatResponse response = chatClient.prompt().user(prompt).call().chatResponse();
            String text = response.getResult().getOutput().getText();
            TaxpayerSummary result = text == null ? null : mapper.readValue(text, TaxpayerSummary.class);

            // Spring AI surfaces token counts on ChatResponse.getMetadata().getUsage().
            // If the provider didn't return usage, both values come back as 0L.
            long promptTokens = safeLong(response.getMetadata().getUsage().getPromptTokens());
            long completionTokens = safeLong(response.getMetadata().getUsage().getCompletionTokens());
            span.setAttribute(ATTR_TOKENS_IN, promptTokens);
            span.setAttribute(ATTR_TOKENS_OUT, completionTokens);

            validate(result);
            span.setStatus(StatusCode.OK);
            LOG.info("structured-output ok id={} model={} tokens.in={} tokens.out={}",
                    id, configuredModel, promptTokens, completionTokens);
            return result;
        } catch (RuntimeException ex) {
            span.recordException(ex);
            span.setStatus(StatusCode.ERROR, ex.getClass().getSimpleName());
            throw ex;
        } catch (Exception ex) {
            span.recordException(ex);
            span.setStatus(StatusCode.ERROR, ex.getClass().getSimpleName());
            throw new IllegalStateException("LLM call failed", ex);
        } finally {
            span.end();
        }
    }

    private static long safeLong(Number n) {
        return n == null ? 0L : n.longValue();
    }

    private void validate(TaxpayerSummary candidate) {
        JsonNode node = mapper.valueToTree(candidate);
        Set<ValidationMessage> errors = schema.validate(node);
        if (!errors.isEmpty()) {
            LOG.warn("structured-output schema violation errors={}", errors);
            throw new IllegalStateException("LLM output failed JSON Schema validation: " + errors);
        }
    }

    private JsonSchema loadSchema() {
        try (InputStream in = new ClassPathResource("schemas/TaxpayerSummary.schema.json").getInputStream()) {
            return JsonSchemaFactory.getInstance(VersionFlag.V202012).getSchema(in);
        } catch (IOException ex) {
            throw new IllegalStateException("failed to load TaxpayerSummary schema", ex);
        }
    }
}
