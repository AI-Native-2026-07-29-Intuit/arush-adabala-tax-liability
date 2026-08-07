-- Verification SELECTs for the taxcalc schema seed.

-- 1. Sanity-check row counts per table (no JOIN).
SELECT 'taxpayer' AS t, COUNT(*) FROM taxcalc.taxpayer
UNION ALL
SELECT 'bracket',  COUNT(*) FROM taxcalc.bracket
UNION ALL
SELECT 'liability', COUNT(*) FROM taxcalc.liability;

-- 2. JOIN: every liability with its taxpayer's filing status and the bracket it resolved to.
SELECT tp.id AS taxpayer_id, tp.filing_status, br.code AS bracket_code, li.taxable_amount, li.liability_amount
FROM   taxcalc.liability li
JOIN   taxcalc.taxpayer  tp ON tp.id = li.taxpayer_id
JOIN   taxcalc.bracket   br ON br.id = li.bracket_id
ORDER  BY tp.id;

-- 3. Aggregate: total computed liability per jurisdiction, via the bracket join, GROUP BY jurisdiction.
SELECT br.jurisdiction, SUM(li.liability_amount) AS total_liability
FROM   taxcalc.liability li
JOIN   taxcalc.bracket   br ON br.id = li.bracket_id
GROUP  BY br.jurisdiction
ORDER  BY total_liability DESC;
