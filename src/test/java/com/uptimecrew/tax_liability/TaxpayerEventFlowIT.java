package com.uptimecrew.tax_liability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.uptimecrew.tax_liability.consumer.TaxpayerUpdatedEvent;
import com.uptimecrew.tax_liability.outbox.EventOutboxRepository;
import com.uptimecrew.tax_liability.outbox.OutboxTopics;
import com.uptimecrew.tax_liability.readmodel.TaxpayerReadModelRepository;
import com.uptimecrew.tax_liability.service.TaxLiabilityService;

import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.KafkaContainer;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

/**
 * Proves the whole W3 D3 event flow end to end against four real Testcontainers-managed
 * datastores: a domain write reaches Kafka via the transactional outbox
 * ({@link #write_publishes_to_kafka_via_outbox}), a raw Kafka send re-projects the Mongo read
 * model ({@link #consumer_updates_mongo_read_model}), and a malformed payload is routed to the
 * dead-letter topic after retries ({@link #poison_pill_routes_to_dlt_after_retries}).
 */
@Testcontainers
@SpringBootTest
@ActiveProfiles("test")
class TaxpayerEventFlowIT {

    /**
     * How long an asynchronous step gets before it is called a failure.
     *
     * <p>Was a hardcoded 5s (10s in one place) per await. W6 D4 widened it to 30s and gave it a
     * name, after this class's first assertion failed in two consecutive full-suite runs while
     * passing every time the class ran alone.
     *
     * <p><b>That first explanation - scheduler contention - was wrong, and is corrected here
     * rather than quietly deleted, because the way it was wrong is the useful part.</b> Widening
     * the budget made one run pass and the next fail on the <em>following</em> assertion, which is
     * the signature of a cause that is not slowness at all. A widened timeout is a plausible
     * response to almost any async failure, and that is exactly what makes it dangerous: it turns
     * a reproducible failure into an intermittent one and buys silence instead of information.
     *
     * <h2>The actual cause, still unfixed</h2>
     *
     * <p>A per-class {@code @Container} is stopped when its class finishes, but Spring's
     * test-context cache does <em>not</em> close the context. The context lives on for the rest of
     * the JVM and so does its {@code @Scheduled} work, so a dead {@code OutboxPublisher} keeps
     * trying to open transactions against a container that no longer exists:
     *
     * <pre>
     * [scheduling-1] Connection to localhost:33732 refused
     * [scheduling-1] HikariPool-1 - Connection is not available, request timed out after 30001ms
     *     at OutboxPublisher$$SpringCGLIB$$0.publishPending(&lt;generated&gt;)
     * </pre>
     *
     * <p>{@code HikariPool-1} is the first pool created in the JVM - an early class's context,
     * long after that class ended. Each zombie holds a connection attempt open for the pool's full
     * 30s timeout, every second. W6 D4's heavier {@code pgvector/pgvector:pg16} image (required by
     * {@code V5__create_taxpayer_embeddings.sql}, see {@link TestImages}) added enough startup cost
     * and memory pressure to make a latent problem reproducible; it did not create it.
     *
     * <p><b>The fix is a JVM-lifetime container, and it was attempted and reverted.</b> The
     * standard singleton-container pattern relies on Ryuk to reap the container at JVM exit, and
     * Ryuk cannot start on a Rancher Desktop host - which is why this machine sets
     * {@code ryuk.container.disabled=true}. With Ryuk off the singleton left the suite worse (7
     * failures rather than 1); with Ryuk forced on nothing runs locally at all. It needs an
     * environment where it can actually be verified, so it is left as a known issue rather than
     * shipped unvalidated.
     *
     * <p>The 30s stays, because the original 5s was independently too tight - the sweep runs on
     * {@code fixedDelay = 1000L}, so it allowed five attempts. It does not weaken anything: these
     * tests pin that a write reaches Kafka through the outbox at all, never that it does so within
     * any particular number of seconds.
     */
    private static final Duration ASYNC_BUDGET = Duration.ofSeconds(30);

