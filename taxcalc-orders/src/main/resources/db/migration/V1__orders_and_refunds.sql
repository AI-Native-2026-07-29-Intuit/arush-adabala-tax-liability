-- taxcalc-orders/src/main/resources/db/migration/V1__orders_and_refunds.sql
--
-- Two tables and one unique index. The index is the interesting line in this file.

CREATE TABLE IF NOT EXISTS orders (
    order_id   TEXT           NOT NULL,
    tenant_id  TEXT           NOT NULL,
    -- NUMERIC, never DOUBLE PRECISION. A money column stored as a float is a rounding error
    -- waiting for a large enough number, and the scale is part of the value: 10.00 and 10 are
    -- the same number and a different money amount.
    total      NUMERIC(12, 2) NOT NULL CHECK (total >= 0),
    status     TEXT           NOT NULL,
    created_at TIMESTAMPTZ    NOT NULL DEFAULT now(),
    -- Composite key, so the tenant is part of a row's identity rather than a column that has to
    -- be remembered in every WHERE clause.
    PRIMARY KEY (order_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS refunds (
    refund_id       TEXT           NOT NULL PRIMARY KEY,
    order_id        TEXT           NOT NULL,
    tenant_id       TEXT           NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    reason          TEXT           NOT NULL,
    status          TEXT           NOT NULL,
    idempotency_key TEXT           NOT NULL,
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    FOREIGN KEY (order_id, tenant_id) REFERENCES orders (order_id, tenant_id)
);

-- THIS is the idempotency guarantee. Not the application code that reads it back, and not the
-- caller's discipline in sending a key - this index.
--
-- Two retries of one request routinely arrive concurrently, because a retry is what a caller
-- does when the first response was slow. Application-level "check whether this key was used,
-- then insert" lets both of them check, both find nothing, and both insert; the ledger is
-- debited twice and every line of code involved looks correct in review. The unique index is the
-- only participant that sees both statements, so it is where the decision has to be made.
--
-- Scoped to the tenant rather than global: a global key space lets one tenant's UUID collide
-- with another's and silently suppress a legitimate refund, which presents as missing money and
-- is close to undiagnosable.
CREATE UNIQUE INDEX IF NOT EXISTS refunds_tenant_idempotency_key
    ON refunds (tenant_id, idempotency_key);

-- The synthetic order the MCP fixtures, the stdio smoke test and the E2E all reference. Seeded
-- in the migration rather than by the test, so the service is useful the moment it is healthy
-- and every consumer sees the same row.
INSERT INTO orders (order_id, tenant_id, total, status)
VALUES ('ord-synth-9001', 'tenant-a', 42.50, 'paid')
ON CONFLICT (order_id, tenant_id) DO NOTHING;
