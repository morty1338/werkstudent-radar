"""Skill statistics with the LLM's results (data/history/llm.csv): build.py uses them where
they exist and the rules otherwise, and adds must have / nice to have; the MCP server's
top_skills reports the split as optional fields."""

import csv
import importlib
import sys
from pathlib import Path

from radar import build
from radar.extract_llm import STORE_COLUMNS
from tests.test_build import job, write_csv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))


def llm_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STORE_COLUMNS, restval="")
        w.writeheader()
        w.writerows(rows)


def make_db(tmp_path):
    jobs = tmp_path / "jobs.csv"
    write_csv(jobs, [
        job("a", skills="excel|sql"),                 # LLM: python must, sql nice
        job("b", skills="excel"),                     # LLM: excel nice
        job("c", skills="excel|powerpoint"),          # no_sections: rules stay
        job("d", skills="sap"),                       # not read by the LLM
        job("gone", skills="excel", last_seen="2026-10-01"),
    ])
    store = tmp_path / "llm.csv"
    llm_csv(store, [
        {"refnr": "a", "text_hash": "h", "extracted": "2026-10-09", "status": "ok", "must": "python", "nice": "sql",
         "other_must": "Kassensystem", "german_req": "must_have"},
        {"refnr": "b", "text_hash": "h", "extracted": "2026-10-09", "status": "ok", "nice": "excel"},
        {"refnr": "c", "text_hash": "h", "extracted": "2026-10-09", "status": "no_sections"},
        {"refnr": "zzz", "text_hash": "h", "extracted": "2026-10-09", "status": "ok", "must": "java"},   # unknown posting
    ])
    return build.load_db(jobs, llm_csv=store)


def test_llm_skills_replace_the_rules_where_they_exist(tmp_path):
    db = make_db(tmp_path)
    skills = lambda ref: sorted(r[0] for r in db.execute("SELECT skill FROM job_skills WHERE refnr = ?", (ref,)))
    assert skills("a") == ["python", "sql"]                 # the rules' excel is gone
    assert skills("b") == ["excel"]
    assert skills("c") == ["excel", "powerpoint"]           # no_sections: the rules' skills
    assert skills("d") == ["sap"]
    assert db.execute("SELECT COUNT(*) FROM job_skills WHERE refnr = 'zzz'").fetchone()[0] == 0
    assert sorted(r[0] for r in db.execute("SELECT refnr FROM llm_jobs")) == ["a", "b"]


def test_summary_counts_must_and_nice_and_the_source(tmp_path):
    summary = build.build_summary(make_db(tmp_path))
    by_id = {s["id"]: s for s in summary["skills"]}
    assert (by_id["excel"]["jobs"], by_id["excel"]["must_have"], by_id["excel"]["nice_to_have"]) == (2, 0, 1)
    assert (by_id["sql"]["must_have"], by_id["sql"]["nice_to_have"]) == (0, 1)
    assert (by_id["python"]["must_have"], by_id["python"]["nice_to_have"]) == (1, 0)
    assert (by_id["sap"]["must_have"], by_id["sap"]["nice_to_have"]) == (0, 0)     # rules only: no split
    assert summary["skill_source"] == {"llm": 2, "rules": 2}


def test_checker_rows_gain_two_columns_at_the_end(tmp_path):
    db = make_db(tmp_path)
    summary = build.build_summary(db)
    build.MIN_CHECKER_SKILL_JOBS, keep = 1, build.MIN_CHECKER_SKILL_JOBS
    try:
        checker, postings = build.build_checker(db, summary)
    finally:
        build.MIN_CHECKER_SKILL_JOBS = keep
    assert checker["fields"][:8] == ["category", "city", "german", "pay", "skills", "role_first", "role_city_first", "company"]
    assert checker["fields"][8:] == ["nice", "llm"]
    names = [s["id"] for s in checker["skills"]]
    rows = {p[0]: r for p, r in zip(postings["rows"], checker["jobs"])}
    a = rows["a"]
    assert sorted(names[i] for i in a[4]) == ["python", "sql"] and [names[i] for i in a[8]] == ["sql"] and a[9] == 1
    assert rows["d"][8] == [] and rows["d"][9] == 0


def test_without_an_llm_file_nothing_changes(tmp_path):
    jobs = tmp_path / "jobs.csv"
    write_csv(jobs, [job("a", skills="excel|sql")])
    db = build.load_db(jobs)                                    # a test jobs.csv: no default llm.csv
    assert sorted(r[0] for r in db.execute("SELECT skill FROM job_skills")) == ["excel", "sql"]
    assert build.build_summary(db)["skill_source"] == {"llm": 0, "rules": 1}


def test_mcp_top_skills_reports_the_split(tmp_path):
    rd = importlib.import_module("radar_data")
    rt = importlib.import_module("radar_tools")
    skills = [{"id": "excel", "label": "Excel", "group": "Office & Tools", "jobs": 3},
              {"id": "sql", "label": "SQL", "group": "Data", "jobs": 1}]
    rows = [[0, 0, 3, None, [0, 1], 1, 1, 0, [1], 1],        # read by the LLM: Excel must, SQL nice
            [0, 0, 3, None, [0], 1, 1, 0, [0], 1],           # Excel nice
            [0, 0, 3, None, [0], 1, 1, 0, [], 0],            # rules
            [0, 0, 3, None, [0], 1, 1, 0]]                   # an older file's row
    d = rd.Dataset(as_of="2026-10-14", summary={}, checker={"german": ["none", "plus", "implicit", "required"]}, fields=["it"], field_labels={"it": "IT"},
                cities=["Berlin"], skills=skills, jobs=rows, postings=None)
    out = rt.top_skills(d)
    by = {s["skill"]: s for s in out["skills"]}
    assert (by["Excel"]["postings"], by["Excel"]["must_have"], by["Excel"]["nice_to_have"]) == (4, 1, 1)
    assert (by["SQL"]["must_have"], by["SQL"]["nice_to_have"]) == (0, 1)
    assert out["postings_read_by_llm"] == 2

