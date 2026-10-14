"""LLM extraction without the API: section splitting, the request, answer checks, cost (radar/llm.py)."""

import json
import re

from radar import llm

POSTING = {"refnr": "r1", "title": "Werkstudent Data (m/w/d)", "text": """Über uns
Wir sind ein Softwarehaus in Köln mit 200 Leuten.

Deine Aufgaben:
- Auswertungen mit Excel und Power BI
- Pflege von Daten in SAP

Das bringst du mit
- Studium der Informatik
- Erste Erfahrung mit Python

Von Vorteil:
- SQL

Wir bieten
- 16 €/Stunde und flexible Zeiten
- Obstkorb
"""}


def test_sections_by_headings():
    s = llm.split_sections(POSTING["text"])
    assert set(s) == {"company", "tasks", "requirements", "nice_to_have", "benefits"}
    assert "Power BI" in s["tasks"] and "Python" in s["requirements"] and s["nice_to_have"] == "- SQL"
    assert "Softwarehaus" in s["company"] and "Obstkorb" in s["benefits"]


def test_headings_inside_one_paragraph_and_questions():
    one_line = "Wir sind toll. Deine Aufgaben: Daten pflegen in SAP. Das bringst du mit: Excel. Wir bieten: Obst."
    s = llm.split_sections(one_line)
    assert "SAP" in s["tasks"] and "Excel" in s["requirements"] and "Obst" in s["benefits"]
    s = llm.split_sections("Intro\nWas erwartet dich?\n- Reports\nWas bringst du mit?\n- Excel\n")
    assert s["tasks"] == "- Reports" and s["requirements"] == "- Excel"


def test_only_tasks_and_requirements_are_sent():
    msg, found = llm.model_input(POSTING)
    assert found
    assert msg.startswith("Title: Werkstudent Data (m/w/d)\n\n## Tasks\n")
    assert "## Requirements" in msg and "## Nice to have" in msg
    assert "Softwarehaus" not in msg and "Obstkorb" not in msg            # company and benefits stay out
    assert "16 €/Stunde" in msg                                           # a pay line from the benefits is kept
    assert llm.model_input({"refnr": "x", "title": "T", "text": "Nur ein Absatz ohne Überschriften."}) == (None, False)


def test_text_hash_changes_with_the_text():
    h = llm.text_hash(POSTING)
    assert h == llm.text_hash(dict(POSTING)) and len(h) == 16
    assert h != llm.text_hash({**POSTING, "text": POSTING["text"] + "."})


def test_request_parameters():
    p = llm.request_params("Title: x")
    assert p["model"] == "claude-haiku-5-5"
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert p["thinking"] == {"type": "adaptive"} and p["output_config"]["effort"] == "low"
    assert p["output_config"]["format"]["schema"] == llm.SCHEMA
    assert llm.request_params("x", cache_ttl="1h")["system"][0]["cache_control"]["ttl"] == "1h"
    off = llm.request_params("x", effort=None)
    assert off["thinking"] == {"type": "disabled"} and "effort" not in off["output_config"]
    assert "temperature" not in p                                         # not accepted by the model


def test_schema_is_strict():
    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)
    for obj in objects(llm.SCHEMA):
        assert obj["additionalProperties"] is False and set(obj["required"]) == set(obj["properties"])
    item = llm.SCHEMA["properties"]["skills"]["items"]["properties"]
    assert list(item)[:3] == ["term", "evidence", "id"]                   # term and quote are written before the id


def answer(**kw):
    base = {"skills": [], "german_level": "fluent", "german_requirement": "must_have", "english_level": "unknown",
            "english_requirement": "not_stated", "pay_hour_min": None, "pay_hour_max": None,
            "hours_week_min": None, "hours_week_max": None}
    return {**base, **kw}


