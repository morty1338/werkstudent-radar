"""Telegram alerts: which postings are new since the last run, the webhook payload, its signature
and sending (radar/notify.py)."""

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from radar import build, notify
from tests.test_build import job, write_csv

TODAY = "2026-10-09"
JOB_KEYS = {"id", "title", "company", "city", "field", "field_label", "skills", "pay", "german",
            "no_german", "remote", "source", "url"}


def rows(*extra):
    # Each source's first collection day (2026-10-01) only finds postings that were there before.
    return [
        job("start", first_seen="2026-10-01", published="2026-09-01", last_seen=TODAY),
        job("an-start", source="arbeitnow", url="https://example.org/s", first_seen="2026-10-01",
            published="2026-09-01", last_seen=TODAY),
        *extra,
    ]


def new(tmp_path, *extra):
    path = tmp_path / "jobs.csv"
    write_csv(path, rows(*extra))
    return notify.new_postings(build.load_db(path))


def test_new_means_first_seen_on_the_latest_collection_day(tmp_path):
    as_of, jobs = new(tmp_path,
        job("today", first_seen=TODAY, published=TODAY),
        job("yesterday", first_seen="2026-10-08", published="2026-10-08"),             # sent by the last run
        job("relisted", first_seen=TODAY, published="2026-08-01"),                     # old posting listed again
        job("gone", first_seen=TODAY, published=TODAY, last_seen="2026-10-08"),        # no longer online
        job("an-today", source="arbeitnow", url="https://example.org/1", first_seen=TODAY, published=""),
    )
    assert as_of == TODAY
    assert sorted(j["id"] for j in jobs) == ["an-today", "today"]


def test_nothing_new(tmp_path):
    assert new(tmp_path) == (TODAY, [])


def test_job_facts(tmp_path):
    _, jobs = new(tmp_path,
        job("a", title="Werkstudent Data", company="Acme", city="Köln", category="data", first_seen=TODAY,
            published=TODAY, pay_min=16, pay_max=18, german="none", remote=1, skills="sql|python"),
        job("b", first_seen=TODAY, published=TODAY, german="required"),
        job("c", source="arbeitnow", url="https://example.org/c", first_seen=TODAY, published=TODAY, german="plus"),
    )
    a, b, c = sorted(jobs, key=lambda j: j["id"])
    assert all(set(j) == JOB_KEYS for j in jobs)
    assert a == {
        "id": "a", "title": "Werkstudent Data", "company": "Acme", "city": "Köln", "field": "data",
        "field_label": "Data & Analytics", "skills": ["Python", "SQL"], "pay": 17.0, "german": "none",
        "no_german": True, "remote": True, "source": "ba",
        "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/a",
    }
    assert (b["pay"], b["skills"], b["no_german"], b["remote"]) == (None, [], False, False)
    assert (c["source"], c["url"], c["no_german"]) == ("arbeitnow", "https://example.org/c", True)


def career(**kw):
    j = {"company": "Acme", "title": "Working Student Data", "city": "Berlin", "remote": False,
         "url": "https://jobs.example.org/1", "posted_at": "2026-10-08", "source_ats": "greenhouse",
         "field": "data", "first_seen": TODAY, "new": True, "also_on": []}
    return {**j, **kw}


def test_career_page_jobs_new_today_and_only_there():
    companies = {"as_of": TODAY, "field_labels": {"data": "Data & Analytics"}, "jobs": [
        career(),
        career(url="https://jobs.example.org/2", new=False),                 # seen before
        career(url="https://jobs.example.org/3", also_on=["ba"]),            # already in the BA list
        career(url="https://jobs.example.org/4", city="", remote=True, field="zzz"),
    ]}
    jobs = notify.new_career_jobs(companies, TODAY)
    assert [j["url"] for j in jobs] == ["https://jobs.example.org/1", "https://jobs.example.org/4"]
    assert all(set(j) == JOB_KEYS for j in jobs)
    assert jobs[0] == {
        "id": "c:https://jobs.example.org/1", "title": "Working Student Data", "company": "Acme", "city": "Berlin",
        "field": "data", "field_label": "Data & Analytics", "skills": [], "pay": None, "german": "",
        "no_german": False, "remote": False, "source": "career_page", "url": "https://jobs.example.org/1",
    }
    assert (jobs[1]["city"], jobs[1]["remote"], jobs[1]["field_label"]) == ("", True, "Other")
    assert notify.new_career_jobs({**companies, "as_of": "2026-10-08"}, TODAY) == []    # a stale file
    assert notify.new_career_jobs(None, TODAY) == []


