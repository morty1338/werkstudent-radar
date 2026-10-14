-- Jobs mode: distribution of the salary per year in 5,000 € bins; below 20,000 € and from
-- 100,000 € up are one bin each.
SELECT MIN(MAX(CAST(role_pay / 5000 AS INTEGER) * 5000, 15000), 100000) AS bin,
       COUNT(*)                                                         AS jobs
FROM active
WHERE role_pay IS NOT NULL
GROUP BY bin
ORDER BY bin;
