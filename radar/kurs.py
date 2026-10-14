"""Kurs-Radar: postings for the job titles that career changers are trained for
(data/kurs_radar.yaml), from the Bundesagentur für Arbeit's job search. All
working times, not only Werkstudent jobs; kept apart from the Werkstudent data.

Usage:
    python -m radar.kurs              # daily: search, then update data/kurs/*.csv
    python -m radar.kurs --dry-run    # search and print the counts, write nothing

Each job title has a few search queries and title patterns. Every query's first
page is always read (it gives the total and how many results fit the title);
more pages only while the title has fewer than `cap_per_title` postings, at most
`max_pages_per_query`. Results come most relevant first, so a capped title keeps
the closest matches. A posting returned by several queries is stored once and
counts for the first title in the file whose pattern matches its title.

Output (only new files; the Werkstudent data is untouched):
    data/kurs/postings.csv  one row per posting ever seen: facts, academy, job title,
                            first_seen / last_seen (no job texts)
    data/kurs/counts.csv    per day and job title: postings kept, postings matched,
                            estimated postings on the board, the search's own totals
    data/raw/<date>/kurs.jsonl.gz   raw search records (git-ignored)

The estimate: a title whose queries were read to the end is exact (`complete`).
Otherwise it is a lower bound: the largest query's total times the share of its read
results that fit the title (the queries of one title overlap, so they aren't added up).

All or nothing: when a request fails after the retries in api.py, or far fewer
postings come back than on the last run, nothing is written and the step exits 1
(daily.yml runs it with continue-on-error, so the day's other data still lands).
"""

import argparse
import csv
import gzip
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from . import api
from .extract import normalise_city

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "kurs_radar.yaml")
KURS_DIR = os.path.join(ROOT, "data", "kurs")
RAW_DIR = os.path.join(ROOT, "data", "raw")

POSTING_COLUMNS = [
    "refnr", "title", "company", "city", "region", "lat", "lon", "academy", "job_title", "also",
    "query", "quereinstieg", "fulltime", "parttime", "minijob", "homeoffice", "published",
    "first_seen", "last_seen",
]
COUNT_COLUMNS = [
    "date", "academy", "job_title", "postings", "matched", "estimated", "complete",
    "ba_total", "fetched", "queries",
]
PAGE_SIZE = 100
REQUEST_DELAY = 0.5        # seconds between requests, as in collect.py
MAX_REQUESTS = 300         # hard stop per run, whatever the config says
MIN_RATIO_VS_LAST_RUN = 0.5
_ID = re.compile(r"^[a-z0-9_]+$")

log = logging.getLogger("kurs")


class KursError(Exception):
    """The config is broken or the result looks implausible; nothing is written."""


# --- Config -------------------------------------------------------------------------------------

