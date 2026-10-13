"""Company career pages in Berlin and München (data/companies.yaml).

Usage:
    python -m radar.companies candidates [--min 2]   employers in our postings that aren't listed yet
    python -m radar.companies detect URL             which ATS a careers page uses
    python -m radar.companies detect --all [--force] fill ats + slug in companies.yaml

Each listed company has a careers page. If that page runs on an applicant
tracking system with a public job-board API (Greenhouse, Lever, Ashby, Personio,
SmartRecruiters), its jobs can be read from there; everything else stays
"unsupported" (no per-site scrapers). Detection looks at the redirects and the
links, iframes and scripts of the careers page, then of up to three linked job
pages on it. robots.txt is respected and requests are spaced out.
"""

import argparse
import csv
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANIES_PATH = os.path.join(ROOT, "data", "companies.yaml")
JOBS_PATH = os.path.join(ROOT, "data", "jobs.csv")

CITIES = ("Berlin", "München")
SUPPORTED = ("greenhouse", "lever", "ashby", "personio", "smartrecruiters")
USER_AGENT = "werkstudent-radar/0.1 (+https://github.com/morty1338/werkstudent-radar)"
TIMEOUT = 20
DELAY = 1.5          # seconds between companies (and between pages of one company)
MAX_BYTES = 3_000_000
MAX_FOLLOW = 3       # linked job pages checked when the careers page itself shows no ATS

HEADER = """\
# Company career pages for the "Companies" tab (Berlin and München).
#
# name         company name as shown on the site
# cities       cities whose jobs are kept (Berlin, München)
# careers_url  the company's careers page
# ats          greenhouse | lever | ashby | personio | smartrecruiters | unsupported
#              (filled by `python -m radar.companies detect --all`)
# slug         the company's id on that ATS
# enabled      fetched by the daily run (only supported ATS)
# ba_names     employer names in Bundesagentur postings, to mark jobs "also on BA"
# note         what detection saw: another ATS, a 404, robots.txt, ...
"""

