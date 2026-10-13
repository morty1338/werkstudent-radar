import csv

import pytest
import requests

from radar import companies as C


@pytest.mark.parametrize("text, expected", [
    ("https://boards.greenhouse.io/celonis/jobs/123", ("greenhouse", "celonis")),
    ("https://job-boards.eu.greenhouse.io/n26", ("greenhouse", "n26")),
    ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=contentful">', ("greenhouse", "contentful")),
    ("https://jobs.lever.co/pitch/abc-def", ("lever", "pitch")),
    ("https://jobs.eu.lever.co/hellofresh", ("lever", "hellofresh")),
    ("https://jobs.ashbyhq.com/trade-republic", ("ashby", "trade-republic")),
    ("https://gvc2u.jobs.personio.de/job/116056", ("personio", "gvc2u")),
    ("https://acme-gmbh.jobs.personio.com/", ("personio", "acme-gmbh")),
    ("https://jobs.smartrecruiters.com/BoschGroup/7439", ("smartrecruiters", "BoschGroup")),
    ("https://api.smartrecruiters.com/v1/companies/AUTO1/postings", ("smartrecruiters", "AUTO1")),
])
def test_match_ats(text, expected):
    assert C.match_ats(text) == expected


def test_match_ats_ignores_non_slugs_and_picks_the_most_frequent():
    assert C.match_ats("https://boards.greenhouse.io/embed/job_app") is None
    text = "jobs.lever.co/acme/1 jobs.lever.co/acme/2 jobs.lever.co/other/3"
    assert C.match_ats(text) == ("lever", "acme")
    assert C.match_ats("https://www.example.com/karriere") is None


def test_match_other_names_systems_without_adapter():
    assert C.match_other("https://bmw.wd3.myworkdayjobs.com/x") == ["workday"]
    assert C.match_other('{"smartrecruitersJobId": "9"}') == ["uses smartrecruiters, slug not found"]


class FakeResponse:
    def __init__(self, url, status=200, history=()):
        self.url, self.status_code, self.history = url, status, list(history)


class FakeFetcher:
    """Serves pages from a dict; records which URLs were fetched."""

    def __init__(self, pages, disallow=()):
        self.pages, self.disallow, self.fetched = pages, set(disallow), []

    def allowed(self, url):
        return url not in self.disallow

    def get(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise requests.ConnectionError(url)
        page = self.pages[url]
        if isinstance(page, tuple):            # (status, body) or (final_url, body, history)
            if isinstance(page[0], int):
                return FakeResponse(url, page[0]), page[1]
            return FakeResponse(page[0], history=[FakeResponse(u) for u in page[2]]), page[1]
        return FakeResponse(url), page


def test_detect_from_redirect():
    f = FakeFetcher({"https://acme.de/jobs": ("https://jobs.ashbyhq.com/acme", "<html></html>", ["https://acme.de/jobs"])})
    assert C.detect("https://acme.de/jobs", f) == {"ats": "ashby", "slug": "acme", "note": None}


def test_detect_from_iframe_and_from_url_without_fetching():
    f = FakeFetcher({"https://acme.de/karriere": '<iframe src="https://acme.jobs.personio.de/?display=en"></iframe>'})
    assert C.detect("acme.de/karriere", f)["slug"] == "acme"
    f = FakeFetcher({})
    assert C.detect("https://jobs.lever.co/acme", f) == {"ats": "lever", "slug": "acme", "note": None}
    assert f.fetched == []


def test_detect_follows_a_linked_jobs_page_on_the_same_site():
    f = FakeFetcher({
        "https://www.acme.de/karriere": '<a href="/about">About</a> <a href="https://karriere.acme.de/offene-stellen">Jobs</a>'
                                        ' <a href="https://other.com/jobs">x</a>',
        "https://karriere.acme.de/offene-stellen": '<a href="https://boards.greenhouse.io/acme/jobs/1">Werkstudent</a>',
    })
    result = C.detect("https://www.acme.de/karriere", f)
    assert (result["ats"], result["slug"]) == ("greenhouse", "acme")
    assert "https://other.com/jobs" not in f.fetched


def test_detect_unsupported_cases():
    f = FakeFetcher({"https://a.de/k": (404, "")})
    assert C.detect("https://a.de/k", f)["note"] == "HTTP 404"
    f = FakeFetcher({}, disallow={"https://a.de/k"})
    assert C.detect("https://a.de/k", f)["note"].startswith("robots.txt")
    f = FakeFetcher({"https://a.de/k": '<a href="https://acme.wd3.myworkdayjobs.com/en-US/ext">Jobs</a>'})
    assert C.detect("https://a.de/k", f) == {"ats": "unsupported", "slug": None, "note": "workday"}
    f = FakeFetcher({})
    assert C.detect("https://a.de/k", f)["note"] == "fetch failed: ConnectionError"


def test_detect_all_fills_only_new_entries(tmp_path):
    companies = [
        {"name": "Acme", "careers_url": "https://jobs.lever.co/acme", "ats": None, "slug": None, "enabled": False},
        {"name": "Done", "careers_url": "https://x.de", "ats": "unsupported", "slug": None, "enabled": False},
        {"name": "NoUrl", "careers_url": None, "ats": None, "slug": None, "enabled": False},
    ]
    C.detect_all(companies, fetcher=FakeFetcher({}), log=lambda *_: None)
    assert (companies[0]["ats"], companies[0]["slug"], companies[0]["enabled"]) == ("lever", "acme", True)
    assert companies[1]["ats"] == "unsupported" and companies[2]["ats"] is None

    path = tmp_path / "companies.yaml"
    C.save(companies, path)
    assert path.read_text().startswith("# Company career pages")
    assert [c["name"] for c in C.load(path)] == ["Acme", "Done", "NoUrl"]


def test_candidates_lists_unlisted_ba_employers(tmp_path):
    path = tmp_path / "jobs.csv"
    rows = [
        ("A GmbH", "Berlin", "ba", "2026-10-13"), ("A GmbH", "Berlin", "ba", "2026-10-12"),
        ("B AG", "München", "ba", "2026-10-13"), ("B AG", "München", "ba", "2026-10-13"),
        ("C SE", "Hamburg", "ba", "2026-10-13"), ("C SE", "Hamburg", "ba", "2026-10-13"),
        ("D", "Berlin", "arbeitnow", "2026-10-13"), ("D", "Berlin", "arbeitnow", "2026-10-13"),
        ("E", "Berlin", "ba", "2026-09-01"), ("E", "Berlin", "ba", "2026-09-01"),
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "city", "source", "last_seen"])
        w.writerows(rows)
    listed = [{"name": "B", "ba_names": ["B AG"]}]
    assert C.candidates(jobs_path=path, companies=listed) == [("A GmbH", {"Berlin": 2})]
