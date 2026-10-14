"""Werkstudent Radar data for the MCP server: where it comes from and how it fits together.

Reads the same JSON files as the website (docs/data/, written by radar/build.py):

    summary.json   headline numbers and per-field / per-city / per-skill aggregates
    checker.json   one positional row per posting online today:
                   [field, city, german, pay, [skills], role_first, role_city_first, company]
                   with the field, city, German level and skill lists as lookup tables
    postings.json  title, company, city, publication date and link of the same
                   postings, in the same order (only valid for the same as_of)

Source: the files in the repository (default) or, with live=True, the published
files on GitHub Pages, kept in memory for `ttl` seconds. Local files are re-read
when they change on disk, so a running server picks up a `git pull`.

Read-only: nothing is written anywhere. No job texts exist in these files.
"""

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
LIVE_URL = "https://morty1338.github.io/werkstudent-radar/data/"
USER_AGENT = "werkstudent-radar-mcp/0.1 (+https://github.com/morty1338/werkstudent-radar)"
LIVE_TTL = 600        # GitHub Pages caches files for 10 minutes too
TIMEOUT = 20

# checker.json "jobs" row positions (same as J in docs/assets/app.js).
CAT, CITY, DE, PAY, SKILLS, ROLE, ROLE_CITY, COMPANY, NICE, LLM = range(10)   # NICE, LLM: newer checker.json only

log = logging.getLogger(__name__)   # stderr only: on stdio, stdout carries the protocol


class DataError(Exception):
    """The data files are missing, unreadable or don't fit together."""


class Source:
    """Loads docs/data/<name>.json from disk or from the live site."""

    def __init__(self, data_dir=DATA_DIR, live=False, base_url=LIVE_URL, ttl=LIVE_TTL, opener=None):
        self.data_dir, self.live, self.base_url, self.ttl = data_dir, live, base_url, ttl
        self.opener = opener or urllib.request.urlopen
        self._cache = {}    # name -> (stamp, data); stamp is mtime (local) or fetch time (live)

    @property
    def where(self):
        return self.base_url if self.live else self.data_dir

    def get(self, name):
        return self._get_live(name) if self.live else self._get_local(name)

    def _get_local(self, name):
        path = os.path.join(self.data_dir, f"{name}.json")
        try:
            mtime = os.path.getmtime(path)
        except OSError as e:
            raise DataError(f"{path} not found; run the pipeline or start with --live") from e
        cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise DataError(f"can't read {path}: {e}") from e
        self._cache[name] = (mtime, data)
        return data

    def _get_live(self, name):
        cached = self._cache.get(name)
        if cached and time.monotonic() - cached[0] < self.ttl:
            return cached[1]
        url = f"{self.base_url}{name}.json"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self.opener(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:   # network errors, HTTP errors, bad JSON
            if cached:
                log.warning("fetching %s failed (%s); using the copy from %.0f s ago", url, e, time.monotonic() - cached[0])
                return cached[1]
            raise DataError(f"can't fetch {url}: {e}") from e
        self._cache[name] = (time.monotonic(), data)
        return data


@dataclass
class Dataset:
    """One day's data with lookup tables; built by `load`."""

    as_of: str
    summary: dict
    checker: dict
    fields: list            # field ids, in checker order
    field_labels: dict      # field id -> English label
    cities: list            # city names, in checker order
    skills: list            # [{"id", "label", "group", "jobs"}], in checker order
    jobs: list              # checker rows
    postings: list | None   # postings.json rows by position, or None if they don't match
    posting_fields: list = field(default_factory=list)
    posting_url: str = ""

    def posting(self, i):
        """Title, company, city, published and link of posting i (None without postings.json)."""
        if self.postings is None:
            return None
        row = dict(zip(self.posting_fields, self.postings[i]))
        row["url"] = row.get("url") or self.posting_url.format(refnr=row.get("refnr", ""))
        return row


def load(source, prefix=""):
    """summary + checker + postings as a Dataset. postings.json is only used when it
    belongs to the same day and has one row per checker row (they are matched by position).
    prefix "jobs_" reads the Jobs mode files (jobs_summary.json, …) in the same shape."""
    summary, checker = source.get(f"{prefix}summary"), source.get(f"{prefix}checker")
    if checker.get("as_of") != summary.get("as_of"):
        log.warning("summary.json (%s) and checker.json (%s) are from different days", summary.get("as_of"), checker.get("as_of"))
    postings, fields, url = None, [], ""
    try:
        p = source.get(f"{prefix}postings")
        if p.get("as_of") == checker.get("as_of") and len(p.get("rows") or []) == len(checker["jobs"]):
            postings, fields, url = p["rows"], p["fields"], p.get("url", "")
        else:
            log.warning("postings.json doesn't match checker.json (as_of %s vs %s); titles unavailable", p.get("as_of"), checker.get("as_of"))
    except DataError as e:
        log.warning("postings.json unavailable: %s", e)
    return Dataset(
        as_of=checker["as_of"],
        summary=summary,
        checker=checker,
        fields=checker["categories"],
        field_labels=checker["category_labels"],
        cities=[c["name"] for c in checker["cities"]],
        skills=checker["skills"],
        jobs=checker["jobs"],
        postings=postings,
        posting_fields=fields,
        posting_url=url,
    )


class Store:
    """The current Dataset, rebuilt only when one of the files changed (or the live cache expired)."""

    def __init__(self, source, prefix=""):
        self.source, self.prefix = source, prefix
        self._key = None
        self._data = None

    def data(self):
        parts = [self.source.get(f"{self.prefix}{n}") for n in ("summary", "checker")]
        key = tuple(id(p) for p in parts)
        if key != self._key:
            self._data = load(self.source, self.prefix)
            self._key = key
        return self._data
