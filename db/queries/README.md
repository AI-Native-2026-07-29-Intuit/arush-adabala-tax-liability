# db/queries — Advanced SQL Catalogue

Four self-contained query files against the Day 1 `taxcalc` schema
(`src/main/resources/db/migration/V1__schema.sql` +
`src/main/resources/db/migration/V2__seed.sql`, applied as Flyway migrations since W3
D3), each demonstrating one advanced-SQL idiom, plus the Testcontainers integration test
(`TaxpayerQueryIT`) that proves one of them against a real Postgres.

## Query catalogue

- **`joins.sql`** — Answers "which liabilities belong to which taxpayer, and how many
  liabilities does each taxpayer have (including zero)?" Touches `taxcalc.taxpayer` and
  `taxcalc.liability`. Idiom: one `INNER JOIN` (only taxpayers with a matching liability
  row survive) and one `LEFT JOIN` (every taxpayer survives, with `COUNT` producing 0 for
  taxpayers with no liability instead of dropping them).
- **`cte.sql`** — Answers "which taxpayers have a positive total recorded liability, and
  in what order?" Touches `taxcalc.taxpayer` and `taxcalc.liability`. Idiom: a `WITH
  totals AS (...)` CTE that sums `liability_amount` per taxpayer once, reused by the
  outer `SELECT` that joins back to `taxpayer` and filters/orders on the named result.
- **`window.sql`** — Answers "within each filing status, how does each taxpayer's
  liability rank, and what does that filing status owe in total?" Touches
  `taxcalc.taxpayer` and `taxcalc.liability`. Idiom: `RANK() OVER (PARTITION BY ...
  ORDER BY ...)` plus a windowed `SUM(...) OVER (PARTITION BY ...)` — both aggregate
  without collapsing rows, so the result still has one row per liability.
- **`group_by_having.sql`** — Answers "for each filing status, how many taxpayers are in
  it and what's their average liability?" Touches `taxcalc.taxpayer` and
  `taxcalc.liability`. Idiom: `GROUP BY filing_status` collapses to one row per group,
  filtered at the group level with `HAVING COUNT(p.id) >= 1` (a `WHERE` clause cannot
  reference an aggregate like `COUNT`).

## Running locally

```bash
psql -h localhost -U postgres -d postgres -f src/main/resources/db/migration/V1__schema.sql
psql -h localhost -U postgres -d postgres -f src/main/resources/db/migration/V2__seed.sql
psql -h localhost -U postgres -d postgres -f db/queries/cte.sql
```

Swap the last line for `joins.sql`, `window.sql`, or `group_by_having.sql` to run any of
the other three.

## Running in tests

```bash
./gradlew test --tests "*QueryIT"
```

`TaxpayerQueryIT` is annotated `@Testcontainers`, so a Postgres 16 container is started,
seeded, and torn down automatically per test run — no manual `docker run` is needed for
the test path (Docker itself must still be running on the host).

## Trade-offs

`cte.sql` uses a named CTE instead of an equivalent subquery so the aggregate
(`SUM(liability_amount) AS total_liability`) is computed once and given a name the outer
query reads twice (`t.total_liability` in both the `WHERE` and `ORDER BY`); a subquery
duplicated in `FROM` and `WHERE`/`ORDER BY` would either repeat the aggregation or force
an extra join, and either way reads worse than the named intermediate result.

The window function in `window.sql` cannot be replaced with a `GROUP BY`: `GROUP BY
filing_status` would collapse the `SINGLE` filing-status group's two liability rows
(taxpayers `tp-2026-0001` and `tp-2026-0004`) into a single summary row, destroying the
per-taxpayer detail (`amount_rank`, `liability_amount`) the query needs to show alongside
the group aggregate. `group_by_having.sql` demonstrates exactly that collapsing behavior
on the same `filing_status` partition, for contrast.
