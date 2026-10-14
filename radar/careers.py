"""Daily step: Werkstudent jobs from company career pages -> docs/data/companies.json.

Usage:
    python -m radar.careers

Reads the ATS job boards of the enabled companies in data/companies.yaml
(radar.ats) and writes docs/data/companies.json for the "Companies" tab:

    jobs       company, title, city ("" = remote in Germany), remote, field, url,
               posted_at, source_ats, first_seen, new, also_on
    companies  per enabled company: ats, careers page, postings on the board,
               Werkstudent jobs kept, whether the board could be read today

No job texts, only links to the originals. `first_seen` is carried over from
the previous file; `new` marks jobs first seen today at a company that was
already listed before (so adding a company doesn't flood "new today").
`also_on` names our other sources with the same job: same company (its
`ba_names` for the Bundesagentur, the name for Arbeitnow), same normalised
title and same city.

A board that can't be read keeps its jobs from the previous file for up to
CARRY_DAYS days. Problems are warnings in the run log (and the GitHub run
summary), never a failed run: a board answering 404, a board that is empty
after having postings, or one that can't be read.
"""

import csv
import json
import os
import re
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import ats, kurs
from . import companies as C
from .extract import CATEGORIES, classify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "docs", "data", "companies.json")
JOBS_PATH = os.path.join(ROOT, "data", "jobs.csv")
# Jobs mode: the same boards' postings for the Kurs-Radar job titles (data/kurs_radar.yaml).
JOBS_OUT_PATH = os.path.join(ROOT, "docs", "data", "jobs_companies.json")
KURS_POSTINGS_PATH = os.path.join(ROOT, "data", "kurs", "postings.csv")
CARRY_DAYS = 3

_GENDER = re.compile(
    r"\((?:[mwfdx]\s*[/|,]\s*)+[mwfdx]\)|\((?:gn|div|all genders?|alle geschlechter|m/w/d|w/m/d|m/f/d|f/m/d|m/w/x|w/m/x|m/f/x|f/m/x)\*?\)"
    r"|\b(?:[mwfdx]\s*/\s*){2}[mwfdx]\b|\ball genders?\b", re.I)


def norm_title(title):
    """Title for matching: no gender tags, Werkstudent/Working Student as one word,
    lowercase letters and digits only."""
    t = _GENDER.sub(" ", title or "")
    t = re.sub(r"werk[\s-]?student(?:in|:in|\*in|_in|en|innen)?|werkstudierende?|working[\s-]?student", " werkstudent ", t, flags=re.I)
    return " ".join(re.findall(r"[0-9a-zäöüß]+", t.lower()))


def norm_company(name):
    """Company name for matching Arbeitnow employers: no legal form, lowercase."""
    n = re.sub(r"\b(?:gmbh|ag|se|kg|kgaa|co|ug|e\.?\s?v|ltd|inc|holding|group|gruppe)\b\.?", " ", (name or "").lower())
    return " ".join(re.findall(r"[0-9a-zäöüß]+", n))


def online_postings(path=JOBS_PATH):
    """Our postings online on the latest day: {(source, company, city, norm_title)}."""
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return set()
    last = max(r["last_seen"] for r in rows)
    out = set()
    for r in rows:
        if r["last_seen"] != last:
            continue
        source = r.get("source") or "ba"                 # data/kurs/postings.csv: Bundesagentur only
        company = r["company"] if source == "ba" else norm_company(r["company"])
        out.add((source, company, r["city"], norm_title(r["title"])))
    return out


def also_on(job, company, postings):
    """Sources among ours that list the same job."""
    title = norm_title(job["title"])
    found = []
    ba_names = company.get("ba_names") or []
    an_names = {norm_company(company["name"])} | {norm_company(n) for n in ba_names}
    for source, name, city, t in postings:
        if t != title or (job["city"] and city != job["city"]):
            continue
        if (source == "ba" and name in ba_names) or (source == "arbeitnow" and name in an_names):
            found.append(source)
    return sorted(set(found))


