"""Kurs-Radar step 2 (radar/kurs_extract.py): career-changer and AI detection, and the run
with a stand-in BA details API and Message Batches API (no network)."""

import csv
import json
import os

import pytest

from radar import kurs_extract as KE
from tests.test_extract_llm import FakeBatches, fake_api

DAY = "2026-10-14"
TEXT = ("Wir sind ein KI-Startup aus Berlin.\n\nDeine Aufgaben:\n- Recruiting\n\nDein Profil:\n"
        "- Sicherer Umgang mit MS Office und Excel\n- Quereinsteiger sind willkommen\n")


@pytest.mark.parametrize("text, kind", [
    ("Quereinsteiger sind herzlich willkommen.", "quereinstieg"),
    ("Auch für Berufseinsteiger und Quereinsteiger geeignet", "quereinstieg"),
    ("oder du möchtest einen Quereinstieg ins Recruiting starten", "quereinstieg"),
    ("Quer-Einsteiger (m/w/d) gesucht", "quereinstieg"),
    ("Du hast noch keine Erfahrung im Online-Marketing? Kein Problem.", "ohne_erfahrung"),
    ("Keine Vorerfahrung nötig", "ohne_erfahrung"),
    ("Berufserfahrung ist nicht zwingend erforderlich.", "ohne_erfahrung"),
    ("(keine Vorkenntnisse nötig)", "ohne_erfahrung"),
    ("Es ist keine spezifische Berufserfahrung als Sachbearbeiter erforderlich", "ohne_erfahrung"),
    ("Ob Ausbildung, Studium oder Quereinstieg: entscheidend sind Ihre Fähigkeiten", "quereinstieg"),
    ("Wir stellen auch ohne abgeschlossene Ausbildung ein.", "ohne_ausbildung"),
    ("Eine Ausbildung ist kein Muss.", "ohne_ausbildung"),
    ("Erfahrung im Assistenzbereich – gern auch branchenfremd", "branchenfremd"),
    ("Erste Erfahrungen, auch aus anderen Branchen", "branchenfremd"),
    ("Career changers welcome!", "english"),
    ("No previous experience required", "english"),
])
def test_phrases_that_welcome_career_changers(text, kind):
    assert KE.career_changer("Recruiter (m/w/d)", f"Dein Profil:\n{text}\n") == kind


@pytest.mark.parametrize("text", [
    "Kein Quereinstieg — Berufserfahrung ist erforderlich",
    "Keine Quereinsteiger.",
    "Ein Quereinstieg ist leider nicht möglich.",
    "Quereinsteiger sind leider ausgeschlossen.",
    "Du erklärst SEO-Themen auch fachfremden Ansprechpartnern verständlich.",   # the audience, not the applicant
    "Mehrjährige Berufserfahrung im Recruiting",
    "Abgeschlossene kaufmännische Ausbildung",
])
def test_phrases_that_dont_count(text):
    assert KE.career_changer("Recruiter (m/w/d)", text) is None


def test_the_title_counts_too():
    assert KE.career_changer("Sales Development Representative (m/w/d) - Quereinstieg offen", "") == "quereinstieg"


@pytest.mark.parametrize("text, expected", [
    (TEXT, False),                                                          # "KI-Startup" in the intro only
    ("Dein Profil:\n- Erfahrung mit KI-Tools wie ChatGPT\n", True),
    ("Deine Aufgaben:\n- Prompts für unsere Chatbots schreiben\n", True),
    ("Dein Profil:\n- Spaß an künstlicher Intelligenz\n", True),
    ("Dein Profil:\n- Erfahrung in der Kita-Verwaltung\n", False),
    ("Dein Profil:\n- Grundkenntnisse in Gaia\n", False),                  # "ai" inside a word
])
def test_ai_only_in_tasks_and_requirements(text, expected):
    assert KE.ai_in_requirements("Sachbearbeiter (m/w/d)", text) is expected


def test_ai_in_the_title():
    assert KE.ai_in_requirements("KI-Manager (m/w/d)", "Über uns: ein Familienunternehmen.") is True


def test_features_row_keeps_facts_not_text():
    row = KE.features_row({"refnr": "a", "title": "Recruiter (m/w/d)", "text": TEXT}, DAY)
    assert row["qe_text"] == 1 and row["qe_kind"] == "quereinstieg" and row["ai_text"] == 0
    assert {"ms_office", "excel"} <= set(row["rules_skills"].split("|"))
    assert row["version"] == KE.DETECT_VERSION
    assert "Quereinsteiger sind willkommen" not in json.dumps(row)


# --- The run -----------------------------------------------------------------------------------

POSTING_COLUMNS = ["refnr", "title", "job_title", "first_seen", "last_seen", "quereinstieg", "published"]


