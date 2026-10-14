"""Jobs mode of the site: the Kurs-Radar postings (data/kurs/, all working times) in the same
shape as the Werkstudent data, so the page's charts can show either data set.

Usage:
    python -m radar.jobs_build     # after radar.kurs_stats in the daily run

Builds rows with data/jobs.csv's columns from data/kurs/postings.csv (facts) and
data/kurs/features.csv (what the text says), runs them through the same SQL and functions as
radar/build.py and writes, next to the Werkstudent files:

    docs/data/jobs_summary.json, jobs_checker.json, jobs_postings.json, jobs_history.json,
    jobs_lifetimes.json, jobs_cooccurrence.json
    data/kurs/history.csv   today's snapshot appended (like data/history.csv)

Differences to the Werkstudent data:
- "category" is the job title from data/kurs_radar.yaml (the field filter becomes a job title /
  academy filter); summary and checker gain `academies`, and every job title says whether it
  is a sample (`sampled`: only the most relevant postings were read) with the estimated number
  of postings on the board (`estimated`).
- Pay is the salary per year (radar/salary.py), not per hour, of full-time postings only
  (`full_time`: no minijobs, no part-time only, no weekly hours below 30 stated).
  `pay_unit` is "year" and the histogram has 5,000 € bins. Same rules otherwise: each role
  counts once, a median needs 10 roles from 5 employers and a narrow 95% CI.
- Skills come from Claude Haiku (data/kurs/llm.csv) where it read the posting, else the rules.
"""

import os
import sys

from . import build
from .extract_llm import read_csv
from .kurs import CONFIG_PATH, KURS_DIR, ROOT, load_config

OUT_PREFIX = "jobs_"
HISTORY_CSV = os.path.join(KURS_DIR, "history.csv")
LLM_CSV = os.path.join(KURS_DIR, "llm.csv")


FULL_TIME_HOURS = 30   # a salary counts from this many hours a week (or when no hours are stated)


def full_time(posting, feature):
    """Whether a posting's salary is a full-time salary: not a minijob, not part-time only at the
    Bundesagentur, and no weekly hours below FULL_TIME_HOURS in the text. Part-time salaries
    (1,690 € a month for 25 hours) would pull the medians down without saying anything about pay."""
    if posting.get("minijob") == "1":
        return False
    if posting.get("parttime") == "1" and posting.get("fulltime") != "1":
        return False
    hours = feature.get("hours")
    return not hours or int(float(hours)) >= FULL_TIME_HOURS


def job_rows(kurs_dir=KURS_DIR):
    """data/kurs postings + text features as rows with data/jobs.csv's columns."""
    features = {r["refnr"]: r for r in read_csv(os.path.join(kurs_dir, "features.csv"))}
    rows = []
    for p in read_csv(os.path.join(kurs_dir, "postings.csv")):
        f = features.get(p["refnr"]) or {}
        ok = f.get("status") == "ok"
        paid = ok and f.get("pay_min") and full_time(p, f)
        rows.append({
            "refnr": p["refnr"], "title": p["title"], "company": p["company"], "city": p["city"],
            "region": p["region"], "lat": p["lat"], "lon": p["lon"], "category": p["job_title"],
            "lang": f.get("lang", "") if ok else "", "german": f.get("german", "") if ok else "",
            "english": f.get("english", "") if ok else "",
            "pay_min": f["pay_min"] if paid else "", "pay_max": f["pay_max"] if paid else "",
            "pay_src": f.get("pay_src", "") if paid else "",
            "hours": f.get("hours", "") if ok else "",
            "skills": f.get("rules_skills", "") if ok else "", "majors": f.get("majors", "") if ok else "",
            "remote": p.get("homeoffice") or "0", "external": "0",
            "published": p["published"], "first_seen": p["first_seen"], "last_seen": p["last_seen"],
            "detail_ok": "1" if ok else "0", "source": "ba", "url": "",
        })
    return rows


