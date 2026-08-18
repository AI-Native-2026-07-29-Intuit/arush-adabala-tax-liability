-- Week 3 Day 3: transactional outbox for the taxcalc schema. One row per domain event
-- awaiting Kafka publish, written by the application inside the SAME transaction as the
-- domain write it accompanies (see TaxLiabilityService.computeLiability) so the two can
-- never diverge.

-- ---------------------------------------------------------------------------
-- taxcalc.event_outbox — swept by OutboxPublisher on a fixed schedule; published_at is
-- set once the Kafka send for that row has completed successfully.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS taxcalc.event_outbox (
    id           TEXT        PRIMARY KEY,
    aggregate_id TEXT        NOT NULL CHECK (length(aggregate_id) > 0),
    topic        TEXT        NOT NULL CHECK (length(topic) > 0),
    payload      JSONB       NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
                 -- intent: NULL means "not yet published"; OutboxPublisher polls exactly
                 -- these rows and sets this column on a successful Kafka send.
);

-- intent: OutboxPublisher's poll only ever wants unpublished rows, oldest first, and that
-- set stays small in steady state - a partial index keeps the poll cheap as the table
-- accumulates published history instead of scanning it.
CREATE INDEX IF NOT EXISTS idx_event_outbox_unpublished
    ON taxcalc.event_outbox (occurred_at)
    WHERE published_at IS NULL;
