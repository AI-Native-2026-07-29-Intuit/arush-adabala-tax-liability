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
     * <p>The cause is contention, not a broken chain. {@code OutboxPublisher} sweeps on
     * {@code @Scheduled(fixedDelay = 1000L)}, so 5s was only five sweeps of headroom; and by the
     * time this class runs, Spring's test-context cache is holding several earlier ITs' contexts
     * open, each with its own scheduled sweep and its own Kafka listener still reconnecting. The
     * evidence for contention rather than breakage is in the log immediately before the failure -
     * a consumer retrying {@code localhost:9092}, the default, which belongs to an older cached
     * context rather than to this test's mapped container port.
     *
     * <p>W6 D4's move from {@code postgres:16-alpine} to {@code pgvector/pgvector:pg16} (required
     * by {@code V5__create_taxpayer_embeddings.sql}, see {@link TestImages}) added startup time
     * and memory pressure across nine containers, which is what pushed an already-tight budget
     * over. The suite was clean on the lighter image immediately before that change.
     *
     * <p><b>Widening this does not weaken the assertion.</b> What these tests are for is that a
     * domain write reaches Kafka through the outbox at all - the latency of that path is not a
     * property any of them was written to pin, and no production behaviour depends on it
     * completing inside five seconds. A timeout tuned so tightly that it fails on a loaded
     * machine tests the machine, not the code.
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
