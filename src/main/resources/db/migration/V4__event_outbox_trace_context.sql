-- Week 3 Day 5: carries the writing request's W3C traceparent alongside the outbox row it
-- accompanies, so OutboxPublisher's later @Scheduled sweep - which runs on its own background
-- thread with no inherited span context - can restore it as the parent context for the Kafka
-- send, rather than always starting a fresh trace disconnected from the request that wrote
-- the row. NULL for any row written outside an active span (e.g. a direct service call with no
-- HTTP request behind it) - OutboxPublisher falls back to publishing untraced in that case.
ALTER TABLE taxcalc.event_outbox ADD COLUMN IF NOT EXISTS trace_parent TEXT;
