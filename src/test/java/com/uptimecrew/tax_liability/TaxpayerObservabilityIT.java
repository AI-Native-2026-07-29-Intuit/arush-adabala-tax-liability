package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedEvent;
import com.uptimecrew.tax_liability.entity.Taxpayer;
import com.uptimecrew.tax_liability.outbox.OutboxTopics;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel.EmbeddedLiability;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.repository.TaxpayerRepository;
import com.uptimecrew.tax_liability.security.ScopeAndRoleAuthoritiesConverter;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.propagation.ContextPropagators;
import io.opentelemetry.sdk.OpenTelemetrySdk;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.metadata.ChatResponseMetadata;
import org.springframework.ai.chat.metadata.DefaultUsage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.graphql.tester.AutoConfigureGraphQlTester;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.graphql.test.tester.GraphQlTester;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.RequestPostProcessor;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.KafkaContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

/**
 * Proves trace continuity programmatically (W3 D5 Task 4), the counterpart to eyeballing spans in
 * the Jaeger UI: {@link TestOtelConfig} overrides the app's {@link OpenTelemetry} bean with an
 * {@link InMemorySpanExporter}-backed SDK so finished spans can be asserted on directly, in-process,
 * without a running collector.
 *
 * <p>Three legs, one assertion style each: an HTTP request to {@code GET /api/v1/taxpayers/{id}}
 * emits a server span with a JDBC child sharing its trace id ({@link #httpRequest_emitsServerSpan_withJdbcChildSpan})
 * when the read model falls back to Postgres; a raw Kafka send and the {@link
 * com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedListener} that consumes it share exactly one
 * trace id via the {@code traceparent} header Task 2 wired up ({@link
 * #kafkaSendAndConsume_shareOneTraceId}); and the {@code summarizeTaxpayer} mutation's manual {@code
 * llm.summarize} span (Task 3) carries non-null token attributes ({@link
 * #llmSummarize_spanHasTokenAttributes}).
 *
 * <p>Deliberately does NOT run a Jaeger container: the {@code test} profile sets {@code
 * otel.sdk.disabled: true}, so without {@link TestOtelConfig}'s {@code @Primary} override every
 * other IT gets a no-op {@link OpenTelemetry} bean and never attempts real OTLP export. This class
 * is the only one that pays for a real (in-memory) SDK.
 */
@Testcontainers
@SpringBootTest(properties = "otel.sdk.disabled=false")
@AutoConfigureMockMvc
@AutoConfigureGraphQlTester
@ActiveProfiles("test")
class TaxpayerObservabilityIT {

    private static final String READ_SCOPE = "taxpayers.read";
    private static final String READER_ROLE = "TAXPAYER_READER";

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16-alpine");

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Container
    @ServiceConnection
    static final KafkaContainer KAFKA = new KafkaContainer(DockerImageName.parse("confluentinc/cp-kafka:7.6.0"));

    @Autowired
    private MockMvc mvc;

    @Autowired
    private GraphQlTester graphQlTester;

    @Autowired
    private TaxpayerRepository taxpayerRepository;

    @Autowired
    private TaxpayerReadModelRepository readModelRepository;

    @Autowired
    private KafkaTemplate<String, String> kafkaTemplate;

    @Autowired
    private ObjectMapper mapper;

    @Autowired
    private InMemorySpanExporter spanExporter;

    @BeforeEach
    void resetExporter() {
        spanExporter.reset();
    }

    @BeforeEach
    void seedReadModelForLlmTest() {
        readModelRepository.save(new TaxpayerReadModel("seeded-id-1", "Ada Lovelace", "SINGLE", "FEDERAL",
                Instant.now(), List.of(new EmbeddedLiability(2026, "fed-2026-10pct",
                        new BigDecimal("9500.00"), new BigDecimal("950.00"), Instant.now()))));
    }

    @Test
    void httpRequest_emitsServerSpan_withJdbcChildSpan() throws Exception {
        // Saved via the JPA repository directly (bypassing TaxLiabilityService's Mongo
        // write-through), so the GET below is guaranteed to miss Redis and Mongo and fall back
        // to a real Postgres query - the JDBC leg this test is actually proving.
        String id = "observability-jdbc-1";
        taxpayerRepository.save(new Taxpayer(id, "Ada Lovelace", "SINGLE", "FEDERAL", Instant.now()));

        mvc.perform(get("/api/v1/taxpayers/{id}", id).with(readerJwt()))
                .andExpect(status().isOk());

        List<SpanData> spans = spanExporter.getFinishedSpanItems();
        assertThat(spans).isNotEmpty();

        SpanData server = spans.stream()
                .filter(s -> s.getKind() == SpanKind.SERVER)
                .findFirst()
                .orElseThrow(() -> new AssertionError("no HTTP server span emitted"));

        boolean hasJdbcChild = spans.stream()
                .anyMatch(s -> s.getTraceId().equals(server.getTraceId())
                        && !s.getSpanId().equals(server.getSpanId())
                        && (s.getInstrumentationScopeInfo().getName().toLowerCase().contains("jdbc")
                                || s.getName().toLowerCase().contains("select")));

        assertThat(hasJdbcChild)
                .as("expected at least one JDBC child span sharing the HTTP server span's traceId")
                .isTrue();
    }

