"""The daily LLM step (radar/extract_llm.py) with a stand-in for the Message Batches API:
only new or changed postings are sent, unfinished batches are picked up later, budget
limits hold, and nothing can fail the pipeline."""

import csv
import json
import types

import pytest

from radar import extract_llm as X
from radar import llm

DAY = "2026-10-14"
TEXT = "Deine Aufgaben:\n- Auswertungen in Excel\n\nDas bringst du mit:\n- Python\n- Deutsch fließend\n"


def posting(ref, text=TEXT):
    return {"refnr": ref, "title": f"Werkstudent {ref}", "text": text}


def answer_for(p):
    return {"skills": [{"term": "Excel", "id": "excel", "importance": "must_have"},
                       {"term": "Python", "id": "python", "importance": "must_have"},
                       {"term": "Kassensystem", "id": "other", "importance": "nice_to_have"}],
            "german_level": "fluent", "german_requirement": "must_have", "english_level": "unknown",
            "english_requirement": "not_stated", "pay_hour_min": None, "pay_hour_max": None,
            "hours_week_min": None, "hours_week_max": 20}


class FakeBatches:
    """Batches end after `ends_after` retrieve calls; `fail` makes create raise."""

    def __init__(self, ends_after=1, fail=False):
        self.ends_after, self.fail = ends_after, fail
        self.created, self.retrieved, self.stored = [], 0, {}

    def create(self, requests):
        if self.fail:
            raise ConnectionError("api down")
        bid = f"msgbatch_{len(self.created)}"
        self.created.append(requests)
        self.stored[bid] = requests
        return types.SimpleNamespace(id=bid)

    def retrieve(self, bid):
        self.retrieved += 1
        return types.SimpleNamespace(id=bid, processing_status="ended" if self.retrieved >= self.ends_after else "in_progress")

    def results(self, bid):
        for r in self.stored[bid]:
            msg = r["params"]["messages"][0]["content"]
            ans = answer_for(msg)
            message = types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text=json.dumps(ans))], stop_reason="end_turn",
                usage={"input_tokens": 600, "output_tokens": 300, "cache_read_input_tokens": 4000,
                       "cache_creation_input_tokens": 0})
            yield types.SimpleNamespace(custom_id=r["custom_id"], result=types.SimpleNamespace(type="succeeded", message=message))


