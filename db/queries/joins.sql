-- db/queries/joins.sql
-- Task 1.1 — INNER JOIN across parent + child, and a LEFT JOIN for "every parent".
-- Run with: psql -h localhost -U postgres -d postgres -f db/queries/joins.sql

SET search_path TO taxcalc, public;

-- 1. INNER JOIN: every recorded liability paired with its taxpayer's filing_status.
SELECT p.id            AS taxpayer_id,
       p.filing_status,
       c.tax_year,
       c.liability_amount
FROM   taxcalc.taxpayer p
JOIN   taxcalc.liability c ON c.taxpayer_id = p.id
ORDER BY p.id, c.liability_amount DESC;

-- 2. LEFT JOIN: every taxpayer, even ones with no recorded liability (NULL count would show as 0).
SELECT p.id                 AS taxpayer_id,
       p.filing_status,
       COUNT(c.taxpayer_id) AS liability_count
FROM   taxcalc.taxpayer p
LEFT JOIN taxcalc.liability c ON c.taxpayer_id = p.id
GROUP BY p.id, p.filing_status
ORDER BY liability_count DESC, p.id;
