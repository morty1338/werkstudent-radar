-- Week-over-week change of the estimated postings per Kurs-Radar job title.
-- "Before" is the newest collection day 7 to 10 days before the latest one (the
-- daily run sometimes misses a day); no such day yet -> before is NULL.
WITH latest AS (
    SELECT MAX(date) AS day FROM kurs_counts
),
before_day AS (
    SELECT MAX(date) AS day FROM kurs_counts, latest
    WHERE date <= date(latest.day, '-7 days') AND date >= date(latest.day, '-10 days')
)
SELECT now.academy, now.job_title, now.estimated AS now, before.estimated AS before, before.date AS before_date
FROM kurs_counts AS now
JOIN latest ON now.date = latest.day
LEFT JOIN kurs_counts AS before
       ON before.job_title = now.job_title AND before.date = (SELECT day FROM before_day)
ORDER BY now.academy, now.job_title;