def test_clean_checks_ids_and_ranges():
    raw = answer(skills=[
        {"term": "Excel", "id": "excel", "importance": "must_have"},
        {"term": "Excel-Kenntnisse", "id": "excel", "importance": "nice_to_have"},       # duplicate: once
        {"term": "SharePoint", "id": "sap", "importance": "must_have"},                  # wrong id -> other
        {"term": "ERP-System", "id": "ms_office", "importance": "must_have"},            # wrong id -> the one that fits
        {"term": "", "id": "sql", "importance": "must_have"},                            # no term: dropped
        {"term": "Jira", "id": "jira_software", "importance": "must_have"},              # made-up id
        {"term": "Kassensystem", "id": "other", "importance": "nice_to_have"},
    ], pay_hour_min=17.5, pay_hour_max=None, hours_week_min=30, hours_week_max=20, german_level="fluentish")
    out, problems = llm.clean(json.dumps(raw))
    assert [(s["id"], s["importance"]) for s in out["skills"]] == [
        ("excel", "must_have"), ("other", "must_have"), ("other_erp", "must_have"), ("jira", "must_have"),
        ("other", "nice_to_have")]
    assert (out["pay_hour_min"], out["pay_hour_max"]) == (17.5, 17.5)            # one rate: both bounds
    assert (out["hours_week_min"], out["hours_week_max"]) == (20, 30)            # swapped
    assert out["german_level"] == "unknown" and any("german_level" in p for p in problems)
    assert llm.clean(json.dumps(answer(pay_hour_min=2, hours_week_max=60)))[0]["pay_hour_min"] is None
    assert llm.clean("not json") == (None, ["not JSON"])
    assert llm.clean({"no": "skills"})[0] is None
    kept, _ = llm.clean(raw, check_ids=False)
    assert ("sap", "must_have") in [(s["id"], s["importance"]) for s in kept["skills"]]


def test_check_id():
    assert llm.check_id("ms_office", "M365") == "ms_office"
    assert llm.check_id("sap", "SAP SuccessFactors") == "sap"
    assert llm.check_id("controlling", "Erstellung von Reportings") == "controlling"
    assert llm.check_id("excel", "Auswertungen") == "other"
    assert llm.check_id("sql", "") is None


def test_german_categories_match_the_rules():
    assert {llm.german_label(answer(german_requirement=k)) for k in llm.NEED} == {"required", "plus", "none", "implicit"}


def test_cost():
    u = {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write_5m": 1_000_000,
         "cache_write_1h": 0, "cache_write": 1_000_000}
    assert round(llm.cost(u), 4) == round(0.10 + 0.50 + 0.01 + 0.125, 4)
    assert round(llm.cost(u, batch=True), 4) == round((0.10 + 0.50 + 0.01 + 0.125) / 2, 4)


def test_batch_custom_ids_are_valid_and_unique():
    refs = ["12951-aa9c386e-f630-4c77--S", "an:some/slug with spaces", "x" * 120, "x" * 121]
    ids = [llm.batch_custom_id(r) for r in refs]
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", i) for i in ids) and len(set(ids)) == len(ids)


def test_english_postings_without_german_count_as_open():
    not_stated = answer(german_requirement="not_stated")
    assert llm.german_label(not_stated, "en") == "none"          # as the rules: English text, German never asked
    assert llm.german_label(not_stated, "de") == "implicit"
    assert llm.german_label(answer(german_requirement="must_have"), "en") == "required"


def test_evidence_is_looked_up_in_the_text():
    text = "Das bringst du mit:\n- Sicherer Umgang mit MS-Office (Excel, PowerPoint)\n- Gute Deutschkenntnisse"
    assert llm.evidence_found("sicherer Umgang mit MS Office", text)            # case, hyphen and spaces ignored
    assert llm.evidence_found("Sicherer Umgang mit … Excel", text)               # a gap marked with …
    assert not llm.evidence_found("Wir stellen dir MS Office zur Verfügung", text)
    assert llm.evidence_found("Sicherer Umgang mit MS Office", "- Sicherer Umgang mit**&#160;MS Office**")   # HTML entity
    raw = answer(skills=[{"term": "MS Office", "evidence": "Umgang mit MS-Office", "id": "ms_office", "importance": "must_have"},
                         {"term": "SAP", "evidence": "SAP-Kenntnisse", "id": "sap", "importance": "must_have"}])
    out, problems = llm.clean(raw, text=text)
    assert [s["evidence_found"] for s in out["skills"]] == [True, False]
    assert problems == ["evidence not in the text for sap"]
    assert "evidence_found" not in llm.clean(raw)[0]["skills"][0]               # no text given: no check


def test_full_text_only_on_request():
    flat = {"refnr": "x", "title": "T", "text": "Nur ein Absatz ohne Überschriften, Excel erwünscht."}
    assert llm.model_input(flat) == (None, False)
    msg, found = llm.model_input(flat, full_text=True)
    assert found and "Excel erwünscht" in msg and msg.startswith("Title: T")
