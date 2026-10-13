"""Tell an n8n workflow about today's new postings (it filters them and posts to Telegram).

Usage:
    python -m radar.notify             after the daily build: one signed POST, if configured
    python -m radar.notify --dry-run   print the payload instead of sending it

New means new since the previous run: postings whose first_seen is the latest
collection day, by the same rules as the RSS feeds (feeds.new_postings: not a
source's first collection day, not an old posting listed again), plus jobs first
seen today on company career pages (docs/data/companies.json) that no other
source lists. The payload has facts and links only, never job texts.

Configuration comes from the environment (GitHub Secrets in the daily workflow):
    N8N_WEBHOOK_URL   the n8n Webhook node's production URL (https)
    WEBHOOK_SECRET    shared secret; n8n recomputes the signature and drops other calls

Signature: X-Radar-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>." + body)>,
with the Unix time in X-Radar-Timestamp, so a recorded call can't be replayed later.
Without both settings the step does nothing; a failed call is reported but never
fails the run (the exit code is always 0).
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlsplit

import requests

from . import build, feeds

SITE_URL = feeds.SITE_URL
COMPANIES_JSON = os.path.join(build.OUT_DIR, "companies.json")
VERSION = 1
MAX_JOBS = 500            # a normal day has a few hundred; the rest only counts in `total`
TIMEOUT = 20
ATTEMPTS = 3
USER_AGENT = "werkstudent-radar/1.0 (+https://github.com/morty1338/werkstudent-radar)"


def new_postings(db):
    """Postings first seen on the latest collection day (BA and Arbeitnow), with their skills."""
    as_of = db.execute("SELECT MAX(last_seen) FROM jobs").fetchone()[0]
    today = [p for p in feeds.new_postings(db) if p["first_seen"] == as_of]
    if not today:
        return as_of, []
    refnrs = [p["refnr"] for p in today]
    extra = {r["refnr"]: r for r in db.execute(
        f"SELECT refnr, remote FROM jobs WHERE refnr IN ({','.join('?' * len(refnrs))})", refnrs)}
    skills = {}
    for r in db.execute(f"""
        SELECT js.refnr, s.label FROM job_skills js JOIN skills s ON s.id = js.skill
        WHERE js.refnr IN ({','.join('?' * len(refnrs))}) ORDER BY js.refnr, s.label
    """, refnrs):
        skills.setdefault(r["refnr"], []).append(r["label"])
    labels = {r["id"]: r["label"] for r in db.execute("SELECT id, label FROM categories")}
    return as_of, [{
        "id": p["refnr"],
        "title": p["title"],
        "company": p["company"],
        "city": p["city"],
        "field": p["category"],
        "field_label": labels.get(p["category"], "Other"),
        "skills": skills.get(p["refnr"], []),
        "pay": p["pay"],
        "german": p["german"],
        "no_german": p["german"] in ("none", "plus"),
        "remote": bool(extra[p["refnr"]]["remote"]),
        "source": p["source"],
        "url": p["url"] or feeds.JOB_URL.format(refnr=p["refnr"]),
    } for p in today]


def new_career_jobs(companies, as_of):
    """Jobs first seen today on company career pages that no other source lists."""
    if not companies or companies.get("as_of") != as_of:
        return []
    labels = companies.get("field_labels") or {}
    return [{
        "id": f"c:{j['url']}",
        "title": j["title"],
        "company": j["company"],
        "city": j["city"],                    # "" = remote anywhere in Germany
        "field": j["field"],
        "field_label": labels.get(j["field"], "Other"),
        "skills": [],
        "pay": None,
        "german": "",                         # unknown: career pages are read without their texts
        "no_german": False,
        "remote": bool(j.get("remote")),
        "source": "career_page",
        "url": j["url"],
    } for j in companies.get("jobs") or [] if j.get("new") and not j.get("also_on")]


def payload(as_of, jobs, *, test=False):
    return {
        "event": "new_postings",
        "version": VERSION,
        "as_of": as_of,
        "test": test,
        "site": SITE_URL,
        "total": len(jobs),
        "jobs": jobs[:MAX_JOBS],
    }


def encode(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(secret, timestamp, body):
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def headers(secret, body, now=None):
    timestamp = str(int(now if now is not None else time.time()))
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "X-Radar-Timestamp": timestamp,
        "X-Radar-Signature": sign(secret, timestamp, body),
    }


def url_ok(url):
    """https only (the payload and signature shouldn't travel in clear text); http for localhost tests."""
    parts = urlsplit(url)
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"))


def send(url, secret, data, *, session=None, sleep=time.sleep):
    """POST the payload; returns (ok, message). Never raises, never prints the URL (it's a secret)."""
    body = encode(data)
    http = session or requests
    message = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = http.post(url, data=body, headers=headers(secret, body), timeout=TIMEOUT)
        except requests.RequestException as e:
            message = f"no answer ({type(e).__name__})"     # the exception text would contain the URL
        else:
            if 200 <= r.status_code < 300:
                return True, f"HTTP {r.status_code}"
            message = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                return False, message                       # wrong URL or rejected: retrying won't help
        if attempt < ATTEMPTS:
            sleep(2 ** attempt)
    return False, message


def load_companies(path=COMPANIES_JSON):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def collect(jobs_csv=build.JOBS_CSV, companies_json=COMPANIES_JSON):
    as_of, jobs = new_postings(build.load_db(jobs_csv))
    return as_of, jobs + new_career_jobs(load_companies(companies_json), as_of)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    args = ap.parse_args(argv)

    as_of, jobs = collect()
    data = payload(as_of, jobs)
    if args.dry_run:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
    secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not url or not secret:
        print("Telegram alerts: N8N_WEBHOOK_URL or WEBHOOK_SECRET not set; nothing sent.")
        return 0
    if not url_ok(url):
        print("Telegram alerts: N8N_WEBHOOK_URL must start with https://; nothing sent.")
        return 0
    ok, message = send(url, secret, data)
    sources = {s: sum(j["source"] == s for j in jobs) for s in ("ba", "arbeitnow", "career_page")}
    detail = ", ".join(f"{n} {s}" for s, n in sources.items() if n) or "none"
    if ok:
        print(f"Telegram alerts: sent {len(data['jobs'])} new postings for {as_of} ({detail}): {message}")
    else:
        print(f"::warning::Telegram alerts: the n8n webhook failed ({message}); {len(jobs)} new postings not sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
