"""Read-only check of the live services before a demo: is the site up and today's data on it?

Usage:
    python -m radar.smoke    one line per check, exit 1 when one fails

Checks the published site (GitHub Pages), that its data files were built from a recent
collection (the daily run's as_of, at most MAX_AGE_DAYS before today in Berlin), and the
hosted MCP server's /health. Only GET requests; nothing is written or sent anywhere.
"""

import argparse
import json
import sys
import time
from datetime import date, datetime

import requests

from .feeds import BERLIN, SITE_URL

MCP_URL = "https://radar-mcp.vercel.app"   # the address in the README and the site's #claude card
MAX_AGE_DAYS = 1      # as_of is the collection day in Berlin: today after the morning run, yesterday before it
TIMEOUT = 20
DATA_FILES = {        # file -> label; each has a top-level as_of
    "summary.json": "Werkstudent data",
    "jobs_summary.json": "Jobs mode data",
    "companies.json": "Company career pages",
    "kurs.json": "Opportunities",
}


def fetch(url):
    """(HTTP status, body text); status 0 with the error type when the request failed."""
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "werkstudent-radar-smoke"})
    except requests.RequestException as e:
        return 0, type(e).__name__
    return resp.status_code, resp.text


def _json(body):
    try:
        return json.loads(body)
    except ValueError:
        return None


def _status(status, body):
    return f"HTTP {status}" if status else body   # status 0: body is the error type


def _age(as_of, today):
    """Days from as_of to today, or None when as_of isn't a date."""
    try:
        return (today - date.fromisoformat(as_of)).days
    except (TypeError, ValueError):
        return None


def checks(get, today):
    """Yield (ok, name, detail) per check; get(url) -> (status, text) does the I/O."""
    status, body = get(SITE_URL)
    yield status == 200, "Site answers", _status(status, body)

    for name, label in DATA_FILES.items():
        status, body = get(f"{SITE_URL}data/{name}?t={int(time.time())}")   # past Pages' 10-minute cache
        data = _json(body) if status == 200 else None
        as_of = data.get("as_of") if isinstance(data, dict) else None
        if not as_of:
            yield False, label, f"{_status(status, body)}, no as_of"
            continue
        age = _age(as_of, today)
        if age is None:
            yield False, label, f"as_of {as_of!r} is not a date"
            continue
        yield 0 <= age <= MAX_AGE_DAYS, label, f"as_of {as_of} ({age} days old)"

    status, body = get(f"{MCP_URL}/health")
    data = _json(body) if status == 200 else None
    ok = isinstance(data, dict) and data.get("status") == "ok"
    detail = _status(status, body)
    if ok and data.get("data_as_of"):   # None until the server read its data once (cold start)
        age = _age(data["data_as_of"], today)
        ok = age is not None and 0 <= age <= MAX_AGE_DAYS
        detail += f", data as of {data['data_as_of']}"
    yield ok, "MCP server", detail


def main(argv=None, today=None):
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    today = today or datetime.now(BERLIN).date()
    failed = 0
    for ok, name, detail in checks(fetch, today):
        failed += not ok
        print(f"{'✓' if ok else '✗'} {name}: {detail}")
    print("All checks passed." if not failed else f"{failed} check(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