def load_previous(path=OUT_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build(companies, fetched, status, previous, postings, as_of, field_of=None, field_labels=None):
    """companies.json content and the list of warnings.

    fetched/status come from ats.fetch_all; previous is yesterday's file (or None).
    field_of(title) and field_labels replace the Werkstudent fields (Jobs mode: job titles)."""
    prev = previous or {}
    prev_as_of = prev.get("as_of")
    prev_companies = {c["name"]: c for c in prev.get("companies") or []}
    prev_jobs = {(j["url"], j["city"]): j for j in prev.get("jobs") or []}
    by_name = {c["name"]: c for c in companies}
    warnings = []

    jobs = list(fetched)
    for name, st in status.items():
        if st["ok"]:
            continue
        pc = prev_companies.get(name)
        stale = prev_as_of and (date.fromisoformat(as_of) - date.fromisoformat(prev_as_of)).days > CARRY_DAYS
        if pc and not stale:
            jobs += [{k: j[k] for k in ("company", "title", "city", "remote", "url", "posted_at", "source_ats")}
                     for j in prev.get("jobs") or [] if j["company"] == name]

    out_jobs = []
    for j in jobs:
        company = by_name[j["company"]]
        since = (prev_companies.get(j["company"]) or {}).get("since") or as_of
        old = prev_jobs.get((j["url"], j["city"]))
        first_seen = old["first_seen"] if old else as_of
        out_jobs.append({
            **j,
            "field": field_of(j["title"]) if field_of else classify(j["title"], ""),
            "first_seen": first_seen,
            "new": first_seen == as_of and since < as_of,
            "also_on": also_on(j, company, postings),
        })
    out_jobs.sort(key=lambda j: (j["company"].casefold(), j["city"], j["title"].casefold()))

    out_companies = []
    for name, st in status.items():
        c, pc = by_name[name], prev_companies.get(name) or {}
        board = st["board"] if st["ok"] else pc.get("board")
        out_companies.append({
            "name": name,
            "ats": c["ats"],
            "careers_url": c.get("careers_url"),
            "cities": c.get("cities") or [],
            "board": board,
            "jobs": sum(1 for j in out_jobs if j["company"] == name),
            "ok": st["ok"],
            "since": pc.get("since") or as_of,
        })
        if st.get("not_found"):
            warnings.append(f"{name}: the {c['ats']} board '{c['slug']}' answers 404; fix ats/slug in data/companies.yaml")
        elif not st["ok"]:
            warnings.append(f"{name}: the {c['ats']} board couldn't be read: {st['error']}")
        elif st["board"] == 0 and (pc.get("board") or 0) > 0:
            warnings.append(f"{name}: 0 postings on the {c['ats']} board after {pc['board']} on {prev_as_of}; check ats/slug")
    out_companies.sort(key=lambda c: c["name"].casefold())

    data = {
        "as_of": as_of,
        "cities": list(ats.CITY_PATTERNS),
        "field_labels": field_labels or ({cid: label for cid, label, _ in CATEGORIES} | {"other": "Other"}),
        "companies": out_companies,
        "jobs": out_jobs,
    }
    return data, warnings


def report(data, warnings):
    n_new = sum(j["new"] for j in data["jobs"])
    n_ba = sum("ba" in j["also_on"] for j in data["jobs"])
    ok = sum(c["ok"] for c in data["companies"])
    print(f"{len(data['jobs'])} Werkstudent jobs from {ok}/{len(data['companies'])} company boards "
          f"({n_new} new today, {n_ba} also on the Bundesagentur)")
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for w in warnings:
        print(f"::warning title=Company career pages::{w}" if in_actions else f"WARNING {w}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Company career pages\n\n| Company | ATS | On board | Werkstudent | Read |\n|---|---|---|---|---|\n")
            for c in data["companies"]:
                f.write(f"| {c['name']} | {c['ats']} | {c['board'] if c['board'] is not None else '—'} | {c['jobs']} | {'✅' if c['ok'] else '⚠️'} |\n")
            if warnings:
                f.write("\n**Warnings**\n\n" + "".join(f"- {w}\n" for w in warnings))
            f.write("\n")


def main():
    as_of = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    companies = [c for c in C.load() if c.get("enabled")]
    try:
        config = kurs.load_config()
    except Exception as e:                                   # noqa: BLE001 – Jobs mode must not cost the Werkstudent file
        print(f"WARNING Kurs-Radar config unreadable ({e}); no jobs_companies.json today")
        config = None
    keep = (lambda title: bool(kurs.classify(title, config))) if config else None
    got = ats.fetch_all(companies, extra=keep) if keep else ats.fetch_all(companies)
    fetched, status = got[0], got[1]
    data, warnings = build(companies, fetched, status, load_previous(), online_postings(), as_of)
    if config:
        labels = {t["id"]: t["name"] for t in config["titles"]} | {"other": "Other"}
        jobs_data, _ = build(companies, got[2], status, load_previous(JOBS_OUT_PATH), online_postings(KURS_POSTINGS_PATH),
                             as_of, field_of=lambda title: (kurs.classify(title, config) or ["other"])[0], field_labels=labels)
        jobs_data["mode"] = "jobs"
        with open(JOBS_OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(jobs_data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"wrote {os.path.relpath(JOBS_OUT_PATH, ROOT)}: {len(jobs_data['jobs'])} jobs for the Kurs-Radar titles")
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {os.path.relpath(OUT_PATH, ROOT)} ({os.path.getsize(OUT_PATH) / 1024:.0f} KB)")
    report(data, warnings)


if __name__ == "__main__":
    sys.exit(main())
