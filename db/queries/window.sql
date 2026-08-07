-- db/queries/window.sql
-- Task 1.3 — RANK() and a windowed SUM() that aggregate without collapsing rows.
-- Run with: psql -h localhost -U postgres -d postgres -f db/queries/window.sql

SET search_path TO taxcalc, public;

-- For each filing_status group, rank taxpayers by liability_amount (highest = 1)
-- and compute the group's total liability alongside every row, one output row per liability.
SELECT p.filing_status,
       p.id                                                                        AS taxpayer_id,
       c.tax_year,
       c.liability_amount,
       RANK() OVER (PARTITION BY p.filing_status ORDER BY c.liability_amount DESC) AS amount_rank,
       SUM(c.liability_amount) OVER (PARTITION BY p.filing_status)                 AS filing_status_total
FROM   taxcalc.taxpayer p
JOIN   taxcalc.liability c ON c.taxpayer_id = p.id
ORDER BY p.filing_status, amount_rank;
