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

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Service;

/**
 * Backs the {@code summarizeTaxpayer} GraphQL mutation (W3 D4 Task 3). A two-stage contract: Spring
 * AI's {@code .entity(TaxpayerSummary.class)} converter first coerces the LLM's JSON response into
 * the record (schema-shape via Jackson), then {@link #validate} re-checks the same record against a
 * hand-written JSON Schema in {@code resources/schemas/}. The two checks aren't redundant -
 * {@code .entity(...)} only guarantees the response deserializes into the record's field shape, not
 * that its values are sane (a made-up {@code riskBand} or a negative {@code jurisdictionCount} would
 * still pass); the JSON Schema step enforces the actual value constraints, so a future model release
 * that drifts those values fails loudly here instead of shipping a malformed summary.
 */
@Service
public class LlmSummaryService {

    private static final Logger LOG = LoggerFactory.getLogger(LlmSummaryService.class);

    private final ChatClient chatClient;
    private final TaxpayerReadModelRepository readModelRepository;
    private final ObjectMapper mapper;
    private final JsonSchema schema;

    public LlmSummaryService(ChatClient.Builder builder, TaxpayerReadModelRepository readModelRepository,
            ObjectMapper mapper) {
        Objects.requireNonNull(builder, "builder must not be null");
        this.readModelRepository =
                Objects.requireNonNull(readModelRepository, "readModelRepository must not be null");
        this.mapper = Objects.requireNonNull(mapper, "mapper must not be null");
        this.chatClient = builder.build();
        this.schema = loadSchema();
    }

    public TaxpayerSummary summarize(String id) {
        Objects.requireNonNull(id, "id must not be null");
        TaxpayerReadModel taxpayer = readModelRepository.findById(id)
                .orElseThrow(() -> new IllegalArgumentException("unknown id " + id));

        String prompt = "Summarise this taxpayer as a JSON object matching the TaxpayerSummary schema. "
                + "Output JSON only, no prose. Domain data: " + taxpayer;
        TaxpayerSummary summary = chatClient.prompt().user(prompt).call().entity(TaxpayerSummary.class);
        validate(summary);
        LOG.info("structured-output ok id={}", id);
        return summary;
    }

    private void validate(TaxpayerSummary candidate) {
        JsonNode node = mapper.valueToTree(candidate);
        Set<ValidationMessage> errors = schema.validate(node);
        if (!errors.isEmpty()) {
            LOG.warn("schema violation errors={}", errors);
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
