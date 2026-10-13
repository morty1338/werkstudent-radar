"""Werkstudent jobs from company career pages, read through public ATS job-board APIs.

Usage:
    python -m radar.ats [--company NAME]    print the jobs found for the enabled companies

One small adapter per applicant tracking system (greenhouse.py, lever.py,
ashby.py, personio.py) turns the board's public response into raw postings:
{title, locations, url, posted_at, remote, country}. This module keeps the
Werkstudent / Working Student titles in Berlin or München (or remote in Germany,
flagged separately) and normalises them to:

    company, title, city, remote, url, posted_at, source_ats

Job texts are never kept. Requests go through radar.companies.Fetcher: our
User-Agent, robots.txt checked per host, spaced out, retried with backoff.
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET

from .. import companies as C
from . import ashby, greenhouse, lever, personio

ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "personio": personio}
MAX_BYTES = 30_000_000   # a big Lever board with descriptions is several MB

WERKSTUDENT = re.compile(r"werk[\s-]?student|werkstudierend|working[\s-]?student", re.I)
# Open applications are listed like jobs on many boards but aren't one.
NOT_A_JOB = re.compile(r"initiativ|blindbewerbung|speculative application|open application|talent ?pool", re.I)
CITY_PATTERNS = {
    "Berlin": re.compile(r"\bberlin\b", re.I),
    "München": re.compile(r"\bm(?:ü|ue|u)nchen\b|\bmunich\b", re.I),
}
REMOTE = re.compile(r"\bremote\b|home[\s-]?office|mobiles arbeiten|\bfully[\s-]distributed\b", re.I)
GERMANY = re.compile(r"\b(?i:germany|deutschland)\b|\bDEU?\b")   # the codes only in capitals


class AtsError(Exception):
    """The board couldn't be read (robots.txt, HTTP error, unexpected format)."""


class BoardNotFound(AtsError):
    """The ATS answered 404: the slug is wrong or the board was taken down."""


def get(fetcher, url, kind="json"):
    """GET an ATS API url; returns parsed JSON or an XML root element."""
    if not fetcher.allowed(url):
        raise AtsError(f"robots.txt disallows {url}")
    resp, body = fetcher.get(url, max_bytes=MAX_BYTES)
    if resp.status_code == 404:
        raise BoardNotFound(f"HTTP 404 from {url}")
    if resp.status_code >= 400:
        raise AtsError(f"HTTP {resp.status_code} from {url}")
    try:
        return json.loads(body) if kind == "json" else ET.fromstring(body.encode("utf-8"))
    except (ValueError, ET.ParseError) as e:
        raise AtsError(f"unreadable {kind} from {url}: {e}") from e


def is_werkstudent(title):
    return bool(WERKSTUDENT.search(title or "")) and not NOT_A_JOB.search(title or "")


def cities_of(locations):
    """Berlin / München named in any of the location strings, in that order."""
    text = " | ".join(x for x in locations if x)
    return [city for city, rx in CITY_PATTERNS.items() if rx.search(text)]


def normalise(company, ats, raw):
    """Rows for one raw posting: one per matching city in the company's cities, or
    one with city "" when it is remote in Germany without a Berlin/München office."""
    if not is_werkstudent(raw["title"]):
        return []
    locations = [x for x in raw.get("locations") or [] if x]
    text = " | ".join(locations)
    remote = bool(raw.get("remote")) or bool(REMOTE.search(text))
    country = (raw.get("country") or "").upper()
    in_germany = country in ("DE", "DEU", "GERMANY", "DEUTSCHLAND") or bool(GERMANY.search(text)) or bool(cities_of(locations))
    if ats == "personio" and not country:
        in_germany = in_germany or remote   # Personio boards have no country field; ours are German companies
    wanted = company.get("cities") or list(CITY_PATTERNS)
    cities = [c for c in cities_of(locations) if c in wanted]
    base = {
        "company": company["name"],
        "title": re.sub(r"\s+", " ", raw["title"]).strip(),
        "remote": remote and in_germany,
        "url": raw["url"],
        "posted_at": raw.get("posted_at"),
        "source_ats": ats,
    }
    if cities:
        return [{**base, "city": city} for city in cities]
    if remote and in_germany:
        return [{**base, "city": ""}]
    return []


def fetch_company(company, fetcher):
    """Werkstudent jobs of one company; raises AtsError when its board can't be read."""
    adapter = ADAPTERS.get(company.get("ats"))
    if adapter is None:
        raise AtsError(f"no adapter for ats {company.get('ats')!r}")
    raws = adapter.fetch(company["slug"], lambda url, kind="json": get(fetcher, url, kind))
    rows = [row for raw in raws for row in normalise(company, company["ats"], raw)]
    seen, unique = set(), []
    for r in rows:
        key = (r["url"], r["city"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique, len(raws)


def fetch_all(companies, fetcher=None, log=print):
    """Jobs from every enabled company, plus a status per company:
    {name: {"ok": bool, "board": n postings on the board, "jobs": n kept, "error": str}}."""
    fetcher = fetcher or C.Fetcher()
    jobs, status = [], {}
    for company in companies:
        if not company.get("enabled"):
            continue
        try:
            rows, board = fetch_company(company, fetcher)
            jobs += rows
            status[company["name"]] = {"ok": True, "board": board, "jobs": len(rows), "error": None}
            log(f"{company['name']:<40} {company['ats']:<10} {board:>4} on board, {len(rows):>3} Werkstudent in Berlin/München")
        except (AtsError, C.requests.RequestException) as e:
            status[company["name"]] = {"ok": False, "board": None, "jobs": 0, "error": str(e),
                                       "not_found": isinstance(e, BoardNotFound)}
            log(f"{company['name']:<40} {company['ats']:<10} FAILED: {e}")
    return jobs, status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", help="only this company (name as in companies.yaml)")
    args = parser.parse_args(argv)
    companies = C.load()
    if args.company:
        companies = [c for c in companies if c["name"].casefold() == args.company.casefold()]
        for c in companies:
            c["enabled"] = True
    jobs, _ = fetch_all(companies)
    print()
    for j in sorted(jobs, key=lambda j: (j["city"], j["company"], j["title"])):
        flag = " [remote]" if j["remote"] else ""
        print(f"{j['city'] or 'remote DE':<10} {j['company']:<28} {j['posted_at'] or '':<10}  {j['title']}{flag}\n{'':<40}{j['url']}")


if __name__ == "__main__":
    sys.exit(main())