def build_all(kurs_dir=KURS_DIR, config_path=CONFIG_PATH, history_csv=HISTORY_CSV, llm_csv=None):
    """All Jobs-mode outputs as {file name: data}, or None without data."""
    config = load_config(config_path)
    rows = job_rows(kurs_dir)
    counts = read_csv(os.path.join(kurs_dir, "counts.csv"))
    if not rows or not counts:
        return None
    titles = config["titles"]
    db = build.load_db(rows=rows, categories=[(t["id"], t["name"]) for t in titles],
                       llm_csv=llm_csv or os.path.join(kurs_dir, "llm.csv"))

    summary = build.build_summary(db)
    day = summary["as_of"]
    today = {r["job_title"]: r for r in counts if r["date"] == max(c["date"] for c in counts)}
    academy_of = {t["id"]: t["academy"] for t in titles}
    for c in summary["categories"]:
        n = today.get(c["key"])
        c["academy"] = academy_of.get(c["key"], "")
        c["sampled"] = bool(n and n["complete"] != "1")
        c["estimated"] = int(n["estimated"]) if n else c["jobs"]
    academies = [{"id": a["id"], "name": a["name"], "titles": a["titles"],
                  "jobs": sum(c["jobs"] for c in summary["categories"] if c["academy"] == a["id"]),
                  "estimated": sum(int(today[t]["estimated"]) for t in a["titles"] if t in today)}
                 for a in config["academies"]]
    summary.update({
        "mode": "jobs",
        "pay_unit": "year",
        "pay_histogram": build.query(db, "pay_histogram_year"),
        "academies": academies,
        "insights": {},          # the Werkstudent headline comparisons (IT vs. marketing pay) don't apply
    })
    summary["source"] = {
        "name": "Bundesagentur für Arbeit – Jobbörse",
        "url": "https://www.arbeitsagentur.de/jobsuche/",
        "note": "Jobs of every working time for the job titles career-changer courses train for "
                "(data/kurs_radar.yaml), Bundesagentur postings only, a snapshot. Titles with many postings "
                "are a sample of the most relevant ones (`sampled`); job texts are analysed but not published.",
    }
    summary["method"]["pay"] = (
        "Salary per year from the Bundesagentur's salary fields or the posting's text (a monthly salary "
        "times 12; ranges by their midpoint; caps like 'up to' and unclear amounts ignored). Full-time postings only: "
        "minijobs, part-time-only postings and those stating fewer than 30 hours a week are left out. Each role (company + title) counts once. Medians need at least 10 roles from 5 employers and "
        "a 95% confidence interval no wider than a quarter of the median.")
    summary["totals"]["estimated"] = sum(a["estimated"] for a in academies)
    summary["totals"]["sampled_titles"] = sum(c["sampled"] for c in summary["categories"])

    checker, postings = build.build_checker(db, summary)
    checker.update({"mode": "jobs", "pay_unit": "year", "academies": academies,
                    "category_academy": {c["key"]: c["academy"] for c in summary["categories"]},
                    "sampled": [c["key"] for c in summary["categories"] if c["sampled"]]})
    history = build.build_history(build.update_history(db, day, path=history_csv))
    return {
        "summary.json": summary, "checker.json": checker, "postings.json": postings, "history.json": history,
        "lifetimes.json": build.build_lifetimes(db, day), "cooccurrence.json": build.build_cooccurrence(db, day),
    }


def report(outputs):
    s = outputs["summary.json"]
    t = s["totals"]
    by_title = {c["key"]: c for c in s["categories"]}
    lines = [f"Jobs mode {s['as_of']}: {t['jobs']} postings read (≥ {t['estimated']:,} on the board), "
             f"{t['with_pay']} roles with a salary per year ({t['postings_with_pay']} postings, "
             f"{t['postings_with_pay'] / t['jobs']:.0%}), median {t['median_pay']} €/year",
             "", f"{'academy / job title':42s} {'read':>5s} {'board':>7s} {'salary':>7s} {'share':>6s} {'median €/y':>11s}"]
    for a in s["academies"]:
        lines.append(f"{a['name']:42s} {a['jobs']:5d} {a['estimated']:7d}")
        for tid in a["titles"]:
            c = by_title.get(tid)
            if not c:
                continue
            med = f"{c['median_pay']:,.0f}" if c.get("median_pay") else "–"
            lines.append(f"  {c['label'][:38]:40s} {c['jobs']:5d} {('≥' if c['sampled'] else '') + str(c['estimated']):>7s} "
                         f"{c['with_pay']:7d} {c['with_pay'] / c['jobs']:6.0%} {med:>11s}")
    return "\n".join(lines)


def main():
    try:
        outputs = build_all()
    except Exception as e:                                       # noqa: BLE001 – never fail the daily run
        print(f"Jobs mode skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if not outputs:
        print("no Kurs-Radar data yet; Jobs mode skipped", file=sys.stderr)
        return 0
    for name, data in outputs.items():
        path = build.write_json(OUT_PREFIX + name, data)
        print(f"wrote {os.path.relpath(path, ROOT)} ({os.path.getsize(path) / 1024:.0f} KB)")
    print(report(outputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
