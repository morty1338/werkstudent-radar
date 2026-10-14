"""Kurs-Radar metrics per job title and per academy, for the site and the MCP server.

Usage:
    python -m radar.kurs_stats            # writes docs/data/kurs.json and prints the numbers
    python -m radar.kurs_stats --print    # only print

Reads data/kurs/*.csv (radar.kurs, radar.kurs_extract) and data/kurs_radar.yaml; also writes
docs/data/kurs_postings.json (today's postings with title, company, city, date, job title and the
Quereinsteiger / AI marks, for the page's job list; links point to the Bundesagentur). The
sample is the postings online on the latest collection day (at most cap_per_title per
title, the most relevant ones); "postings" is the estimate for the whole board
(counts.csv: exact, or a lower bound for capped titles).

Per posting:
- skills: Claude Haiku's result (must have / nice to have) where there is one, else the
  rules' skills from the text; postings without a text yet don't count for skills.
- Quereinsteiger: the text welcomes career changers (radar.kurs_extract.career_changer)
  or the Bundesagentur's own flag `quereinstiegGeeignet` is set; base: postings with text.
- AI: an AI skill (GenAI, ML, NLP, deep learning) or an "other" term about AI from the
  LLM; for rules-only postings, AI words in the task/requirement sections.
- Basics: MS Office, Excel, PowerPoint or Word asked for (KURS_BASICS).
- New in 7 days: published at most 7 days before the as-of day.
Week-over-week: SQL over the daily counts (radar/sql/kurs_wow.sql); empty until a
collection day 7–10 days back exists.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from .extract_llm import read_csv
from .kurs import CONFIG_PATH, KURS_DIR, ROOT, load_config
from .kurs_extract import AI_SKILLS, AI_TERMS
from .skills import SKILLS

OUT_JSON = os.path.join(ROOT, "docs", "data", "kurs.json")
POSTINGS_JSON = os.path.join(ROOT, "docs", "data", "kurs_postings.json")
JOB_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
POSTING_FIELDS = ["refnr", "title", "company", "city", "published", "job_title", "qe", "ai"]
SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")
KURS_BASICS = ["ms_office", "excel", "powerpoint", "word"]
TOP_SKILLS = 15
TOP_TERMS = 10
NOTE = {"de": "nur Stellen der Bundesagentur für Arbeit, Momentaufnahme",
        "en": "Bundesagentur für Arbeit postings only, a snapshot"}


def _ids(s):
    return {x for x in (s or "").split("|") if x}


def _terms(s):
    return [t.strip() for t in (s or "").split(";") if t.strip()]


def posting_facts(p, feature, result, day):
    """What one posting asks for. `feature` and `result` may be None."""
    has_text = bool(feature and feature.get("status") == "ok")
    new_7d = bool(p.get("published")) and p["published"] >= (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    facts = {"title": p["job_title"], "new_7d": new_7d, "has_text": has_text,
             "qe_flag": p.get("quereinstieg") == "1", "qe_text": has_text and feature.get("qe_text") == "1",
             "source": None, "skills": set(), "must": set(), "nice": set(), "terms": []}
    if result and result.get("status") == "ok":
        must, nice = _ids(result["must"]), _ids(result["nice"])
        terms = _terms(result.get("other_must")) + _terms(result.get("other_nice"))
        facts.update(source="llm", must=must, nice=nice, skills=must | nice, terms=terms,
                     ai=bool((must | nice) & AI_SKILLS) or any(AI_TERMS.search(t) for t in terms))
    elif has_text:
        skills = _ids(feature.get("rules_skills"))
        facts.update(source="rules", skills=skills, ai=bool(skills & AI_SKILLS) or feature.get("ai_text") == "1")
    facts["qe"] = facts["qe_flag"] or facts["qe_text"]
    facts["basics"] = bool(facts["skills"] & set(KURS_BASICS))
    return facts


def term_key(term):
    """One key for the spellings of an "other" term: case, and a plural ending on long words
    ("Warenwirtschaftssysteme" = "Warenwirtschaftssystem")."""
    k = " ".join(term.casefold().split())
    return re.sub(r"(?:en|e|n|s)$", "", k) if len(k) > 6 else k


def _share(n, base):
    return {"n": n, "base": base, "share": round(n / base, 3) if base else None}


def aggregate(facts):
    """Metrics of a group of postings (one job title or one academy)."""
    with_text = [f for f in facts if f["has_text"]]
    with_skills = [f for f in facts if f["source"]]
    by_llm = [f for f in facts if f["source"] == "llm"]
    n, must, nice, terms, spelled = Counter(), Counter(), Counter(), Counter(), {}
    for f in with_skills:
        n.update(f["skills"])
    for f in by_llm:
        must.update(f["must"])
        nice.update(f["nice"])
        forms = {term_key(t): t for t in f["terms"]}                 # once per posting
        terms.update(forms.keys())
        for k, t in forms.items():
            spelled.setdefault(k, Counter())[t] += 1

    def row(sid):
        return {"id": sid, "n": n[sid], "share": round(n[sid] / len(with_skills), 3), "must": must[sid], "nice": nice[sid]}

    ranked = sorted(n, key=lambda s: (-n[s], s))
    return {
        "sample": len(facts),
        "with_text": len(with_text),
        "with_skills": len(with_skills),
        "read_by_llm": len(by_llm),
        "new_7d": _share(sum(f["new_7d"] for f in facts), len(facts)),
        "quereinsteiger": {**_share(sum(f["qe"] for f in with_text), len(with_text)),
                           "text": sum(f["qe_text"] for f in with_text),
                           "ba_flag": sum(f["qe_flag"] for f in with_text)},
        "ai": _share(sum(bool(f.get("ai")) for f in with_skills), len(with_skills)),
        "basics": _share(sum(f["basics"] for f in with_skills), len(with_skills)),
        "skills": [row(s) for s in ranked if s not in KURS_BASICS][:TOP_SKILLS],
        "basics_skills": [row(s) for s in KURS_BASICS if n[s]],
        "other_terms": [{"term": spelled[k].most_common(1)[0][0], "n": c} for k, c in terms.most_common(TOP_TERMS) if c >= 2],
    }


def week_over_week(counts):
    """{job_title: {"now", "before", "before_date"}} from the daily counts, in SQLite."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE kurs_counts (date TEXT, academy TEXT, job_title TEXT, estimated INTEGER)")
    db.executemany("INSERT INTO kurs_counts VALUES (?, ?, ?, ?)",
                   [(r["date"], r["academy"], r["job_title"], int(r["estimated"])) for r in counts])
    with open(os.path.join(SQL_DIR, "kurs_wow.sql"), encoding="utf-8") as f:
        rows = db.execute(f.read()).fetchall()
    return {t: {"now": now, "before": before, "before_date": d} for _, t, now, before, d in rows}