def load_config(path=CONFIG_PATH):
    """The YAML file, checked and with compiled patterns.

    Returns {"settings": {...}, "academies": [...], "titles": [...]} where every title
    carries its academy id and compiled `match` patterns, in file order.
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    settings = {"angebotsart": 1, "cap_per_title": 500, "max_pages_per_query": 10, "exclude": [],
                **(data.get("settings") or {})}
    try:
        exclude = [re.compile(p, re.IGNORECASE) for p in settings["exclude"]]
    except re.error as e:
        raise KursError(f"bad exclude pattern: {e}") from None
    academies, titles, seen = [], [], set()
    for a in data.get("academies") or []:
        if not _ID.match(str(a.get("id", ""))) or not a.get("name"):
            raise KursError(f"academy needs an id (a-z0-9_) and a name: {a.get('id')!r}")
        academies.append({"id": a["id"], "name": a["name"], "titles": []})
        for t in a.get("titles") or []:
            tid = str(t.get("id", ""))
            if not _ID.match(tid) or tid in seen:
                raise KursError(f"job title id missing, not a-z0-9_ or used twice: {tid!r}")
            if not t.get("name") or not t.get("queries") or not t.get("match"):
                raise KursError(f"job title {tid} needs a name, queries and match patterns")
            try:
                patterns = [re.compile(p, re.IGNORECASE) for p in t["match"]]
                not_patterns = [re.compile(p, re.IGNORECASE) for p in t.get("exclude") or []]
            except re.error as e:
                raise KursError(f"bad pattern in {tid}: {e}") from None
            seen.add(tid)
            title = {"id": tid, "name": t["name"], "academy": a["id"],
                     "queries": [str(q) for q in t["queries"]], "match": patterns, "exclude": not_patterns}
            academies[-1]["titles"].append(tid)
            titles.append(title)
    if not titles:
        raise KursError(f"no job titles in {path}")
    return {"settings": {**settings, "exclude": exclude}, "academies": academies, "titles": titles}


def classify(posting_title, config):
    """Job title ids whose patterns match, in file order; [] when excluded or none fits."""
    text = posting_title or ""
    if any(p.search(text) for p in config["settings"]["exclude"]):
        return []
    return [t["id"] for t in config["titles"]
            if any(p.search(text) for p in t["match"]) and not any(p.search(text) for p in t["exclude"])]


# --- Search -------------------------------------------------------------------------------------

def search_page(query, page, angebotsart):
    data = api.get_json(api.BASE_URL + api.SEARCH_PATH,
                        {"was": query, "page": page, "size": PAGE_SIZE, "angebotsart": angebotsart})
    if not isinstance(data, dict) or "maxErgebnisse" not in data:
        raise api.ApiError(f"unexpected search response for '{query}' page {page}")
    return data["maxErgebnisse"], data.get("ergebnisliste") or []


def collect(config, search=search_page, sleep=time.sleep):
    """Run every title's queries. Returns (postings, counts, queries).

    postings: {refnr: {"record", "titles" (matching ids), "query"}} in discovery order
    counts:   {title_id: {...}} kept/matched/estimated per title
    queries:  list of per-query dicts (title, query, total, fetched, pages, hits)
    """
    s = config["settings"]
    cap, max_pages = int(s["cap_per_title"]), int(s["max_pages_per_query"])
    found, per_title, queries, requests = {}, {t["id"]: [] for t in config["titles"]}, [], 0

    def add(rec, query):
        """Store a record once; returns the job title it counts for (also when seen before)."""
        ref = rec.get("referenznummer")
        if not ref:
            return None
        if ref not in found:
            ids = classify(rec.get("stellenangebotsTitel"), config)
            found[ref] = {"record": rec, "titles": ids, "query": query}
            if ids:
                per_title[ids[0]].append(ref)
        ids = found[ref]["titles"]
        return ids[0] if ids else None

    for t in config["titles"]:
        for query in t["queries"]:
            q = {"title": t["id"], "query": query, "total": 0, "fetched": 0, "pages": 0, "hits": 0}
            page = 1
            while True:
                if requests >= MAX_REQUESTS:
                    raise KursError(f"more than {MAX_REQUESTS} requests; lower cap_per_title or max_pages_per_query")
                if requests:
                    sleep(REQUEST_DELAY)
                total, rows = search(query, page, s["angebotsart"])
                requests += 1
                q.update(total=total, pages=page)
                q["fetched"] += len(rows)
                for rec in rows:
                    if add(rec, query) == t["id"]:
                        q["hits"] += 1
                done = not rows or page * PAGE_SIZE >= total
                if done or page >= max_pages or len(per_title[t["id"]]) >= cap:
                    break
                page += 1
            log.info("  %-22s %-36s total %5d, read %4d (%d pages), %d fit the title",
                     t["id"], query, q["total"], q["fetched"], q["pages"], q["hits"])
            queries.append(q)

    counts = {}
    for t in config["titles"]:
        qs = [q for q in queries if q["title"] == t["id"]]
        matched = len(per_title[t["id"]])
        complete = all(q["fetched"] >= q["total"] for q in qs)
        # Lower bound: the queries overlap (Bürokaufmann / Kaufmann Büromanagement are the
        # same occupation), so take the largest single query, scaled by the share of its
        # results read that fit the title, never below what was actually found.
        scaled = [q["total"] * q["hits"] / q["fetched"] for q in qs if q["fetched"]]
        counts[t["id"]] = {
            "academy": t["academy"], "postings": min(matched, cap), "matched": matched,
            "estimated": matched if complete else max(matched, round(max(scaled, default=0))),
            "complete": int(complete), "ba_total": sum(q["total"] for q in qs),
            "fetched": sum(q["fetched"] for q in qs), "queries": len(qs),
        }
        # Keep the cap: the first postings found are the most relevant ones.
        for ref in per_title[t["id"]][cap:]:
            found[ref]["dropped"] = True
    log.info("%d requests, %d unique postings, %d fit a job title",
             requests, len(found), sum(1 for p in found.values() if p["titles"]))
    return found, counts, queries


# --- Storage ------------------------------------------------------------------------------------

def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _flag(value):
    return "" if value is None else int(bool(value))


def posting_row(ref, hit, config, day, old=None):
    rec = hit["record"]
    loc = (rec.get("stellenlokationen") or [{}])[0]
    addr = loc.get("adresse") or {}
    academy_of = {t["id"]: t["academy"] for t in config["titles"]}
    first = hit["titles"][0]
    return {
        "refnr": ref,
        "title": (rec.get("stellenangebotsTitel") or "").strip(),
        "company": (rec.get("firma") or "").strip(),
        "city": normalise_city(addr.get("ort")),
        "region": addr.get("region") or "",
        "lat": round(loc["breite"], 4) if loc.get("breite") is not None else "",
        "lon": round(loc["laenge"], 4) if loc.get("laenge") is not None else "",
        "academy": academy_of[first],
        "job_title": first,
        "also": ";".join(hit["titles"][1:]),
        "query": hit["query"],
        "quereinstieg": _flag(rec.get("quereinstiegGeeignet")),
        "fulltime": _flag(rec.get("arbeitszeitVollzeit")),
        "parttime": int(any(rec.get(k) for k in ("arbeitszeitTeilzeitVormittag", "arbeitszeitTeilzeitNachmittag",
                                                  "arbeitszeitTeilzeitAbend", "arbeitszeitTeilzeitFlexibel"))),
        "minijob": _flag(rec.get("istGeringfuegigeBeschaeftigung")),
        "homeoffice": _flag(rec.get("homeofficemoeglich")),
        "published": rec.get("datumErsteVeroeffentlichung") or "",
        "first_seen": (old or {}).get("first_seen") or day,
        "last_seen": day,
    }


def merge(found, config, day, kurs_dir=KURS_DIR):
    """Today's postings merged into the stored rows (first_seen kept). Returns all rows."""
    old = {r["refnr"]: r for r in _read_csv(os.path.join(kurs_dir, "postings.csv"))}
    rows = dict(old)
    for ref, hit in found.items():
        if hit["titles"] and not hit.get("dropped"):
            rows[ref] = posting_row(ref, hit, config, day, old.get(ref))
    return list(rows.values())