# (ats, regex with the slug as group 1). Order matters: the first ATS that matches wins.
ATS_PATTERNS = [
    ("greenhouse", r"greenhouse\.io/[^\s\"'<>]*?[?&]for=([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)"),
    ("lever", r"api(?:\.eu)?\.lever\.co/v0/postings/([A-Za-z0-9_.-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("personio", r"([a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?:de|com)"),
    ("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
]
# Path segments on ATS hosts that aren't company slugs.
NOT_SLUGS = {"embed", "js", "v0", "v1", "api", "static", "assets", "oneclick-ui", "sr-jobs", "jobs", "widget"}

# A supported ATS named on the page without a usable slug (e.g. job ids only).
SUPPORTED_HINTS = {
    "greenhouse": r"greenhouse\.io",
    "lever": r"lever\.co\b",
    "ashby": r"ashbyhq\.com",
    "personio": r"personio\.(?:de|com)",
    "smartrecruiters": r"smartrecruiters",
}

# Other systems, only named in the note (no adapters for them).
OTHER_ATS = {
    "workday": r"myworkdayjobs\.com|myworkdaysite\.com|\.wd\d\.myworkday",
    "successfactors": r"successfactors\.(?:com|eu)|jobs\.sap\.com|sapsf\.",
    "softgarden": r"softgarden\.(?:io|de)",
    "recruitee": r"recruitee\.com",
    "join": r"join\.com/companies",
    "teamtailor": r"teamtailor\.com",
    "d.vinci": r"dvinci(?:-easy)?\.(?:de|com)|dvinci-hr",
    "rexx": r"rexx-systems\.com|rexx-recruitment",
    "onlyfy": r"onlyfy\.(?:jobs|io)|prescreen\.io",
    "jobvite": r"jobvite\.com",
    "icims": r"icims\.com",
    "taleo": r"taleo\.net",
    "phenom": r"phenompeople\.com|phenom\.com",
    "avature": r"avature\.net",
    "umantis": r"umantis\.com",
    "eightfold": r"eightfold\.ai",
    "beesite": r"beesite\.de",
    "concludis": r"concludis\.de",
    "talention": r"talention\.(?:de|com)",
    "pitchyou": r"pitchyou\.de",
    "coveto": r"coveto\.de",
    "mhm": r"mhm-portal\.de",
    "kenjo": r"kenjo\.io",
    "factorial": r"factorialhr\.",
}

JOB_LINK = re.compile(r"(?:jobs?|stellen|karriere|career|vacanc|offene|open-positions|positions)", re.I)
HREF = re.compile(r"""(?:href|src)\s*=\s*["']([^"'#]+)["']""", re.I)


# --- The list ------------------------------------------------------------------------------------

def load(path=COMPANIES_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("companies") or []


def save(companies, path=COMPANIES_PATH):
    companies = sorted(companies, key=lambda c: c["name"].casefold())
    body = yaml.safe_dump({"companies": companies}, allow_unicode=True, sort_keys=False,
                          default_flow_style=None, width=120)
    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER + "\n" + body)


def candidates(min_jobs=2, days=7, jobs_path=JOBS_PATH, companies=None):
    """Bundesagentur employers with at least `min_jobs` postings in Berlin or München
    seen in the last `days` days that aren't in the list yet: [(employer, {city: n})]."""
    with open(jobs_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    last = max(r["last_seen"] for r in rows)
    since = (date.fromisoformat(last) - timedelta(days=days)).isoformat()
    counts = defaultdict(Counter)
    for r in rows:
        if r["source"] == "ba" and r["city"] in CITIES and r["last_seen"] >= since:
            counts[r["company"]][r["city"]] += 1
    known = {n for c in (companies if companies is not None else load()) for n in c.get("ba_names") or []}
    found = [(name, dict(c)) for name, c in counts.items() if max(c.values()) >= min_jobs and name not in known]
    return sorted(found, key=lambda x: (-sum(x[1].values()), x[0]))


# --- ATS detection -------------------------------------------------------------------------------

def match_ats(text):
    """The first supported ATS whose URL pattern occurs in `text`: (ats, slug) or None.
    With several slugs for one ATS, the most frequent one wins."""
    for ats in SUPPORTED:
        slugs = Counter()
        for name, rx in ATS_PATTERNS:
            if name != ats:
                continue
            for m in re.finditer(rx, text, re.I):
                slug = m.group(1).strip(".-")
                if slug and slug.lower() not in NOT_SLUGS:
                    slugs[slug] += 1
        if slugs:
            return ats, slugs.most_common(1)[0][0]
    return None


def match_other(text):
    """Notes on known ATS in `text` without a usable slug: supported ones named
    without a slug ("uses lever, slug not found") and others without an adapter."""
    hints = [f"uses {name}, slug not found" for name, rx in SUPPORTED_HINTS.items() if re.search(rx, text, re.I)]
    return hints + [name for name, rx in OTHER_ATS.items() if re.search(rx, text, re.I)]


class Fetcher:
    """GET with a fixed User-Agent, robots.txt check per host, retries with backoff."""

    def __init__(self, delay=DELAY, sleep=time.sleep):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*"})
        self.robots = {}
        self.delay = delay
        self.sleep = sleep
        self.last = 0.0

    def allowed(self, url):
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.session.get(host + "/robots.txt", timeout=TIMEOUT)
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self.robots[host] = rp
        return self.robots[host].can_fetch(USER_AGENT, url)

    def get(self, url, retries=3):
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0:
            self.sleep(wait)
        error = None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=TIMEOUT, stream=True)
                body = resp.raw.read(MAX_BYTES, decode_content=True).decode(resp.encoding or "utf-8", "replace")
                self.last = time.monotonic()
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                return resp, body
            except requests.RequestException as e:
                error = e
                self.last = time.monotonic()
                self.sleep(2 ** attempt * 2)
        raise error


def detect(url, fetcher=None):
    """Which ATS the careers page at `url` uses: {"ats", "slug", "note"}."""
    fetcher = fetcher or Fetcher()
    url = url.strip()
    if not urlparse(url).scheme:
        url = "https://" + url
    hit = match_ats(url)
    if hit:
        return {"ats": hit[0], "slug": hit[1], "note": None}
    pages, others = [url], []
    for i, page in enumerate(pages):
        if not fetcher.allowed(page):
            if i == 0:
                return {"ats": "unsupported", "slug": None, "note": "robots.txt disallows the careers page"}
            continue
        try:
            resp, body = fetcher.get(page)
        except requests.RequestException as e:
            if i == 0:
                return {"ats": "unsupported", "slug": None, "note": f"fetch failed: {type(e).__name__}"}
            continue
        if i == 0 and resp.status_code >= 400:
            return {"ats": "unsupported", "slug": None, "note": f"HTTP {resp.status_code}"}
        trail = " ".join([r.url for r in resp.history] + [resp.url])
        refs = [urljoin(resp.url, h) for h in HREF.findall(body)]
        hit = match_ats(trail) or match_ats(" ".join(refs)) or match_ats(body)
        if hit:
            note = None if i == 0 else f"found on {resp.url}"
            return {"ats": hit[0], "slug": hit[1], "note": note}
        others += match_other(trail + " " + body)
        if i == 0:
            site = urlparse(resp.url).netloc.split(".")[-2:]
            links = [r for r in dict.fromkeys(refs)
                     if urlparse(r).netloc.split(".")[-2:] == site and JOB_LINK.search(urlparse(r).path)
                     and r.split("#")[0].rstrip("/") != resp.url.rstrip("/")
                     and not re.search(r"\.(?:css|js|png|jpe?g|svg|gif|webp|ico|pdf|woff2?)(?:\?|$)", r, re.I)]
            pages += links[:MAX_FOLLOW]
    others = list(dict.fromkeys(others))
    return {"ats": "unsupported", "slug": None, "note": ", ".join(others) if others else "no known ATS found"}


def detect_all(companies, force=False, fetcher=None, log=print):
    """Fill ats, slug, note and enabled for every company with a careers_url
    (only those without an ats yet, unless `force`)."""
    fetcher = fetcher or Fetcher()
    for c in companies:
        if not c.get("careers_url") or (c.get("ats") and not force):
            continue
        result = detect(c["careers_url"], fetcher)
        c.update(result)
        c["enabled"] = result["ats"] in SUPPORTED
        log(f"{c['name']:<40} {result['ats']:<16} {result['slug'] or '':<24} {result['note'] or ''}")
    return companies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("candidates")
    p.add_argument("--min", type=int, default=2)
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("detect")
    p.add_argument("url", nargs="?")
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "candidates":
        for name, cities in candidates(args.min, args.days):
            print(f"{sum(cities.values()):>3}  {name}  " + ", ".join(f"{k} {v}" for k, v in cities.items()))
    elif args.all:
        companies = load()
        detect_all(companies, force=args.force)
        save(companies)
        n = Counter(c.get("ats") or "—" for c in companies)
        print("\n" + ", ".join(f"{k}: {v}" for k, v in n.most_common()))
    elif args.url:
        print(detect(args.url))
    else:
        parser.error("detect needs a URL or --all")


if __name__ == "__main__":
    sys.exit(main())
