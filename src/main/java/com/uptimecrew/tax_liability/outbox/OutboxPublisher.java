package com.uptimecrew.tax_liability.outbox;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import io.opentelemetry.context.propagation.TextMapGetter;

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
 *
 * <p>W3 D5: this method runs on its own {@code @Scheduled} thread, with no span of its own
 * inherited from whatever request originally wrote the row - left alone, every Kafka send here
 * would start a fresh, disconnected trace regardless of what triggered the write. Restoring each
 * row's captured {@link EventOutboxEntity#getTraceParent()} as the current context around its
 * send (only for the duration of that one send) makes the auto-instrumented Kafka producer span
 * a child of the ORIGINAL request's trace instead, so a caller can follow one trace id from their
 * HTTP request all the way through to the Kafka consumer that reacts to it.
 */
@Component
public class OutboxPublisher {

    private static final Logger LOG = LoggerFactory.getLogger(OutboxPublisher.class);
    private static final int BATCH_SIZE = 50;
    private static final long SEND_TIMEOUT_SECONDS = 5L;
    private static final String TRACEPARENT_HEADER = "traceparent";

    private static final TextMapGetter<Map<String, String>> TRACEPARENT_GETTER = new TextMapGetter<>() {
        @Override
        public Iterable<String> keys(Map<String, String> carrier) {
            return carrier.keySet();
        }

        @Override
        public String get(Map<String, String> carrier, String key) {
            return carrier == null ? null : carrier.get(key);
        }
    };

    private final EventOutboxRepository repository;
    private final KafkaTemplate<String, String> kafkaTemplate;
    private final OpenTelemetry openTelemetry;

    public OutboxPublisher(EventOutboxRepository repository,
            @Qualifier("kafkaTemplate") KafkaTemplate<String, String> kafkaTemplate, OpenTelemetry openTelemetry) {
        this.repository = Objects.requireNonNull(repository, "repository must not be null");
        this.kafkaTemplate = Objects.requireNonNull(kafkaTemplate, "kafkaTemplate must not be null");
        this.openTelemetry = Objects.requireNonNull(openTelemetry, "openTelemetry must not be null");
    }

    @Scheduled(fixedDelay = 1000L)
    @Transactional
    public void publishPending() {
        List<EventOutboxEntity> batch = repository.findUnpublishedForUpdate(PageRequest.of(0, BATCH_SIZE));
        if (batch.isEmpty()) {
            return;
        }
        for (EventOutboxEntity row : batch) {
            Context restoredContext = extractTraceParent(row.getTraceParent());
            try (Scope ignored = restoredContext == null ? null : restoredContext.makeCurrent()) {
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

    private Context extractTraceParent(String traceParent) {
        if (traceParent == null) {
            return null;
        }
        return openTelemetry.getPropagators().getTextMapPropagator()
                .extract(Context.root(), Map.of(TRACEPARENT_HEADER, traceParent), TRACEPARENT_GETTER);
    }
}