def _wow(items):
    """Summed change of several titles; None unless every one has a value a week back."""
    if not items or any(i is None or i["before"] is None for i in items):
        return None
    return {"now": sum(i["now"] for i in items), "before": sum(i["before"] for i in items),
            "before_date": items[0]["before_date"]}


def build(kurs_dir=KURS_DIR, config_path=CONFIG_PATH):
    config = load_config(config_path)
    postings = read_csv(os.path.join(kurs_dir, "postings.csv"))
    counts = read_csv(os.path.join(kurs_dir, "counts.csv"))
    if not postings or not counts:
        return None
    day = max(r["date"] for r in counts)
    features = {r["refnr"]: r for r in read_csv(os.path.join(kurs_dir, "features.csv"))}
    results = {r["refnr"]: r for r in read_csv(os.path.join(kurs_dir, "llm.csv"))}
    today = {r["job_title"]: r for r in counts if r["date"] == day}
    wow = week_over_week(counts)
    facts = [posting_facts(p, features.get(p["refnr"]), results.get(p["refnr"]), day)
             for p in postings if p["last_seen"] == day]
    names = {t["id"]: t["name"] for t in config["titles"]}

    academies, used = [], set()
    for a in config["academies"]:
        titles = []
        for tid in a["titles"]:
            mine = [f for f in facts if f["title"] == tid]
            c = today.get(tid)
            m = aggregate(mine)
            titles.append({"id": tid, "name": names[tid],
                           "postings": int(c["estimated"]) if c else len(mine),
                           "postings_exact": bool(c and c["complete"] == "1"),
                           **m, "wow": _wow([wow.get(tid)])})
            used.update(s["id"] for s in m["skills"] + m["basics_skills"])
        m = aggregate([f for f in facts if f["title"] in a["titles"]])
        used.update(s["id"] for s in m["skills"] + m["basics_skills"])
        academies.append({"id": a["id"], "name": a["name"],
                          "postings": sum(t["postings"] for t in titles),
                          "postings_exact": all(t["postings_exact"] for t in titles),
                          **m, "wow": _wow([wow.get(t) for t in a["titles"]]), "titles": titles})
    labels = {s[0]: {"label": s[1], "group": s[2]} for s in SKILLS if s[0] in used}
    return {"as_of": day, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": NOTE, "basics": KURS_BASICS, "skills": labels, "academies": academies}


def postings_list(kurs_dir=KURS_DIR):
    """The postings online on the latest day, for the page's job list: facts and the link
    pattern only (no job texts), newest first."""
    postings = read_csv(os.path.join(kurs_dir, "postings.csv"))
    counts = read_csv(os.path.join(kurs_dir, "counts.csv"))
    if not postings or not counts:
        return None
    day = max(r["date"] for r in counts)
    features = {r["refnr"]: r for r in read_csv(os.path.join(kurs_dir, "features.csv"))}
    results = {r["refnr"]: r for r in read_csv(os.path.join(kurs_dir, "llm.csv"))}
    rows = []
    for p in postings:
        if p["last_seen"] != day:
            continue
        f = posting_facts(p, features.get(p["refnr"]), results.get(p["refnr"]), day)
        rows.append([p["refnr"], p["title"], p["company"], p["city"], p["published"], p["job_title"],
                     int(f["qe"]), int(bool(f.get("ai")))])
    rows.sort(key=lambda r: (r[4], r[0]), reverse=True)
    return {"as_of": day, "url": JOB_URL, "fields": POSTING_FIELDS, "rows": rows}


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _pct(m):
    return "–" if m["share"] is None else f"{m['share']:.0%}"


def report(data):
    lines = [f"Kurs-Radar {data['as_of']} ({NOTE['de']})", ""]
    for a in data["academies"]:
        lines.append(f"## {a['name']}: {'' if a['postings_exact'] else '≥ '}{a['postings']:,} postings, sample {a['sample']} "
                     f"(text {a['with_text']}, LLM {a['read_by_llm']})")
        lines.append(f"{'job title':40s} {'postings':>9s} {'sample':>6s} {'Quer':>5s} {'AI':>5s} {'Basics':>6s} {'new7d':>6s} {'WoW':>6s}  top skills")
        for t in a["titles"] + [{**a, "name": "= academy"}]:
            wow = "–" if not t["wow"] else f"{(t['wow']['now'] - t['wow']['before']) / t['wow']['before']:+.0%}"
            top = ", ".join(f"{data['skills'][s['id']]['label']} {s['share']:.0%}" for s in t["skills"][:5])
            lines.append(f"{t['name'][:40]:40s} {('' if t['postings_exact'] else '≥')+str(t['postings']):>9s} {t['sample']:6d} "
                         f"{_pct(t['quereinsteiger']):>5s} {_pct(t['ai']):>5s} {_pct(t['basics']):>6s} {_pct(t['new_7d']):>6s} {wow:>6s}  {top}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--print", action="store_true", help="only print, don't write docs/data/kurs.json")
    args = ap.parse_args(argv)
    try:
        data = build()
    except Exception as e:                                       # noqa: BLE001 – never fail the daily run
        print(f"Kurs-Radar stats skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if not data:
        print("no Kurs-Radar data yet", file=sys.stderr)
        return 0
    print(report(data))
    if not args.print:
        _write_json(OUT_JSON, data)
        jobs = postings_list()
        if jobs:
            _write_json(POSTINGS_JSON, jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
