"""Greenhouse Job Board API (public, no key): https://docs.greenhouse.io/job-board.html

GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs lists the published
jobs without descriptions (those only come with ?content=true, which we don't ask for).
"""

URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def fetch(slug, get):
    data = get(URL.format(slug=slug))
    out = []
    for job in data.get("jobs") or []:
        location = (job.get("location") or {}).get("name") or ""
        offices = [o.get("location") or o.get("name") or "" for o in job.get("offices") or []]
        out.append({
            "title": job.get("title") or "",
            "locations": [location] + offices,
            "url": job.get("absolute_url") or "",
            "posted_at": (job.get("first_published") or job.get("updated_at") or "")[:10] or None,
            "remote": False,
            "country": None,
        })
    return out
