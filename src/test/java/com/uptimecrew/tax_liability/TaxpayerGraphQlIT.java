package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.InputStream;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Set;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion.VersionFlag;
import com.networknt.schema.ValidationMessage;
import com.uptimecrew.tax_liability.graphql.TaxpayerSummary;
import com.uptimecrew.tax_liability.llm.StubChatClientFactory;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel.EmbeddedLiability;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.graphql.tester.AutoConfigureGraphQlTester;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.core.io.ClassPathResource;
import org.springframework.graphql.test.tester.GraphQlTester;
import org.springframework.test.context.ActiveProfiles;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.junit.jupiter.Container;

/**
 * Proves the W3 D4 GraphQL deliverable end to end against a real Spring for GraphQL server: a
 * plain query (Task 1), the {@code @BatchMapping} N+1 fix (Task 2), and the structured-output
 * mutation re-validated against the JSON Schema (Task 3). {@link StubChatModelConfig} below swaps
 * in {@link StubChatClientFactory}'s deterministic {@code ChatModel} so {@code summarizeTaxpayer}
 * still runs Spring AI's real structured-output conversion but never calls Anthropic.
 */
@SpringBootTest
@AutoConfigureGraphQlTester
@ActiveProfiles("test")
class TaxpayerGraphQlIT extends AbstractPostgresIT {

    private static final int SEED_COUNT = 5;

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    // TaxLiabilityService.findById (behind the `taxpayer` query) is @Cacheable against Redis.
    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Autowired
    GraphQlTester graphQlTester;

    @Autowired
    TaxpayerReadModelRepository readModelRepository;

    @Autowired
    ObjectMapper mapper;

    @BeforeEach
    void seedTaxpayers() {
        for (int i = 1; i <= SEED_COUNT; i++) {
            readModelRepository.save(new TaxpayerReadModel(
                    "seeded-id-" + i, "Taxpayer " + i, "SINGLE", "FEDERAL",
                    Instant.now().minusSeconds(SEED_COUNT - i),
                    List.of(new EmbeddedLiability(2026, "fed-2026-10pct",
                            new BigDecimal("9500.00"), new BigDecimal("950.00"), Instant.now()))));
        }
    }

    @AfterEach
    void cleanUp() {
        readModelRepository.deleteAll();
    }

    @Test
    void query_taxpayer_returnsSeedDocument() {
        graphQlTester.document("query { taxpayer(id: \"seeded-id-1\") { id } }")
                .execute()
                .path("taxpayer.id").entity(String.class).isEqualTo("seeded-id-1");
    }

    @Test
    void batchMapping_resolves_lines_inOneRound() {
        graphQlTester.document("query { latestTaxpayers(limit: 5) { id lines { id } } }")
                .execute()
                .path("latestTaxpayers").entityList(Object.class).hasSize(SEED_COUNT);
    }

    @Test
    void summarizeTaxpayer_returnsStructuredOutput_andMatchesSchema() throws Exception {
        TaxpayerSummary summary = graphQlTester
                .document("mutation { summarizeTaxpayer(id: \"seeded-id-1\") { "
                        + "filingStatus totalLiability jurisdictionCount riskBand } }")
                .execute()
                .path("summarizeTaxpayer").entity(TaxpayerSummary.class).get();

        JsonNode node = mapper.valueToTree(summary);
        try (InputStream in = new ClassPathResource("schemas/TaxpayerSummary.schema.json").getInputStream()) {
            Set<ValidationMessage> errors =
                    JsonSchemaFactory.getInstance(VersionFlag.V202012).getSchema(in).validate(node);
            assertThat(errors).isEmpty();
        }
    }

    /** Overrides the app's real Anthropic-backed {@link ChatClient.Builder} with a stub for this test. */
    @TestConfiguration
    static class StubChatModelConfig {

        @Bean
        @Primary
        ChatClient.Builder chatClientBuilder() {
            return StubChatClientFactory.builderReturning(new TaxpayerSummary("SINGLE", 950.0, 1, "LOW"));
        }
    }
}