def test_collect_reads_both_sources(tmp_path):
    path = tmp_path / "jobs.csv"
    write_csv(path, rows(job("a", first_seen=TODAY, published=TODAY)))
    cpath = tmp_path / "companies.json"
    cpath.write_text(json.dumps({"as_of": TODAY, "jobs": [career()]}))
    as_of, jobs = notify.collect(path, cpath)
    assert (as_of, [j["source"] for j in jobs]) == (TODAY, ["ba", "career_page"])
    assert notify.collect(path, tmp_path / "missing.json")[1][0]["id"] == "a"


def test_payload_shape_and_cap():
    jobs = [{"id": str(i)} for i in range(notify.MAX_JOBS + 7)]
    p = notify.payload(TODAY, jobs)
    assert set(p) == {"event", "version", "as_of", "test", "site", "total", "jobs"}
    assert (p["event"], p["version"], p["as_of"], p["test"]) == ("new_postings", 1, TODAY, False)
    assert p["site"] == "https://morty1338.github.io/werkstudent-radar/"
    assert (p["total"], len(p["jobs"])) == (notify.MAX_JOBS + 7, notify.MAX_JOBS)
    assert notify.payload(TODAY, [], test=True)["test"] is True


def test_payload_has_no_job_texts(tmp_path):
    _, jobs = new(tmp_path, job("a", first_seen=TODAY, published=TODAY, skills="sql"))
    body = notify.encode(notify.payload(TODAY, jobs)).decode()
    for key in ("description", "text", "beschreibung", "stellenbeschreibung"):
        assert f'"{key}"' not in body


def test_signature_covers_timestamp_and_body():
    body = notify.encode({"a": "ä"})
    h = notify.headers("s3cret", body, now=1760000000)
    assert h["X-Radar-Timestamp"] == "1760000000"
    expected = hmac.new(b"s3cret", b"1760000000." + body, hashlib.sha256).hexdigest()
    assert h["X-Radar-Signature"] == f"sha256={expected}"
    assert notify.sign("s3cret", "1760000001", body) != h["X-Radar-Signature"]       # another time
    assert notify.sign("other", "1760000000", body) != h["X-Radar-Signature"]        # another secret
    assert notify.sign("s3cret", "1760000000", body + b" ") != h["X-Radar-Signature"]  # changed body
    assert body == '{"a":"ä"}'.encode()                                               # compact UTF-8


def test_only_https_urls():
    assert notify.url_ok("https://n8n.example.org/webhook/abc")
    assert notify.url_ok("http://localhost:5678/webhook/abc")
    assert not notify.url_ok("http://n8n.example.org/webhook/abc")
    assert not notify.url_ok("n8n.example.org/webhook/abc")


class FakeSession:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, data, headers, timeout):
        self.calls.append((url, data, headers))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return type("R", (), {"status_code": a})()


def test_send_retries_server_errors_only():
    waits = []
    s = FakeSession(502, requests.ConnectionError("https://secret.example.org/webhook/x refused"), 200)
    assert notify.send("https://x", "k", {"a": 1}, session=s, sleep=waits.append) == (True, "HTTP 200")
    assert len(s.calls) == 3 and waits == [2, 4]
    assert s.calls[0][1] == b'{"a":1}' and s.calls[0][2]["X-Radar-Signature"].startswith("sha256=")

    s = FakeSession(403)
    assert notify.send("https://x", "k", {}, session=s, sleep=waits.append) == (False, "HTTP 403")
    assert len(s.calls) == 1                                  # rejected: no retry

    s = FakeSession(requests.Timeout("https://secret.example.org/webhook/x"), 503, 500)
    ok, message = notify.send("https://x", "k", {}, session=s, sleep=lambda _: None)
    assert (ok, message) == (False, "HTTP 500")

    s = FakeSession(*[requests.ConnectionError("https://secret.example.org/webhook/x")] * 3)
    ok, message = notify.send("https://x", "k", {}, session=s, sleep=lambda _: None)
    assert (ok, message) == (False, "no answer (ConnectionError)")   # the URL never shows in the log


