package com.uptimecrew.tax_liability.outbox;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * Maps to {@code taxcalc.event_outbox} (W3 D3): one row per domain event awaiting Kafka
 * publish. {@link com.uptimecrew.tax_liability.service.TaxLiabilityService} inserts a row
 * here in the SAME {@code @Transactional} method that writes the domain entity, and
 * {@link OutboxPublisher} later sweeps unpublished rows and marks {@link #markPublished}
 * once the corresponding Kafka send completes.
 *
 * <p>{@link #getTraceParent()} (W3 D5) carries the writing request's W3C traceparent across that
 * later sweep's own {@code @Scheduled} thread, so {@link OutboxPublisher} can restore it as the
 * parent context around the Kafka send instead of always starting a disconnected trace.
 */
@Entity
@Table(schema = "taxcalc", name = "event_outbox")
public class EventOutboxEntity {

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "aggregate_id", nullable = false, updatable = false)
    private String aggregateId;

    @Column(name = "topic", nullable = false, updatable = false)
    private String topic;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "payload", nullable = false, updatable = false, columnDefinition = "jsonb")
    private String payload;

    @Column(name = "occurred_at", nullable = false, updatable = false)
    private Instant occurredAt;

    @Column(name = "published_at")
    private Instant publishedAt;

    // Nullable by design (W3 D5): only present when the write happened inside an active span -
    // see TaxLiabilityService#captureTraceParent. OutboxPublisher restores it as the parent
    // context for the Kafka send; a row with no traceParent is published untraced.
    @Column(name = "trace_parent")
    private String traceParent;

    /** Required by JPA. */
    protected EventOutboxEntity() {
    }

    public EventOutboxEntity(String aggregateId, String topic, String payload) {
        this(aggregateId, topic, payload, null);
    }

    public EventOutboxEntity(String aggregateId, String topic, String payload, String traceParent) {
        this.aggregateId = Objects.requireNonNull(aggregateId, "aggregateId must not be null");
        this.topic = Objects.requireNonNull(topic, "topic must not be null");
        this.payload = Objects.requireNonNull(payload, "payload must not be null");
        if (aggregateId.isBlank()) {
            throw new IllegalArgumentException("aggregateId must not be blank");
        }
        if (topic.isBlank()) {
            throw new IllegalArgumentException("topic must not be blank");
        }
        if (payload.isBlank()) {
            throw new IllegalArgumentException("payload must not be blank");
        }
        this.traceParent = traceParent;
        this.id = UUID.randomUUID().toString();
        this.occurredAt = Instant.now();
    }

    public String getId() {
        return id;
    }

    public String getAggregateId() {
        return aggregateId;
    }

    public String getTopic() {
        return topic;
    }

    public String getPayload() {
        return payload;
    }

    public Instant getOccurredAt() {
        return occurredAt;
    }

    public Instant getPublishedAt() {
        return publishedAt;
    }

    public String getTraceParent() {
        return traceParent;
    }

    public void markPublished(Instant when) {
        this.publishedAt = Objects.requireNonNull(when, "when must not be null");
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof EventOutboxEntity other && Objects.equals(id, other.id);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(id);
    }

    @Override
    public String toString() {
        return "EventOutboxEntity{id=" + id + ", aggregateId=" + aggregateId + ", topic=" + topic
                + ", occurredAt=" + occurredAt + ", publishedAt=" + publishedAt + "}";
    }
}