    @Test
    void kafkaSendAndConsume_shareOneTraceId() throws Exception {
        // A raw send (mirrors TaxpayerEventFlowIT.consumer_updates_mongo_read_model) rather than
        // going through the outbox's @Scheduled poll: that poll runs continuously on its own
        // background thread with no caller span, so it would start a fresh trace regardless of
        // what triggered the write - this isolates the Kafka producer/consumer leg Task 2 wired
        // traceparent propagation for.
        String aggregateId = "observability-kafka-" + System.nanoTime();
        TaxpayerUpdatedEvent event = new TaxpayerUpdatedEvent(aggregateId, "Trace Continuity Taxpayer", "SINGLE",
                "FEDERAL", Instant.now());
        kafkaTemplate.send(OutboxTopics.TAXPAYER_EVENTS, aggregateId, mapper.writeValueAsString(event))
                .get(5, TimeUnit.SECONDS);

        await().atMost(Duration.ofSeconds(15)).untilAsserted(() -> {
            List<SpanData> messagingSpans = spanExporter.getFinishedSpanItems().stream()
                    .filter(s -> (s.getKind() == SpanKind.PRODUCER || s.getKind() == SpanKind.CONSUMER)
                            && s.getName().contains(OutboxTopics.TAXPAYER_EVENTS))
                    .collect(Collectors.toList());

            Set<String> traceIds = messagingSpans.stream().map(SpanData::getTraceId).collect(Collectors.toSet());
            assertThat(traceIds)
                    .as("expected exactly one trace id across the Kafka producer send and consumer receive")
                    .hasSize(1);
            assertThat(messagingSpans)
                    .as("expected at least a producer and a consumer span")
                    .hasSizeGreaterThanOrEqualTo(2);
        });
    }

    @Test
    void llmSummarize_spanHasTokenAttributes() {
        graphQlTester.document("mutation { summarizeTaxpayer(id: \"seeded-id-1\") { riskBand } }")
                .execute()
                .path("summarizeTaxpayer.riskBand").entity(String.class).isEqualTo("HIGH");

        SpanData llm = spanExporter.getFinishedSpanItems().stream()
                .filter(s -> "llm.summarize".equals(s.getName()))
                .findFirst()
                .orElseThrow(() -> new AssertionError("no llm.summarize span emitted"));

        assertThat(llm.getAttributes().get(AttributeKey.stringKey("llm.model"))).isNotBlank();
        assertThat(llm.getAttributes().get(AttributeKey.longKey("llm.tokens.in"))).isNotNull();
        assertThat(llm.getAttributes().get(AttributeKey.longKey("llm.tokens.out"))).isNotNull();
    }

    private static RequestPostProcessor readerJwt() {
        return jwt()
                .jwt(j -> j.subject("observability-it-user").claim("scope", READ_SCOPE).claim("roles", List.of(READER_ROLE)))
                .authorities(new ScopeAndRoleAuthoritiesConverter());
    }

    @TestConfiguration
    static class TestOtelConfig {

        // SimpleSpanProcessor (not BatchSpanProcessor) so spans land in the exporter synchronously,
        // right after each span ends - BatchSpanProcessor's default 500ms delay would force every
        // assertion above onto Awaitility just to see its own span.
        //
        // setPropagators(...) is NOT optional: OpenTelemetrySdk.builder() defaults to a no-op
        // ContextPropagators when it isn't called, which silently breaks both ends of the Kafka
        // leg below - the producer's KafkaTelemetry wrapper injects the traceparent header via
        // this exact propagator, and the consumer's InstrumentedRecordInterceptor extracts it the
        // same way, so a no-op propagator means no header is ever written OR read.
        @Bean
        @Primary
        OpenTelemetry openTelemetry(InMemorySpanExporter exporter) {
            SdkTracerProvider provider = SdkTracerProvider.builder()
                    .addSpanProcessor(SimpleSpanProcessor.create(exporter))
                    .build();
            return OpenTelemetrySdk.builder()
                    .setTracerProvider(provider)
                    .setPropagators(ContextPropagators.create(W3CTraceContextPropagator.getInstance()))
                    .build();
        }

        @Bean
        InMemorySpanExporter inMemorySpanExporter() {
            return InMemorySpanExporter.create();
        }

        // Same reasoning as TaxpayerGraphQlIT.StubChatModelConfig: swap in a deterministic
        // ChatModel so summarizeTaxpayer still runs Spring AI's real call path but never reaches
        // Anthropic, this time with an explicit non-zero Usage so the llm.tokens.* assertions
        // above are proving real propagation, not just "the attribute happens to default to 0".
        @Bean
        @Primary
        ChatClient.Builder stubChatClientBuilder() {
            ChatModel stub = mock(ChatModel.class);
            String json = "{\"filingStatus\":\"SINGLE\",\"totalLiability\":950.0,"
                    + "\"jurisdictionCount\":1,\"riskBand\":\"HIGH\"}";
            ChatResponse response = new ChatResponse(List.of(new Generation(new AssistantMessage(json))),
                    ChatResponseMetadata.builder().usage(new DefaultUsage(17, 42)).build());
            when(stub.call(any(Prompt.class))).thenReturn(response);
            return ChatClient.builder(stub);
        }
    }
}