def last_total(kurs_dir=KURS_DIR, before=None):
    """Postings kept on the newest day in counts.csv before `before`, or None."""
    rows = [r for r in _read_csv(os.path.join(kurs_dir, "counts.csv")) if not before or r["date"] < before]
    if not rows:
        return None
    day = max(r["date"] for r in rows)
    return sum(int(r["postings"]) for r in rows if r["date"] == day)


def save(found, counts, config, day, kurs_dir=KURS_DIR):
    postings = merge(found, config, day, kurs_dir)
    postings.sort(key=lambda r: (r["first_seen"], r["refnr"]))
    _write_csv(os.path.join(kurs_dir, "postings.csv"), POSTING_COLUMNS, postings)
    # Re-running a day replaces that day's counts.
    kept = [r for r in _read_csv(os.path.join(kurs_dir, "counts.csv")) if r["date"] != day]
    kept += [{"date": day, "job_title": tid, **c} for tid, c in counts.items()]
    kept.sort(key=lambda r: (r["date"], r["academy"], r["job_title"]))
    _write_csv(os.path.join(kurs_dir, "counts.csv"), COUNT_COLUMNS, kept)
    return postings


def save_raw(found, day, raw_dir=RAW_DIR):
    out = os.path.join(raw_dir, day)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "kurs.jsonl.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for hit in found.values():
            f.write(json.dumps({**hit["record"], "_query": hit["query"]}, ensure_ascii=False) + "\n")
    return path


# --- Report and main ----------------------------------------------------------------------------

def table(counts, config):
    names = {t["id"]: t["name"] for t in config["titles"]}
    lines = ["| Academy | Job title | Postings kept | Estimated on the board | Search total |",
             "|---|---|--:|--:|--:|"]
    for a in config["academies"]:
        for tid in a["titles"]:
            c = counts[tid]
            est = f"{c['estimated']}" if c["complete"] else f"≈ {c['estimated']}"
            lines.append(f"| {a['name']} | {names[tid]} | {c['postings']} | {est} | {c['ba_total']} |")
    return "\n".join(lines)


def run(day, dry_run=False, config_path=CONFIG_PATH, kurs_dir=KURS_DIR, raw_dir=RAW_DIR,
        search=search_page, sleep=time.sleep):
    config = load_config(config_path)
    started = time.time()
    found, counts, _ = collect(config, search, sleep)
    kept = sum(c["postings"] for c in counts.values())
    previous = last_total(kurs_dir, before=day)
    if kept == 0:
        raise KursError("no posting fits any job title; the search may have changed")
    if previous and kept < previous * MIN_RATIO_VS_LAST_RUN:
        raise KursError(f"only {kept} postings, down from {previous} on the last run; the search may have changed")
    log.info("%d postings kept in %.0f s", kept, time.time() - started)
    if not dry_run:
        save_raw(found, day, raw_dir)
        postings = save(found, counts, config, day, kurs_dir)
        log.info("wrote %s: %d postings in total, %d online today",
                 os.path.relpath(os.path.join(kurs_dir, "postings.csv"), ROOT),
                 len(postings), sum(r["last_seen"] == day for r in postings))
    return counts, config


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="search and print the counts, write nothing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    day = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    try:
        counts, config = run(day, dry_run=args.dry_run)
    except (api.ApiError, KursError, OSError, yaml.YAMLError) as e:
        log.error("Kurs-Radar skipped, nothing written: %s", e)
        return 1
    report = table(counts, config)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Kurs-Radar {day}\n\n{report}\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
