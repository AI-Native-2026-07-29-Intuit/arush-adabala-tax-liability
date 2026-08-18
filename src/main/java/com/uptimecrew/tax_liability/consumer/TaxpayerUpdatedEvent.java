package com.uptimecrew.tax_liability.consumer;

import java.time.Instant;
import java.util.Objects;

/**
 * The JSON shape published to {@link com.uptimecrew.tax_liability.outbox.OutboxTopics#TAXPAYER_EVENTS}
 * by {@link com.uptimecrew.tax_liability.outbox.OutboxPublisher} and consumed here by
 * {@link TaxpayerUpdatedListener} to re-project {@link com.uptimecrew.tax_liability.readmodel.TaxpayerReadModel}.
 * Mirrors the fields {@link com.uptimecrew.tax_liability.service.TaxLiabilityService#computeLiability}
 * actually persists onto {@link com.uptimecrew.tax_liability.entity.Taxpayer}.
 */
public record TaxpayerUpdatedEvent(
        String aggregateId,
        String displayName,
        String filingStatus,
        String homeJurisdiction,
        Instant createdAt) {

    public TaxpayerUpdatedEvent {
        Objects.requireNonNull(aggregateId, "aggregateId must not be null");
        Objects.requireNonNull(displayName, "displayName must not be null");
        Objects.requireNonNull(filingStatus, "filingStatus must not be null");
        Objects.requireNonNull(homeJurisdiction, "homeJurisdiction must not be null");
        Objects.requireNonNull(createdAt, "createdAt must not be null");
    }
}