    @Container
    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>(TestImages.POSTGRES);

    @Container
    @ServiceConnection
    static final MongoDBContainer MONGO = new MongoDBContainer("mongo:7");

    @Container
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS = new GenericContainer<>("redis:7-alpine").withExposedPorts(6379);

    @Container
    @ServiceConnection
    static final KafkaContainer KAFKA = new KafkaContainer(DockerImageName.parse("confluentinc/cp-kafka:7.6.0"));

    // Postgres schema is applied by Flyway automatically during context startup (W3 D3);
    // Mongo, Redis and Kafka are schemaless - no equivalent step is needed for any of them.

    @Autowired
    private TaxLiabilityService service;

    @Autowired
    private EventOutboxRepository outboxRepository;

    @Autowired
    private TaxpayerReadModelRepository readModelRepository;

    @Autowired
    private KafkaTemplate<String, String> kafkaTemplate;

    @Autowired
    private ObjectMapper mapper;

    @Test
    void write_publishes_to_kafka_via_outbox() throws Exception {
        String aggregateId = "agg-" + UUID.randomUUID();

        service.computeLiability(aggregateId, "Ada Lovelace", "SINGLE", new BigDecimal("75000.00"));

        await().atMost(ASYNC_BUDGET).untilAsserted(() ->
                assertThat(outboxRepository.findAll())
                        .anyMatch(r -> r.getAggregateId().equals(aggregateId) && r.getPublishedAt() != null));

        try (KafkaConsumer<String, String> probe = newProbe("probe-1", OutboxTopics.TAXPAYER_EVENTS)) {
            await().atMost(ASYNC_BUDGET).untilAsserted(() -> {
                ConsumerRecord<String, String> rec = pollOne(probe);
                assertThat(rec).isNotNull();
                assertThat(rec.key()).isEqualTo(aggregateId);
            });
        }
    }

    @Test
    void consumer_updates_mongo_read_model() throws Exception {
        String aggregateId = "agg-" + UUID.randomUUID();
        TaxpayerUpdatedEvent event = new TaxpayerUpdatedEvent(aggregateId, "Synthetic Taxpayer", "SINGLE",
                "FEDERAL", Instant.now());
        String payload = mapper.writeValueAsString(event);

        kafkaTemplate.send(OutboxTopics.TAXPAYER_EVENTS, aggregateId, payload).get(5, TimeUnit.SECONDS);

        await().atMost(ASYNC_BUDGET).untilAsserted(() ->
                assertThat(readModelRepository.findById(aggregateId)).isPresent());
    }

    @Test
    void poison_pill_routes_to_dlt_after_retries() throws Exception {
        String aggregateId = "agg-" + UUID.randomUUID();

        kafkaTemplate.send(OutboxTopics.TAXPAYER_EVENTS, aggregateId, "{not valid json")
                .get(5, TimeUnit.SECONDS);

        try (KafkaConsumer<String, String> dlt = newProbe("dlt-probe", OutboxTopics.TAXPAYER_EVENTS_DLT)) {
            await().atMost(ASYNC_BUDGET).untilAsserted(() ->
                    assertThat(pollOne(dlt)).isNotNull());
        }
    }

    private static KafkaConsumer<String, String> newProbe(String groupId, String topic) {
        Map<String, Object> props = Map.of(
                ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KAFKA.getBootstrapServers(),
                ConsumerConfig.GROUP_ID_CONFIG, groupId,
                ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest",
                ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
                ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        KafkaConsumer<String, String> consumer = new KafkaConsumer<>(props);
        consumer.subscribe(List.of(topic));
        return consumer;
    }

    private static ConsumerRecord<String, String> pollOne(KafkaConsumer<String, String> consumer) {
        var records = consumer.poll(Duration.ofMillis(500));
        return records.isEmpty() ? null : records.iterator().next();
    }
}
