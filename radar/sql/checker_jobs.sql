-- One row per tagged posting for the in-browser skill checker. The order (by
-- reference number) is shared by checker.json and postings.json, which are
-- matched up by position.
SELECT a.refnr, a.title, a.company, a.city, a.lat, a.lon, a.published, a.url,
       a.category, a.german, a.pay,
       a.role_n = 1 AS role_first,           -- counts in national pay figures
       a.role_city_n = 1 AS role_city_first, -- counts in pay figures for its city
       GROUP_CONCAT(js.skill, '|') AS skills,
       GROUP_CONCAT(CASE WHEN si.importance = 'nice_to_have' THEN js.skill END, '|') AS nice,
       a.refnr IN (SELECT refnr FROM llm_jobs) AS llm
FROM active_tagged a
LEFT JOIN job_skills js ON js.refnr = a.refnr
LEFT JOIN skill_importance si ON si.refnr = js.refnr AND si.skill = js.skill
GROUP BY a.refnr
ORDER BY a.refnr;
