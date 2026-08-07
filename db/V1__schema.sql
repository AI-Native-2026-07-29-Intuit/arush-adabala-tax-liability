-- Week 2 Day 1: Postgres schema for the Real-Time Tax Liability Calculator.
-- Translates the Week 1 Java domain (TaxBracket record, IncomeEvent/Deduction models,
-- BracketResolver strategies) into a schema-qualified relational model.

CREATE SCHEMA IF NOT EXISTS taxcalc;

-- ---------------------------------------------------------------------------
-- taxcalc.taxpayer — one row per taxpayer / filing identity.
-- ---------------------------------------------------------------------------
CREATE TABLE taxcalc.taxpayer (
    id                TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL CHECK (length(display_name) > 0),
    filing_status      TEXT NOT NULL
                        -- intent: mirrors the closed set of IRS filing statuses; TEXT + CHECK
                        -- instead of a native ENUM because altering an ENUM is a migration headache.
                        CHECK (filing_status IN ('SINGLE', 'MARRIED_FILING_JOINTLY',
                                                  'MARRIED_FILING_SEPARATELY', 'HEAD_OF_HOUSEHOLD')),
    home_jurisdiction TEXT NOT NULL CHECK (length(home_jurisdiction) > 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- taxcalc.bracket — tax brackets indexed by (jurisdiction, year, code); the
-- Postgres-durable form of the Week 1 TaxBracket record.
-- ---------------------------------------------------------------------------
CREATE TABLE taxcalc.bracket (
    id             TEXT PRIMARY KEY,
    jurisdiction   TEXT NOT NULL CHECK (length(jurisdiction) > 0),
    tax_year       INTEGER NOT NULL CHECK (tax_year BETWEEN 2000 AND 2100),
    code           TEXT NOT NULL CHECK (length(code) > 0),
    rate           NUMERIC(5,4) NOT NULL
                   -- intent: rate is a fraction (0.10 = 10%), not a currency amount, so it is
                   -- exempt from the NUMERIC(12,2) money rule but still bounded to a real rate.
                   CHECK (rate >= 0 AND rate <= 1),
    floor_amount   NUMERIC(12,2) NOT NULL CHECK (floor_amount >= 0),
    ceiling_amount NUMERIC(12,2),
                   -- intent: NULL represents an unbounded top bracket, mirroring
                   -- TaxBracket's nullable `ceiling` component; every other column is required.
    -- intent: (jurisdiction, tax_year, code) is the natural key a rules author would type
    -- ("federal 2026 10pct bracket") — the surrogate id exists for FK stability, not lookup.
    UNIQUE (jurisdiction, tax_year, code),
    CHECK (ceiling_amount IS NULL OR ceiling_amount > floor_amount)
);

-- ---------------------------------------------------------------------------
-- taxcalc.liability — computed liabilities per taxpayer per year.
-- ---------------------------------------------------------------------------
CREATE TABLE taxcalc.liability (
    taxpayer_id      TEXT NOT NULL
                       REFERENCES taxcalc.taxpayer(id) ON DELETE CASCADE,
                       -- intent: liabilities are strictly owned by their taxpayer;
                       -- deleting a taxpayer should delete their computed liabilities with them.
    tax_year         INTEGER NOT NULL CHECK (tax_year BETWEEN 2000 AND 2100),
    bracket_id       TEXT NOT NULL
                       REFERENCES taxcalc.bracket(id) ON DELETE RESTRICT,
                       -- intent: brackets are a reference table; a bracket that a stored
                       -- liability points to must not be silently orphaned by deletion.
    taxable_amount   NUMERIC(12,2) NOT NULL CHECK (taxable_amount >= 0),
    liability_amount NUMERIC(12,2) NOT NULL CHECK (liability_amount >= 0),
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- intent: one computed liability per taxpayer per tax year — the composite key
    -- expresses that invariant directly instead of re-checking it in application code.
    PRIMARY KEY (taxpayer_id, tax_year)
);
