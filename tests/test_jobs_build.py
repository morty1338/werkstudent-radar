"""Jobs mode data (radar/jobs_build.py): the Kurs-Radar postings in the Werkstudent data's
shape, with job titles as fields, academies, sample marks and salary per year."""

import csv
import json
from pathlib import Path

import pytest

from radar import jobs_build as JB

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DAY = "2026-10-14"
YAML = """
academies:
  - id: hr
    name: HR
    titles:
      - {id: recruiter, name: Recruiter, queries: [Recruiter], match: [recruit]}
      - {id: hr_sb, name: HR-Sachbearbeiter, queries: [Personalsachbearbeiter], match: [personalsachbearb]}
"""


def write(path, rows):
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def posting(ref, title, job_title, city="Berlin", minijob="0", company=None):
    return {"refnr": ref, "title": title, "company": company or f"Firma {ref}", "city": city, "region": "BERLIN",
            "lat": "52.52", "lon": "13.4", "academy": "hr", "job_title": job_title, "also": "", "query": "q",
            "quereinstieg": "", "fulltime": "1", "parttime": "0", "minijob": minijob, "homeoffice": "1",
            "published": "2026-10-10", "first_seen": "2026-10-13", "last_seen": DAY}


def feature(ref, pay=None, skills="recruiting|ms_office", german="required"):
    return {"refnr": ref, "text_hash": "h", "checked": DAY, "status": "ok", "version": "3", "qe_text": "0", "qe_kind": "",
            "ai_text": "0", "rules_skills": skills, "lang": "de", "german": german, "english": "0", "hours": "40",
            "majors": "", "pay_min": str(pay) if pay else "", "pay_max": str(pay) if pay else "", "pay_src": "ba" if pay else ""}


@pytest.fixture
def outputs(tmp_path):
    (tmp_path / "kurs.yaml").write_text(YAML, encoding="utf-8")
    kdir = tmp_path / "kurs"
    kdir.mkdir()
    pays = [40000, 42000, 44000, 45000, 46000, 47000, 48000, 50000, 52000, 55000, 58000, 60000]
    write(kdir / "postings.csv", [posting(f"r{i}", f"Recruiter {i}", "recruiter") for i in range(12)]
          + [posting("m1", "Recruiter Minijob", "recruiter", minijob="1"), posting("s1", "Personalsachbearbeiter", "hr_sb")])
    write(kdir / "features.csv", [feature(f"r{i}", pays[i]) for i in range(12)] + [feature("m1", 13000), feature("s1")])
    write(kdir / "counts.csv", [
        {"date": DAY, "academy": "hr", "job_title": "recruiter", "postings": 13, "matched": 13, "estimated": 1087, "complete": 0},
        {"date": DAY, "academy": "hr", "job_title": "hr_sb", "postings": 1, "matched": 1, "estimated": 1, "complete": 1}])
    return JB.build_all(kurs_dir=str(kdir), config_path=str(tmp_path / "kurs.yaml"),
                        history_csv=str(tmp_path / "history.csv"), llm_csv=str(tmp_path / "none.csv"))


def test_job_titles_are_the_fields_with_academy_and_sample_marks(outputs):
    s = outputs["summary.json"]
    cats = {c["key"]: c for c in s["categories"]}
    assert cats["recruiter"]["label"] == "Recruiter" and cats["recruiter"]["academy"] == "hr"
    assert (cats["recruiter"]["sampled"], cats["recruiter"]["estimated"]) == (True, 1087)
    assert (cats["hr_sb"]["sampled"], cats["hr_sb"]["estimated"]) == (False, 1)
    assert s["academies"] == [{"id": "hr", "name": "HR", "titles": ["recruiter", "hr_sb"], "jobs": 14, "estimated": 1088}]
    assert s["totals"]["sampled_titles"] == 1 and s["mode"] == "jobs"
    c = outputs["checker.json"]
    assert c["categories"] == ["recruiter", "hr_sb"] and c["category_academy"] == {"recruiter": "hr", "hr_sb": "hr"}
    assert c["sampled"] == ["recruiter"]


def test_salary_per_year_without_minijobs(outputs):
    s = outputs["summary.json"]
    assert s["pay_unit"] == "year"
    assert s["totals"]["with_pay"] == 12                                    # the minijob's 13,000 € is left out
    assert s["totals"]["median_pay"] == 47500.0
    assert [b["bin"] for b in s["pay_histogram"]] == [40000, 45000, 50000, 55000, 60000]
    assert "per year" in s["method"]["pay"]
    pays = [j[3] for j in outputs["checker.json"]["jobs"] if j[3] is not None]
    assert min(pays) == 40000 and len(pays) == 12


def test_history_is_kept_in_its_own_file(outputs, tmp_path):
    assert outputs["history.json"]["dates"] == [DAY]
    assert (tmp_path / "history.csv").exists()


@pytest.mark.parametrize("name", ["summary", "checker", "postings", "history", "lifetimes", "cooccurrence"])
def test_same_shape_as_the_werkstudent_files(name):
    """The page can render Jobs mode with its existing code: every Werkstudent key is there."""
    jobs_path = DATA / f"jobs_{name}.json"
    if not jobs_path.exists():
        pytest.skip("no Jobs mode data built yet")
    ws, jobs = (json.loads(p.read_text(encoding="utf-8")) for p in (DATA / f"{name}.json", jobs_path))
    assert set(ws) <= set(jobs)
    if "fields" in ws:
        assert ws["fields"] == jobs["fields"]
    if name == "summary":
        assert set(ws["totals"]) <= set(jobs["totals"])
        assert set(ws["categories"][0]) <= set(jobs["categories"][0])


@pytest.mark.parametrize("posting, hours, expected", [
    ({"minijob": "0", "parttime": "0", "fulltime": "1"}, "40", True),
    ({"minijob": "0", "parttime": "1", "fulltime": "1"}, "", True),     # full or part time: the salary is the full-time one
    ({"minijob": "1", "parttime": "0", "fulltime": "0"}, "", False),
    ({"minijob": "0", "parttime": "1", "fulltime": "0"}, "", False),    # part time only
    ({"minijob": "0", "parttime": "0", "fulltime": "1"}, "25", False),  # "1.690 € monatlich für 25 Stunden"
])
def test_only_full_time_salaries_count(posting, hours, expected):
    assert JB.full_time(posting, {"hours": hours}) is expected
