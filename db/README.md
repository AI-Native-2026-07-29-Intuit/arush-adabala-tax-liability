# Week 2 Day 1 — Postgres Schema, Constraints & Transactional Seed

Translates the Week 1 `TaxBracket` / `BracketResolver` domain into a durable
Postgres schema, applied as Flyway migrations (W3 D3 — see the top-level
README's Week 3 Day 3 section) under
[`src/main/resources/db/migration/`](../src/main/resources/db/migration/):
`V1__schema.sql` (DDL) and `V2__seed.sql` (seed data). `db/verify.sql`
(verification SELECTs, below) stays here since it isn't a migration.

## ER Diagram

```mermaid
erDiagram
    TAXPAYER ||--o| LIABILITY : "files (1 taxpayer : 1 liability row per tax_year)"
    BRACKET  ||--o{ LIABILITY : "resolves to (1 bracket : many liabilities)"

    TAXPAYER {
        TEXT id PK
        TEXT display_name
        TEXT filing_status
        TEXT home_jurisdiction
        TIMESTAMPTZ created_at
    }
    BRACKET {
        TEXT id PK
        TEXT jurisdiction
        INTEGER tax_year
        TEXT code
        NUMERIC rate
        NUMERIC floor_amount
        NUMERIC ceiling_amount
    }
    LIABILITY {
        TEXT taxpayer_id PK, FK
        INTEGER tax_year PK
        TEXT bracket_id FK
        NUMERIC taxable_amount
        NUMERIC liability_amount
        TIMESTAMPTZ computed_at
    }
```

`TAXPAYER (1) -- (0..1 per tax_year) LIABILITY`: a taxpayer may have zero or
one computed liability row per tax year (enforced by `LIABILITY`'s composite
primary key `(taxpayer_id, tax_year)`), and any number across different
years — modelled here as 1-to-many across the full table, 1-to-1 within a
single `tax_year`.

`BRACKET (1) -- (0..many) LIABILITY`: a single bracket can be the resolved
bracket for many liability rows (many taxpayers can land in the same
bracket); a liability always resolves to exactly one bracket.

## Schema decisions

- **`taxcalc.taxpayer`** models a taxpayer/filing identity — the anchor
  entity every `IncomeEvent` and `Deduction` in the Week 1 domain points at
  via `taxpayerId`. `filing_status` is constrained to the closed set of IRS
  filing statuses with a `CHECK`, not a native `ENUM`, since altering an
  `ENUM` later is a migration headache. `id` is `TEXT` (not `SERIAL`) so it
  can carry a synthetic id like `tp-2026-0001` without a database round-trip.

- **`taxcalc.bracket`** is the durable form of the Week 1 `TaxBracket`
  record (`id`, `jurisdiction`, `rate`, `floor`, `ceiling`), extended with
  `tax_year` and `code` so brackets are addressable by the natural key a
  rules author would type: `(jurisdiction, tax_year, code)`, enforced with
  `UNIQUE`. `rate` is `NUMERIC(5,4)` rather than `NUMERIC(12,2)` because it
  is a fraction (0–1), not a currency amount — the money-scale rule applies
  to `floor_amount`/`ceiling_amount`, not to rates. `ceiling_amount` is the
  only nullable column in the schema, mirroring `TaxBracket`'s nullable
  `ceiling` component (`null` = unbounded top bracket); every other column
  is `NOT NULL`.

- **`taxcalc.liability`** models one computed liability per taxpayer per
  tax year — the composite primary key `(taxpayer_id, tax_year)` enforces
  that invariant directly instead of re-checking it in application code.
  `taxpayer_id` cascades on delete (`ON DELETE CASCADE`) because a
  liability is strictly owned by its taxpayer and should not outlive it.
  `bracket_id` restricts on delete (`ON DELETE RESTRICT`) because
  `taxcalc.bracket` is a reference table — a bracket a stored liability
  points to must not be silently orphaned by deleting the bracket out from
  under it.

## Local run

Since W3 D3, the app itself applies `V1__schema.sql` and `V2__seed.sql` as Flyway
migrations on startup (`spring.flyway.*`, default `classpath:db/migration` location) —
`./gradlew bootRun` against a clean Postgres 16 container is the normal path:

```bash
docker run --name uptimecrew-pg \
  -e POSTGRES_PASSWORD=devpass \
  -p 5432:5432 \
  -d postgres:16

./gradlew bootRun
psql -h localhost -U postgres -f db/verify.sql
```

To inspect or run the migrations without starting the app, use the Flyway Gradle plugin
(configured in `build.gradle` against the same local datasource) or `psql` directly:

```bash
./gradlew flywayInfo      # lists applied/pending migrations
./gradlew flywayMigrate   # applies them, same as the app does on startup

# or, without Flyway bookkeeping (no flyway_schema_history row written):
psql -h localhost -U postgres -f src/main/resources/db/migration/V1__schema.sql
psql -h localhost -U postgres -f src/main/resources/db/migration/V2__seed.sql
```

To re-run from scratch against an existing database, drop the schema first:

```bash
psql -h localhost -U postgres -c "DROP SCHEMA IF EXISTS taxcalc CASCADE;"
```

## Constraint enforcement demo

Not a migration — a standalone proof that `taxcalc.bracket.rate`'s `CHECK` constraint
rejects out-of-range data, meant to be run manually against a schema that already exists
(e.g. after `./gradlew bootRun` above):

```sql
BEGIN;
INSERT INTO taxcalc.bracket (id, jurisdiction, tax_year, code, rate, floor_amount, ceiling_amount)
    VALUES ('bad-2026-negrate', 'FEDERAL', 2026, 'BADRATE', -0.5000, 0.00, NULL);
ROLLBACK;
```

Captured error when run against a fresh Postgres 16 container:

```
ERROR:  new row for relation "bracket" violates check constraint "bracket_rate_check"
DETAIL:  Failing row contains (bad-2026-negrate, FEDERAL, 2026, BADRATE, -0.5000, 0.00, null).
```

## Trade-offs

- **Composite PK on `liability` vs. a surrogate `id`.** A surrogate `id`
  would need an additional `UNIQUE (taxpayer_id, tax_year)` constraint to
  express the same "one liability per taxpayer per year" rule, plus an
  extra column nothing else references. The composite key `(taxpayer_id,
  tax_year)` expresses that invariant as the primary key itself and is
  exactly the pair every query joins/filters on, so it was chosen over a
  surrogate id.

- **`ON DELETE CASCADE` (taxpayer→liability) vs. `RESTRICT`
  (bracket→liability).** These are deliberately asymmetric. A taxpayer and
  their liabilities are a strict parent/child pair — deleting the taxpayer
  should take their computed liabilities with it, so `CASCADE` was chosen.
  A bracket, by contrast, is shared reference data that many liabilities
  point to; deleting a bracket that historical liabilities still resolved
  to would silently orphan those rows' explanation of how they were
  computed, so `RESTRICT` was chosen to force an explicit decision (e.g.
  reassign or archive the liabilities first) instead of a silent cascade.