@pytest.fixture
def today_data(tmp_path, monkeypatch):
    path = tmp_path / "jobs.csv"
    write_csv(path, rows(job("a", first_seen=TODAY, published=TODAY)))
    monkeypatch.setattr(notify, "collect", lambda: notify.new_postings(build.load_db(path)))


def test_main_skips_quietly_without_settings(today_data, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(notify, "send", lambda *a, **k: sent.append(a))
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "k")
    assert notify.main([]) == 0
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.org/webhook/x")
    monkeypatch.setenv("WEBHOOK_SECRET", " ")
    assert notify.main([]) == 0
    monkeypatch.setenv("WEBHOOK_SECRET", "k")
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://n8n.example.org/webhook/x")
    assert notify.main([]) == 0
    assert sent == []
    assert "n8n.example.org" not in capsys.readouterr().out


def test_main_never_fails_the_run(today_data, monkeypatch, capsys):
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.org/webhook/x")
    monkeypatch.setenv("WEBHOOK_SECRET", "k")
    monkeypatch.setattr(notify, "send", lambda *a, **k: (False, "HTTP 500"))
    assert notify.main([]) == 0
    out = capsys.readouterr().out
    assert "::warning::" in out and "n8n.example.org" not in out

    monkeypatch.setattr(notify, "send", lambda url, secret, data: (True, "HTTP 200"))
    assert notify.main([]) == 0
    assert "sent 1 new postings for 2026-10-09 (1 ba)" in capsys.readouterr().out


def test_dry_run_prints_the_payload(today_data, monkeypatch, capsys):
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("dry run sent something"))
    assert notify.main(["--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["id"] == "a"


def test_end_to_end_over_http():
    """A real POST to a local server that checks the signature the way n8n will."""
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            ts = self.headers["X-Radar-Timestamp"]
            mine = "sha256=" + hmac.new(b"k", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
            got["valid"] = hmac.compare_digest(mine, self.headers["X-Radar-Signature"])
            got["data"] = json.loads(body)
            self.send_response(200 if got["valid"] else 401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/webhook"
        data = notify.payload(TODAY, [{"id": "a", "title": "Werkstudent Ä"}])
        assert notify.send(url, "k", data) == (True, "HTTP 200")
        assert got["valid"] and got["data"] == data
        assert notify.send(url, "wrong", data, sleep=lambda _: None) == (False, "HTTP 401")
    finally:
        server.shutdown()


def test_test_mode_and_strict_exit_codes(today_data, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(notify, "send", lambda url, secret, data: sent.append(data) or (True, "HTTP 200"))
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    assert notify.main(["--strict"]) == 1                 # a manual run says when nothing was sent
    assert notify.main([]) == 0
    monkeypatch.setenv("N8N_WEBHOOK_URL", "https://n8n.example.org/webhook/x")
    monkeypatch.setenv("WEBHOOK_SECRET", "k")
    assert notify.main(["--test", "--strict"]) == 0
    assert sent[-1]["test"] is True and sent[-1]["jobs"][0]["id"] == "a"
    assert "sent test with 1 new postings" in capsys.readouterr().out
    monkeypatch.setattr(notify, "send", lambda *a, **k: (False, "HTTP 401"))
    assert notify.main(["--strict"]) == 1
    assert notify.main([]) == 0


def test_send_reports_why_n8n_skipped_a_call():
    class Answer:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            if self.data is None:
                raise ValueError("no JSON")
            return self.data

    class Session:
        def __init__(self, answer):
            self.answer = answer

        def post(self, *a, **k):
            return self.answer

    skipped = Session(Answer({"ok": False, "reason": "2026-10-13 was already sent"}))
    assert notify.send("https://x", "k", {}, session=skipped) == (True, "HTTP 200, n8n: 2026-10-13 was already sent")
    assert notify.send("https://x", "k", {}, session=Session(Answer({"ok": True}))) == (True, "HTTP 200")
    assert notify.send("https://x", "k", {}, session=Session(Answer(None))) == (True, "HTTP 200")
