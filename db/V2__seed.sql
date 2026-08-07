-- Transactional seed for the taxcalc schema. Every INSERT lives inside one
-- BEGIN/COMMIT — a failure on any row rolls back every previous one.

BEGIN;

INSERT INTO taxcalc.taxpayer (id, display_name, filing_status, home_jurisdiction) VALUES
    ('tp-2026-0001', 'taxcalc.example.internal/tp-0001', 'SINGLE',                    'FEDERAL'),
    ('tp-2026-0002', 'taxcalc.example.internal/tp-0002', 'MARRIED_FILING_JOINTLY',    'FEDERAL'),
    ('tp-2026-0003', 'taxcalc.example.internal/tp-0003', 'HEAD_OF_HOUSEHOLD',         'CA'),
    ('tp-2026-0004', 'taxcalc.example.internal/tp-0004', 'SINGLE',                    'TX'),
    ('tp-2026-0005', 'taxcalc.example.internal/tp-0005', 'MARRIED_FILING_SEPARATELY', 'NY');

INSERT INTO taxcalc.bracket (id, jurisdiction, tax_year, code, rate, floor_amount, ceiling_amount) VALUES
    ('fed-2026-10pct',  'FEDERAL', 2026, '10PCT',  0.1000, 0.00,      11600.00),
    ('fed-2026-22pct',  'FEDERAL', 2026, '22PCT',  0.2200, 11600.00,  47150.00),
    ('ca-2026-flat',    'CA',      2026, 'FLAT',   0.0930, 0.00,      NULL),
    ('tx-2026-none',    'TX',      2026, 'NONE',   0.0000, 0.00,      NULL),
    ('ny-2026-topband', 'NY',      2026, 'TOPBAND', 0.1090, 250000.00, NULL);

INSERT INTO taxcalc.liability (taxpayer_id, tax_year, bracket_id, taxable_amount, liability_amount) VALUES
    ('tp-2026-0001', 2026, 'fed-2026-10pct',  9500.00,   950.00),
    ('tp-2026-0002', 2026, 'fed-2026-22pct',  30000.00,  6600.00),
    ('tp-2026-0003', 2026, 'ca-2026-flat',    82000.00,  7626.00),
    ('tp-2026-0004', 2026, 'tx-2026-none',    61000.00,  0.00),
    ('tp-2026-0005', 2026, 'ny-2026-topband', 310000.00, 33790.00);

COMMIT;

-- ---------------------------------------------------------------------------
-- Intentional failure test — proves the CHECK constraint on taxcalc.bracket.rate
-- rejects out-of-range data. Run outside the seed transaction above.
--
-- Captured error when run against a fresh Postgres 16 container:
--   ERROR:  new row for relation "bracket" violates check constraint "bracket_rate_check"
--   DETAIL:  Failing row contains (bad-2026-negrate, FEDERAL, 2026, BADRATE, -0.5000, 0.00, null).
-- ---------------------------------------------------------------------------
BEGIN;
INSERT INTO taxcalc.bracket (id, jurisdiction, tax_year, code, rate, floor_amount, ceiling_amount)
    VALUES ('bad-2026-negrate', 'FEDERAL', 2026, 'BADRATE', -0.5000, 0.00, NULL);
ROLLBACK;
