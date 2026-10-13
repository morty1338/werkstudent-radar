"""Personio XML job feed (public when the company turns it on):
https://developer.personio.de/docs/retrieving-open-job-positions

GET https://{slug}.jobs.personio.de/xml lists every open position; the job page
is https://{slug}.jobs.personio.de/job/{id}. Some accounts live on
jobs.personio.com instead, so a 404 on .de is retried there. The feed has descriptions
(jobDescriptions); only title, offices, link and date are kept.
"""

DOMAINS = ("jobs.personio.de", "jobs.personio.com")
URL = "https://{slug}.{domain}/xml?language=de"
JOB_URL = "https://{slug}.{domain}/job/{id}"


def _text(el, tag):
    found = el.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def fetch(slug, get):
    from . import BoardNotFound

    for i, domain in enumerate(DOMAINS):
        try:
            root = get(URL.format(slug=slug, domain=domain), kind="xml")
            break
        except BoardNotFound:
            if i == len(DOMAINS) - 1:
                raise
    out = []
    for pos in root.iter("position"):
        offices = [_text(pos, "office")]
        offices += [(o.text or "").strip() for o in pos.findall("additionalOffices/office")]
        out.append({
            "title": _text(pos, "name"),
            "locations": offices,
            "url": JOB_URL.format(slug=slug, domain=domain, id=_text(pos, "id")),
            "posted_at": _text(pos, "createdAt")[:10] or None,
            "remote": False,
            "country": None,
        })
    return out