def fake_api(**kw):
    return types.SimpleNamespace(messages=types.SimpleNamespace(batches=FakeBatches(**kw)))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Paths in tmp, today's postings with texts, rules' skills in jobs.csv."""
    jobs = {f"r{i}": {"refnr": f"r{i}", "title": f"Werkstudent r{i}", "last_seen": DAY, "first_seen": f"2026-10-{10 + i % 4:02d}",
                      "skills": "excel|sql", "german": "required"} for i in range(5)}
    texts = {ref: posting(ref) for ref in jobs}
    texts["r4"] = posting("r4", "Ein Absatz ohne Überschriften, nur über die Firma.")
    state = {"jobs": jobs, "texts": texts}
    monkeypatch.setattr(X, "STORE_CSV", str(tmp_path / "llm.csv"))
    monkeypatch.setattr(X, "RUNS_CSV", str(tmp_path / "llm_runs.csv"))
    monkeypatch.setattr(X, "PENDING_JSON", str(tmp_path / "llm_pending.json"))
    monkeypatch.setattr(X, "read_csv", lambda path: list(state["jobs"].values()) if path == X.JOBS_CSV else _read(path))
    monkeypatch.setattr(X, "todays_texts", lambda jobs, raw_dir: (DAY, jobs, state["texts"]))
    monkeypatch.setattr(X, "latest_raw_dir", lambda: str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_POLL_SECONDS", "1")
    monkeypatch.setenv("LLM_WAIT_MINUTES", "1")
    for k in ("LLM_MAX_POSTINGS", "LLM_MAX_USD", "LLM_MAX_FETCH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_FULL_TEXT", "0")                  # the tests below check section-less postings stay with the rules
    monkeypatch.setattr(X, "fetch_details", lambda refs, path, workers: pytest.fail("nothing should be fetched"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    state["tmp"] = tmp_path
    return state


def _read(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def stored():
    return {r["refnr"]: r for r in _read(X.STORE_CSV)}


def test_sends_new_postings_once(world):
    api = fake_api()
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert len(api.messages.batches.created) == 1 and len(api.messages.batches.created[0]) == 4
    assert (stats["sent"], stats["done"], stats["no_sections"], stats["failed"], stats["pending"]) == (4, 4, 1, 0, 0)
    rows = stored()
    assert rows["r0"]["status"] == "ok" and rows["r0"]["must"] == "excel|python" and rows["r0"]["other_nice"] == "Kassensystem"
    assert rows["r0"]["german_req"] == "must_have" and rows["r0"]["hours_max"] == "20" and rows["r0"]["pay_min"] == ""
    assert rows["r0"]["text_hash"] == llm.text_hash(world["texts"]["r0"])
    assert rows["r4"]["status"] == "no_sections" and rows["r4"]["must"] == ""
    assert "Ein Absatz" not in open(X.STORE_CSV).read()                     # never the job text

    again = fake_api()
    stats, _ = X.run(api=again, sleep=lambda s: None)                     # nothing changed: nothing sent
    assert again.messages.batches.created == [] and stats["candidates"] == 0

    world["texts"]["r1"] = posting("r1", TEXT + "- SQL\n")                # a changed text is sent again
    third = fake_api()
    X.run(api=third, sleep=lambda s: None)
    assert [r["custom_id"] for r in third.messages.batches.created[0]] == [llm.batch_custom_id("r1")]


def test_runs_log_cost_and_agreement(world):
    X.run(api=fake_api(), sleep=lambda s: None)
    run = _read(X.RUNS_CSV)[-1]
    assert run["date"] == DAY and run["done"] == "4" and int(run["output_tokens"]) == 1200
    assert float(run["cost_usd"]) == pytest.approx(llm.cost(
        {"input": 2400, "output": 1200, "cache_read": 16000, "cache_write_5m": 0, "cache_write_1h": 0, "cache_write": 0}, batch=True), abs=1e-4)
    # LLM excel|python vs. rules excel|sql: Dice 2*1/(2+2) = 0.5; German "must_have" = rules "required"
    assert (run["compared"], run["same_skills"], run["skill_agreement"], run["german_agreement"]) == ("4", "0.0", "0.5", "1.0")


def test_unfinished_batch_is_collected_by_the_next_run(world):
    api = fake_api(ends_after=99)
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert stats["done"] == 0 and stats["pending"] == 4
    pending = json.load(open(X.PENDING_JSON))
    assert pending["id"] == "msgbatch_0" and len(pending["requests"]) == 4
    assert stored()["r4"]["status"] == "no_sections" and "r0" not in stored()  # the rules cover r0–r3 for now

    api.messages.batches.ends_after = 0                                   # next day: the batch has ended
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert stats["done"] == 4 and len(api.messages.batches.created) == 1  # collected, not sent again
    assert stored()["r2"]["text_hash"] == llm.text_hash(world["texts"]["r2"])
    assert not (world["tmp"] / "llm_pending.json").exists()


def test_without_a_key_or_with_api_errors_nothing_fails(world, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    api = fake_api()
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert api.messages.batches.created == [] and stats["sent"] == 0
    assert set(stored()) == {"r4"}                                        # only the posting the rules handle anyway

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    stats, _ = X.run(api=fake_api(fail=True), sleep=lambda s: None)
    assert stats["done"] == 0 and "r0" not in stored()                    # tried again next run
    monkeypatch.setattr(X, "run", lambda **kw: 1 / 0)
    assert X.main([]) == 0                                                # even a crash is not a failed run


def test_budget_per_run(world, monkeypatch):
    monkeypatch.setenv("LLM_MAX_POSTINGS", "2")
    api = fake_api()
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert len(api.messages.batches.created[0]) == 2 and stats["over_budget"] == 2
    sent = {llm.batch_custom_id(r) for r in ("r3", "r2")}                 # newest first_seen first
    assert {r["custom_id"] for r in api.messages.batches.created[0]} == sent

    monkeypatch.setenv("LLM_MAX_POSTINGS", "1000")
    monkeypatch.setenv("LLM_MAX_USD", str(X.EST_USD_PER_POSTING * 1.5))  # the cost guard allows one posting
    api = fake_api()
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert len(api.messages.batches.created[0]) == 1 and stats["over_budget"] == 1


def test_dry_run_sends_nothing(world):
    api = fake_api()
    stats, rows = X.run(dry_run=True, api=api)
    assert api.messages.batches.created == [] and rows == [] and stats["candidates"] == 5
    assert not (world["tmp"] / "llm.csv").exists()


def test_summary_reminds_of_the_spend_limit(world, monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    X.run(api=fake_api(), sleep=lambda s: None)
    text = summary.read_text()
    assert "Skills by Claude Haiku" in text and "spend limit" in text and "$" in text


def test_results_load_into_the_history_database(world, tmp_path):
    from radar import history
    X.run(api=fake_api(), sleep=lambda s: None)
    db = history.connect()
    history.load_llm(db, history_dir=str(tmp_path))
    rows = {r["refnr"]: dict(r) for r in db.execute("SELECT * FROM llm_extractions")}
    assert rows["r0"]["status"] == "ok" and rows["r0"]["hours_max"] == 20.0 and rows["r4"]["status"] == "no_sections"
    skills = db.execute("SELECT skill, importance, term FROM llm_skills WHERE refnr = 'r0' ORDER BY skill").fetchall()
    assert [tuple(s) for s in skills] == [("excel", "must_have", None), ("other", "nice_to_have", "Kassensystem"),
                                         ("python", "must_have", None)]



def test_full_text_mode_sends_postings_without_sections(world, monkeypatch):
    X.run(api=fake_api(), sleep=lambda s: None)                           # LLM_FULL_TEXT=0: r4 stays with the rules
    assert stored()["r4"]["status"] == "no_sections"
    monkeypatch.setenv("LLM_FULL_TEXT", "1")
    api = fake_api()
    stats, _ = X.run(api=api, sleep=lambda s: None)                       # now the no_sections row is sent, whole
    assert [r["custom_id"] for r in api.messages.batches.created[0]] == [llm.batch_custom_id("r4")]
    assert "no task or requirement headings" in api.messages.batches.created[0][0]["params"]["messages"][0]["content"]
    assert stored()["r4"]["status"] == "ok" and stats["no_sections"] == 0


def test_texts_fetched_for_active_postings_without_a_result(world, monkeypatch):
    world["jobs"]["old"] = {"refnr": "old", "title": "Werkstudent old", "last_seen": DAY, "first_seen": "2026-10-11",
                            "skills": "excel", "german": "required", "source": "ba"}
    world["jobs"]["an"] = {"refnr": "an:x", "title": "x", "last_seen": DAY, "first_seen": "2026-10-11", "source": "arbeitnow"}
    asked = []

    def fetch(refs, path, workers):
        asked.append(list(refs))
        return {ref: {"stellenangebotsBeschreibung": TEXT} for ref in refs}, 0
    monkeypatch.setattr(X, "fetch_details", fetch)
    api = fake_api()
    stats, _ = X.run(api=api, sleep=lambda s: None)
    assert asked == [["old"]]                                             # only BA postings can be fetched
    assert stats["fetched_texts"] == 1 and stored()["old"]["status"] == "ok"
    asked.clear()
    X.run(api=fake_api(), sleep=lambda s: None)                           # it has a result now: not fetched again
    assert asked == []

    monkeypatch.setenv("LLM_MAX_FETCH", "0")
    world["jobs"]["older"] = {**world["jobs"]["old"], "refnr": "older"}
    X.run(api=fake_api(), sleep=lambda s: None)
    assert asked == [] and "older" not in stored()                        # the fetch budget holds
