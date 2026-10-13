"""Lever Postings API (public, no key): https://github.com/lever/postings-api

GET https://api.lever.co/v0/postings/{slug}?mode=json lists the published postings;
accounts in Lever's EU region answer on api.eu.lever.co instead, so a 404 on the
global host is retried there. The response carries descriptions; only title,
locations, link and date are kept.
"""

from datetime import datetime, timezone

HOSTS = ("https://api.lever.co", "https://api.eu.lever.co")
PATH = "/v0/postings/{slug}?mode=json&skip={skip}&limit={limit}"
LIMIT = 100


def _date(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def fetch(slug, get):
    from . import BoardNotFound

    for i, host in enumerate(HOSTS):
        try:
            postings, skip = [], 0
            while True:
                page = get(host + PATH.format(slug=slug, skip=skip, limit=LIMIT))
                postings += page
                if len(page) < LIMIT:
                    break
                skip += LIMIT
            break
        except BoardNotFound:
            if i == len(HOSTS) - 1:
                raise
    out = []
    for p in postings:
        cats = p.get("categories") or {}
        out.append({
            "title": p.get("text") or "",
            "locations": [cats.get("location") or ""] + list(cats.get("allLocations") or []),
            "url": p.get("hostedUrl") or "",
            "posted_at": _date(p.get("createdAt")),
            "remote": p.get("workplaceType") == "remote",
            "country": p.get("country"),
        })
    return out
