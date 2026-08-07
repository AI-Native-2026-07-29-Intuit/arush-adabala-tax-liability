-- db/queries/group_by_having.sql
-- Task 1.4 — GROUP BY that collapses to one row per group, filtered at the group
-- level with HAVING (not WHERE, which would filter individual rows before aggregation).
-- Run with: psql -h localhost -U postgres -d postgres -f db/queries/group_by_having.sql

SET search_path TO taxcalc, public;

-- Filing-status groups, with their taxpayer count and average liability.
-- HAVING references the aggregate COUNT(...) directly -- a WHERE clause could not.
SELECT p.filing_status,
       COUNT(p.id)              AS taxpayer_count,
       AVG(c.liability_amount)  AS avg_liability
FROM   taxcalc.taxpayer p
JOIN   taxcalc.liability c ON c.taxpayer_id = p.id
GROUP BY p.filing_status
HAVING COUNT(p.id) >= 1
ORDER BY avg_liability DESC;
