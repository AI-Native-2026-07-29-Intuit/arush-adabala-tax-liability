package com.uptimecrew.tax_liability.outbox;

/**
 * Kafka topic names shared by the outbox producer ({@link OutboxPublisher}) and the taxpayer
 * read-model consumer, kept in one place so the two sides cannot drift apart.
 */
public final class OutboxTopics {

    public static final String TAXPAYER_EVENTS = "taxpayers.events";

    public static final String TAXPAYER_EVENTS_DLT = TAXPAYER_EVENTS + ".DLT";

    private OutboxTopics() {
        throw new AssertionError("OutboxTopics is not instantiable");
    }
}
