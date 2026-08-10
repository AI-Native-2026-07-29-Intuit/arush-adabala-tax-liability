-- db/queries/cte.sql
-- Task 1.2 — a named CTE reused by the outer SELECT instead of a repeated subquery.
-- Run with: psql -h localhost -U postgres -d postgres -f db/queries/cte.sql

SET search_path TO taxcalc, public;

-- "totals" names each taxpayer's summed liability once; the outer SELECT joins
-- back to taxpayer and filters/orders on that name instead of repeating the aggregate.
WITH totals AS (
    SELECT taxpayer_id,
           SUM(liability_amount) AS total_liability
    FROM   taxcalc.liability
    GROUP BY taxpayer_id
)
SELECT p.id              AS taxpayer_id,
       p.filing_status,
       p.home_jurisdiction,
       t.total_liability
FROM   taxcalc.taxpayer p
JOIN   totals t ON t.taxpayer_id = p.id
WHERE  t.total_liability > 0.00
ORDER BY t.total_liability DESC;
