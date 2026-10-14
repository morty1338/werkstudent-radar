"""Kurs-Radar metrics (radar/kurs_stats.py) on hand-made CSVs."""

import csv
import json

import pytest

from radar import kurs_stats as KS

YAML = """
academies:
  - id: hr
    name: HR
    titles:
      - {id: recruiter, name: Recruiter, queries: [Recruiter], match: [recruit]}
      - {id: hr_sb, name: HR-Sachbearbeiter, queries: [Personalsachbearbeiter], match: [personalsachbearb]}
  - id: ki
    name: KI
    titles:
      - {id: prompt_engineer, name: Prompt Engineer, queries: [Prompt Engineer], match: [prompt]}
"""
DAY = "2026-10-14"


def write(path, rows):
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def p(ref, title, published="2026-10-01", qe="", last_seen=DAY):
    return {"refnr": ref, "job_title": title, "published": published, "quereinstieg": qe, "last_seen": last_seen}


def feat(ref, qe=0, ai=0, rules="", status="ok"):
    return {"refnr": ref, "text_hash": f"h{ref}", "status": status, "qe_text": qe, "ai_text": ai, "rules_skills": rules}


def res(ref, must="", nice="", other_must="", other_nice=""):
    return {"refnr": ref, "text_hash": f"h{ref}", "status": "ok", "must": must, "nice": nice,
            "other_must": other_must, "other_nice": other_nice}


def count(day, title, academy, est, complete=1):
    return {"date": day, "academy": academy, "job_title": title, "postings": min(est, 500), "estimated": est,
            "complete": complete}


@pytest.fixture
def world(tmp_path):
    (tmp_path / "kurs.yaml").write_text(YAML, encoding="utf-8")
    kdir = tmp_path / "kurs"
    kdir.mkdir()
    write(kdir / "postings.csv", [
        p("r1", "recruiter", published="2026-10-10", qe="1"),     # BA flag, new this week
        p("r2", "recruiter", published="2026-10-07"),             # exactly 7 days: new
        p("r3", "recruiter", published="2026-10-06"),
        p("r4", "recruiter"),                                     # no text yet
        p("r5", "recruiter", last_seen="2026-10-13"),             # gone: not in the sample
        p("s1", "hr_sb"),
        p("e1", "prompt_engineer"),
    ])
    write(kdir / "features.csv", [
        feat("r1"), feat("r2", qe=1), feat("r3", rules="excel|recruiting", ai=1), feat("r5", qe=1),
        feat("s1", rules="payroll|word"), feat("e1"),
    ])
    write(kdir / "llm.csv", [
        res("r1", must="excel|recruiting", nice="genai"),
        res("r2", must="recruiting|ms_office", other_nice="Personio;Active Sourcing"),
        res("e1", must="python", other_must="KI-Tools"),
    ])
    write(kdir / "counts.csv", [
        count("2026-10-06", "recruiter", "hr", 800), count("2026-10-06", "hr_sb", "hr", 2000),
        count("2026-10-06", "prompt_engineer", "ki", 2),
        count("2026-10-13", "recruiter", "hr", 950),
        count(DAY, "recruiter", "hr", 1000, complete=0), count(DAY, "hr_sb", "hr", 2500, complete=0),
        count(DAY, "prompt_engineer", "ki", 1),
    ])
    return KS.build(kurs_dir=str(kdir), config_path=str(tmp_path / "kurs.yaml"))


def title(data, tid):
    return next(t for a in data["academies"] for t in a["titles"] if t["id"] == tid)


def test_sample_and_postings(world):
    r = title(world, "recruiter")
    assert world["as_of"] == DAY
    assert r["postings"] == 1000 and r["postings_exact"] is False
    assert (r["sample"], r["with_text"], r["with_skills"], r["read_by_llm"]) == (4, 3, 3, 2)
    assert title(world, "prompt_engineer")["postings_exact"] is True


def test_quereinsteiger_counts_the_text_or_the_ba_flag_among_postings_with_text(world):
    q = title(world, "recruiter")["quereinsteiger"]
    assert (q["n"], q["base"], q["text"], q["ba_flag"]) == (2, 3, 1, 1)
    assert q["share"] == pytest.approx(0.667, abs=0.001)


def test_ai_from_llm_skills_other_terms_or_rules_text(world):
    assert title(world, "recruiter")["ai"] == {"n": 2, "base": 3, "share": 0.667}   # r1 genai, r3 rules text
    assert title(world, "prompt_engineer")["ai"]["n"] == 1                         # "KI-Tools" as an other term


def test_basics_and_skills_split_must_nice(world):
    r = title(world, "recruiter")
    assert r["basics"]["n"] == 3                                                   # excel, ms_office, excel
    skills = {s["id"]: s for s in r["skills"]}
    assert "excel" not in skills and "ms_office" not in skills                     # basics have their own row
    assert skills["recruiting"] == {"id": "recruiting", "n": 3, "share": 1.0, "must": 2, "nice": 0}
    assert skills["genai"]["nice"] == 1
    assert [b["id"] for b in r["basics_skills"]] == ["ms_office", "excel"]
    assert {t["term"] for t in r["other_terms"]} == set()                          # each term only once: below 2


def test_new_in_7_days(world):
    assert title(world, "recruiter")["new_7d"] == {"n": 2, "base": 4, "share": 0.5}


def test_week_over_week_from_the_daily_counts(world):
    assert title(world, "recruiter")["wow"] == {"now": 1000, "before": 800, "before_date": "2026-10-06"}
    hr = next(a for a in world["academies"] if a["id"] == "hr")
    assert hr["wow"] == {"now": 3500, "before": 2800, "before_date": "2026-10-06"}
    assert hr["postings"] == 3500 and hr["sample"] == 5


def test_no_week_back_yet(tmp_path):
    (tmp_path / "kurs.yaml").write_text(YAML, encoding="utf-8")
    kdir = tmp_path / "kurs"
    kdir.mkdir()
    write(kdir / "postings.csv", [p("r1", "recruiter")])
    write(kdir / "counts.csv", [count("2026-10-12", "recruiter", "hr", 9), count(DAY, "recruiter", "hr", 10)])
    data = KS.build(kurs_dir=str(kdir), config_path=str(tmp_path / "kurs.yaml"))
    assert title(data, "recruiter")["wow"] is None
    assert title(data, "recruiter")["with_skills"] == 0 and title(data, "recruiter")["ai"]["share"] is None


def test_labels_and_note(world):
    assert world["skills"]["recruiting"]["label"]
    assert world["note"]["de"] == "nur Stellen der Bundesagentur für Arbeit, Momentaufnahme"
    assert world["basics"] == ["ms_office", "excel", "powerpoint", "word"]
    json.dumps(world)                                                              # serialisable


def test_report_prints_every_academy(world):
    out = KS.report(world)
    assert "## HR: ≥ 3,500 postings" in out and "Prompt Engineer" in out and "+25%" in out


def test_no_data_means_no_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(KS, "build", lambda: None)
    monkeypatch.setattr(KS, "OUT_JSON", str(tmp_path / "kurs.json"))
    assert KS.main([]) == 0
    assert not (tmp_path / "kurs.json").exists()
