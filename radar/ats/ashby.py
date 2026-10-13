"""Ashby public job posting API (no key):
https://developers.ashbyhq.com/docs/public-job-posting-api

GET https://api.ashbyhq.com/posting-api/job-board/{slug} lists the published
jobs. Unlisted jobs (isListed false, meant for direct links only) are skipped.
"""

URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _place(address):
    postal = (address or {}).get("postalAddress") or {}
    return ", ".join(x for x in (postal.get("addressLocality"), postal.get("addressCountry")) if x)


def fetch(slug, get):
    data = get(URL.format(slug=slug))
    out = []
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        secondary = job.get("secondaryLocations") or []
        locations = [job.get("location") or "", _place(job.get("address"))]
        locations += [s.get("location") or "" for s in secondary] + [_place(s.get("address")) for s in secondary]
        country = ((job.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
        out.append({
            "title": job.get("title") or "",
            "locations": locations,
            "url": job.get("jobUrl") or "",
            "posted_at": (job.get("publishedAt") or "")[:10] or None,
            "remote": bool(job.get("isRemote")) or job.get("workplaceType") == "Remote",
            "country": country,
        })
    return out
