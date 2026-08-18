package com.uptimecrew.tax_liability.outbox;

import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.domain.PageRequest;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Sweeps unpublished {@link EventOutboxEntity} rows on a fixed schedule and sends each to its
 * recorded Kafka topic, keyed by the aggregate id (so all events for one aggregate land on the
 * same partition and preserve per-aggregate ordering). A row is marked published only after its
 * send completes; a send failure leaves {@code published_at} {@code NULL} so the next poll
 * retries it - at-least-once delivery, never a silently dropped event.
 */
@Component
public class OutboxPublisher {

    private static final Logger LOG = LoggerFactory.getLogger(OutboxPublisher.class);
    private static final int BATCH_SIZE = 50;
    private static final long SEND_TIMEOUT_SECONDS = 5L;

    private final EventOutboxRepository repository;
    private final KafkaTemplate<String, String> kafkaTemplate;

    public OutboxPublisher(EventOutboxRepository repository,
            @Qualifier("kafkaTemplate") KafkaTemplate<String, String> kafkaTemplate) {
        this.repository = Objects.requireNonNull(repository, "repository must not be null");
        this.kafkaTemplate = Objects.requireNonNull(kafkaTemplate, "kafkaTemplate must not be null");
    }

    @Scheduled(fixedDelay = 1000L)
    @Transactional
    public void publishPending() {
        List<EventOutboxEntity> batch = repository.findUnpublishedForUpdate(PageRequest.of(0, BATCH_SIZE));
        if (batch.isEmpty()) {
            return;
        }
        for (EventOutboxEntity row : batch) {
            try {
                kafkaTemplate.send(row.getTopic(), row.getAggregateId(), row.getPayload())
                        .get(SEND_TIMEOUT_SECONDS, TimeUnit.SECONDS);
                row.markPublished(Instant.now());
                LOG.info("outbox published id={} topic={} aggregateId={}", row.getId(), row.getTopic(),
                        row.getAggregateId());
            } catch (Exception ex) {
                LOG.warn("outbox publish failed id={} topic={} cause={}", row.getId(), row.getTopic(),
                        ex.toString());
                // leave published_at NULL; next poll retries.
            }
        }
    }
}
