"""ATS adapters on saved public responses (trimmed, job texts removed): HelloFresh
(Greenhouse), FINN (Lever), Enpal (Ashby), 42watt (Personio)."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from radar import ats

FIXTURES = Path(__file__).parent / "fixtures" / "ats"


def fake_get(files):
    """A `get(url, kind)` that serves fixture files by URL substring; 404 for anything else."""
    calls = []

    def get(url, kind="json"):
        calls.append(url)
        for part, name in files.items():
            if part in url:
                text = (FIXTURES / name).read_text(encoding="utf-8")
                return json.loads(text) if kind == "json" else ET.fromstring(text.encode("utf-8"))
        raise ats.BoardNotFound(f"HTTP 404 from {url}")

    get.calls = calls
    return get


def company(name, ats_name, slug, cities=("Berlin", "München")):
    return {"name": name, "ats": ats_name, "slug": slug, "cities": list(cities), "enabled": True}


def rows(c, files):
    raws = ats.ADAPTERS[c["ats"]].fetch(c["slug"], fake_get(files))
    return raws, [r for raw in raws for r in ats.normalise(c, c["ats"], raw)]


def test_greenhouse():
    raws, out = rows(company("HelloFresh", "greenhouse", "hellofresh"), {"boards-api.greenhouse.io/v1/boards/hellofresh/jobs": "greenhouse.json"})
    assert len(raws) == 4
    assert out == [{
        "company": "HelloFresh", "title": "Culinary Operations Working Student (all genders)", "remote": False,
        "url": "https://careers.hellofresh.com/global/en/job/8210829?gh_jid=8210829",
        "posted_at": "2026-09-23", "source_ats": "greenhouse", "city": "Berlin",
    }]


def test_lever_falls_back_to_the_eu_host():
    get = fake_get({"api.eu.lever.co/v0/postings/finn": "lever.json"})
    raws = ats.lever.fetch("finn", get)
    assert get.calls[0].startswith("https://api.lever.co/") and get.calls[1].startswith("https://api.eu.lever.co/")
    assert [r["remote"] for r in raws] == [False, True, False]
    out = [r for raw in raws for r in ats.normalise(company("FINN", "lever", "finn"), "lever", raw)]
    assert [(r["title"], r["city"], r["posted_at"]) for r in out] == [
        ("Working Student - B2B Sales (m/w/d)", "München", "2026-08-27")]


def test_ashby():
    raws, out = rows(company("Enpal", "ashby", "enpal"), {"api.ashbyhq.com/posting-api/job-board/enpal": "ashby.json"})
    assert len(raws) == 4
    assert [(r["title"], r["city"], r["remote"]) for r in out] == [
        ("Werkstudent:in Kundenbetreuer:in (w/m/d)", "Berlin", True),
        ("Werkstudent:in Booking Manager:in (w/m/d)", "Berlin", False),
        ("Werkstudent:in Customer Success Manager:in (w/m/d)", "Berlin", True),   # secondary location "Remote"
    ]
    assert out[1]["url"].startswith("https://jobs.ashbyhq.com/enpal/")


def test_personio_falls_back_to_com_and_keeps_offices():
    get = fake_get({"42watt.jobs.personio.com/xml": "personio.xml"})
    raws = ats.personio.fetch("42watt", get)
    assert [u.split("/xml")[0] for u in get.calls] == ["https://42watt.jobs.personio.de", "https://42watt.jobs.personio.com"]
    assert raws[0]["url"] == "https://42watt.jobs.personio.com/job/2647966"
    assert "Berlin (remote)" in raws[0]["locations"]
    out = [r for raw in raws for r in ats.normalise(company("42watt", "personio", "42watt", ["München"]), "personio", raw)]
    # Only the company's cities are kept; the job open in Berlin too stays a München job.
    assert {r["city"] for r in out} == {"München"}
    assert len(out) == 3 and out[0]["remote"] is True


def test_board_not_found_is_raised_after_all_hosts():
    with pytest.raises(ats.BoardNotFound):
        ats.lever.fetch("nobody", fake_get({}))
    with pytest.raises(ats.BoardNotFound):
        ats.personio.fetch("nobody", fake_get({}))


@pytest.mark.parametrize("title, expected", [
    ("Werkstudent Marketing (m/w/d)", True),
    ("Werkstudentin Controlling", True),
    ("Werk-Student IT", True),
    ("Werkstudierende im Einkauf", True),
    ("Working Student - B2B Sales", True),
    ("Working-Student Finance", True),
    ("Junior Facilitair Medewerker (v/m/x)", False),
    ("Initiativbewerbung Werkstudent | Praktikant", False),
    ("Praktikum Marketing", False),
])
def test_is_werkstudent(title, expected):
    assert ats.is_werkstudent(title) is expected


def test_normalise_cities_and_remote():
    c = company("Acme", "greenhouse", "acme")
    raw = {"title": "Working Student Data", "url": "u", "posted_at": None, "country": None, "remote": False}
    assert [r["city"] for r in ats.normalise(c, "greenhouse", {**raw, "locations": ["Munich, Bavaria, Germany; Berlin"]})] == ["Berlin", "München"]
    assert ats.normalise(c, "greenhouse", {**raw, "locations": ["Hamburg, Germany"]}) == []
    remote = ats.normalise(c, "greenhouse", {**raw, "locations": ["Remote - Germany"]})
    assert [(r["city"], r["remote"]) for r in remote] == [("", True)]
    assert ats.normalise(c, "greenhouse", {**raw, "locations": ["Remote - Poland"]}) == []
    assert ats.normalise(company("B", "lever", "b", ["Berlin"]), "lever", {**raw, "locations": ["München"]}) == []


class Resp:
    def __init__(self, status):
        self.status_code = status


class FakeFetcher:
    def __init__(self, status=200, body="{}", allowed=True):
        self.status, self.body, self.ok = status, body, allowed

    def allowed(self, url):
        return self.ok

    def get(self, url, max_bytes=None):
        return Resp(self.status), self.body


def test_get_checks_robots_and_status():
    assert ats.get(FakeFetcher(body='{"jobs": []}'), "https://x/y") == {"jobs": []}
    with pytest.raises(ats.AtsError, match="robots.txt"):
        ats.get(FakeFetcher(allowed=False), "https://x/y")
    with pytest.raises(ats.BoardNotFound):
        ats.get(FakeFetcher(status=404), "https://x/y")
    with pytest.raises(ats.AtsError, match="HTTP 500"):
        ats.get(FakeFetcher(status=500), "https://x/y")
    with pytest.raises(ats.AtsError, match="unreadable"):
        ats.get(FakeFetcher(body="<html>"), "https://x/y")


def test_fetch_all_reports_status_per_company():
    companies = [company("A", "greenhouse", "a"), {**company("Off", "lever", "off"), "enabled": False},
                 company("B", "workday", "b")]
    fetcher = FakeFetcher(body=(FIXTURES / "greenhouse.json").read_text(encoding="utf-8"))
    jobs, status = ats.fetch_all(companies, fetcher, log=lambda *_: None)
    assert [j["company"] for j in jobs] == ["A"]
    assert status["A"] == {"ok": True, "board": 4, "jobs": 1, "error": None}
    assert "Off" not in status and status["B"]["ok"] is False