def write(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class FakeFetch:
    def __init__(self, texts):
        self.texts, self.calls = texts, []

    def __call__(self, refs, path, workers):
        self.calls.append(list(refs))
        return {r: ({"referenznummer": r, "stellenangebotsBeschreibung": self.texts[r]} if self.texts.get(r) else None)
                for r in refs}, 0


@pytest.fixture
def world(tmp_path, monkeypatch):
    kdir, raw = tmp_path / "kurs", tmp_path / "raw"
    kdir.mkdir()
    rows = [{"refnr": f"k{i}", "title": "Recruiter (m/w/d)", "job_title": "recruiter", "first_seen": f"2026-10-{10 + i:02d}",
             "last_seen": DAY} for i in range(4)]
    rows.append({"refnr": "old", "title": "Recruiter", "job_title": "recruiter", "first_seen": "2026-09-01",
                 "last_seen": "2026-10-01"})                                       # not online any more
    write(kdir / "postings.csv", POSTING_COLUMNS, rows)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for k in KE.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    texts = {f"k{i}": TEXT for i in range(4)}
    texts["k0"] = None                                                             # taken down
    return {"kdir": str(kdir), "raw": str(raw), "texts": texts}


def go(world, api=None, fetch=None, **kw):
    fetch = fetch or FakeFetch(world["texts"])
    return KE.run(kurs_dir=world["kdir"], raw_dir=world["raw"], fetch=fetch, api=api, sleep=lambda s: None, **kw), fetch


def test_first_run_fetches_checks_and_sends_without_waiting(world):
    api = fake_api(ends_after=2)                                                   # still running when first asked
    stats, fetch = go(world, api=api)
    assert sorted(fetch.calls[0]) == ["k0", "k1", "k2", "k3"]                       # not the old posting
    feats = {r["refnr"]: r for r in read(os.path.join(world["kdir"], "features.csv"))}
    assert feats["k1"]["qe_text"] == "1" and feats["k0"]["status"] == "no_text"
    # Default: submit and don't wait; the batch is collected by the next run.
    assert stats["sent"] == 3 and stats["done"] == 0 and stats["pending"] == 3
    pending = json.load(open(os.path.join(world["kdir"], "llm_pending.json")))
    assert sorted(v["refnr"] for v in pending["requests"].values()) == ["k1", "k2", "k3"]
    assert not os.path.exists(os.path.join(world["kdir"], "llm.csv")) or read(os.path.join(world["kdir"], "llm.csv")) == []


def test_next_run_collects_the_batch_and_sends_nothing_again(world):
    api = fake_api(ends_after=2)
    go(world, api=api)
    stats, fetch = go(world, api=api)
    # Texts are local now (raw dir), k0 is retried only after a few days.
    assert fetch.calls == []
    store = {r["refnr"]: r for r in read(os.path.join(world["kdir"], "llm.csv"))}
    assert sorted(store) == ["k1", "k2", "k3"] and store["k1"]["must"] == "excel|python"
    assert stats["done"] == 3 and stats["sent"] == 0 and stats["pending"] == 0
    assert not os.path.exists(os.path.join(world["kdir"], "llm_pending.json"))
    assert len(api.messages.batches.created) == 1
    # The answers with evidence stay local, next to the texts.
    assert os.path.exists(os.path.join(world["raw"], DAY, "kurs_llm.jsonl.gz"))
    stats, _ = go(world, api=api)
    assert stats["sent"] == 0 and stats["candidates"] == 0
    assert len(read(os.path.join(world["kdir"], "llm_runs.csv"))) == 3


def test_waiting_collects_in_the_same_run(world, monkeypatch):
    monkeypatch.setenv("KURS_LLM_WAIT_MINUTES", "1")
    stats, _ = go(world, api=fake_api(ends_after=2))
    assert stats["done"] == 3 and stats["pending"] == 0


def test_no_key_still_checks_the_texts(world, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    stats, _ = go(world, api=fake_api())
    assert stats["sent"] == 0
    assert len([r for r in read(os.path.join(world["kdir"], "features.csv")) if r["status"] == "ok"]) == 3


def test_api_error_never_raises(world):
    stats, _ = go(world, api=fake_api(fail=True))
    assert stats["sent"] == 0 and stats["pending"] == 0
    assert len(read(os.path.join(world["kdir"], "features.csv"))) == 4


def test_budget_and_fetch_limits(world, monkeypatch):
    monkeypatch.setenv("KURS_MAX_FETCH", "2")
    monkeypatch.setenv("KURS_LLM_MAX_POSTINGS", "1")
    stats, fetch = go(world, api=fake_api())
    assert fetch.calls == [["k3", "k2"]]                                           # newest first
    assert stats["sent"] == 1 and stats["over_budget"] == 1


def test_a_failed_fetch_waits_for_the_next_run(world):
    def broken(refs, path, workers):
        raise ConnectionError("BA down")
    stats, _ = go(world, api=fake_api(), fetch=broken)
    assert stats["fetched_texts"] == 0 and stats["sent"] == 0


def test_new_detector_version_checks_again(world, monkeypatch):
    go(world, api=None, fetch=FakeFetch(world["texts"]))
    monkeypatch.setattr(KE, "DETECT_VERSION", "2")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    go(world)
    assert {r["version"] for r in read(os.path.join(world["kdir"], "features.csv")) if r["status"] == "ok"} == {"2"}


def test_dry_run_writes_nothing(world):
    stats, fetch = go(world, dry_run=True)
    assert fetch.calls == [] and stats["active"] == 4
    assert sorted(os.listdir(world["kdir"])) == ["postings.csv"]


def test_main_never_fails(monkeypatch):
    monkeypatch.setattr(KE, "run", lambda dry_run=False: 1 / 0)
    assert KE.main([]) == 0


def test_fake_batches_are_shared():
    assert FakeBatches                                                             # the same stand-in as extract_llm's tests
