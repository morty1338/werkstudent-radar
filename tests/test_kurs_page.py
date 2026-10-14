"""Kurs-Radar on the site (docs/assets/kurs-rank.js) and in the MCP server (kurs_radar): the
opportunities ranking agrees between both, small samples get no shares, names resolve."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import radar_data as rd  # noqa: E402
import radar_tools as rt  # noqa: E402

KURS_JS = (ROOT / "docs" / "assets" / "kurs-rank.js").as_uri()
node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def metric(n, base):
    return {"n": n, "base": base, "share": round(n / base, 3) if base else None}


def title(tid, name, postings, sample, qe, new7, exact=True, wow=None, skills=()):
    return {"id": tid, "name": name, "postings": postings, "postings_exact": exact, "sample": sample,
            "with_text": sample, "with_skills": sample, "read_by_llm": sample,
            "new_7d": metric(new7, sample), "quereinsteiger": {**metric(qe, sample), "text": qe, "ba_flag": 0},
            "ai": metric(1, sample), "basics": metric(sample // 2, sample),
            "skills": [{"id": s, "n": 10, "share": 0.5, "must": 8, "nice": 2} for s in skills],
            "basics_skills": [{"id": "ms_office", "n": 5, "share": 0.25, "must": 5, "nice": 0}],
            "other_terms": [{"term": "Personio", "n": 3}], "wow": wow}


KURS = {
    "as_of": "2026-10-14", "note": {"de": "nur Stellen der Bundesagentur für Arbeit, Momentaufnahme", "en": "BA only"},
    "basics": ["ms_office", "excel", "powerpoint", "word", "outlook", "teams"],
    "skills": {"recruiting": {"label": "Recruiting / Sourcing", "group": "x"}, "ms_office": {"label": "MS Office", "group": "y"}},
    "academies": [
        {"id": "hr", "name": "HR", **title("hr", "HR", 1300, 120, 20, 30), "titles": [
            title("recruiter", "Recruiter", 1000, 100, 16, 30, exact=False, skills=["recruiting"]),
            title("hr_sb", "HR-Sachbearbeiter", 300, 50, 2, 10),
        ]},
        {"id": "ki", "name": "KI", **title("ki", "KI", 40, 40, 4, 8), "titles": [
            title("ki_manager", "KI-Manager / AI Specialist", 38, 38, 4, 8),
            title("prompt_engineer", "Prompt Engineer", 2, 2, 0, 1),
        ]},
    ],
}


def run_node(tmp_path, expr):
    script = tmp_path / "k.mjs"
    script.write_text('globalThis.location = { search: "" };\n'
                      f"const m = await import({json.dumps(KURS_JS)});\n"
                      f"console.log(JSON.stringify({expr}));\n")
    return json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)


@node
def test_page_helpers(tmp_path):
    out = run_node(tmp_path, "[m.shareOf({share: 0.5, base: 19}), m.shareOf({share: 0.5, base: 20}), "
                             "m.wowChange({now: 110, before: 100}), m.wowChange(null), "
                             "m.mustFraction({must: 4, nice: 1}), m.mustFraction({must: 3, nice: 1})]")
    assert out[:2] == [None, 0.5] and out[2] == pytest.approx(0.1) and out[3] is None
    assert out[4] == 0.8 and out[5] is None                       # 4 postings read: too few for a split


@node
def test_opportunities_score_is_the_mean_of_three_scaled_parts(tmp_path):
    out = run_node(tmp_path, f"m.opportunities({json.dumps(KURS)})")
    assert [r["id"] for r in out["rows"]] == ["recruiter", "ki_manager", "hr_sb"]
    assert [x["id"] for x in out["skipped"]] == ["prompt_engineer"]
    rec, ki, hr = out["rows"]
    assert rec["parts"] == {"volume": 100, "momentum": 100, "openness": 100} and rec["score"] == 100
    # 38 postings is the smallest volume (log scale: 1000 vs 300 vs 38 -> 1, 0.63, 0)
    assert ki["parts"] == {"volume": 0, "momentum": 11, "openness": 54} and ki["score"] == 22
    assert hr["parts"] == {"volume": 63, "momentum": 0, "openness": 0} and hr["score"] == 21


@node
def test_week_over_week_doesnt_change_the_score(tmp_path):
    """Momentum is always the share of postings new in the last 7 days."""
    k = json.loads(json.dumps(KURS))
    for a in k["academies"]:
        for x in a["titles"]:
            x["wow"] = {"now": x["postings"], "before": 1, "before_date": "2026-10-07"}
    a = run_node(tmp_path, f"m.opportunities({json.dumps(KURS)}).rows.map((r) => r.score)")
    b = run_node(tmp_path, f"m.opportunities({json.dumps(k)}).rows.map((r) => r.score)")
    assert a == b


@node
@pytest.mark.parametrize("kurs", [KURS, None], ids=["fixture", "real data"])
def test_mcp_ranking_matches_the_page(tmp_path, kurs):
    k = kurs or json.loads((ROOT / "docs" / "data" / "kurs.json").read_text(encoding="utf-8"))
    js = run_node(tmp_path, f"m.opportunities({json.dumps(k)}).rows.map((r) => [r.name, r.score])")
    py = rt.kurs_opportunities(k)
    assert [[r["job_title"], r["score"]] for r in py["ranking"]] == js
    parts = run_node(tmp_path, f"m.opportunities({json.dumps(k)}).rows.map((r) => r.parts)")
    assert [r["parts"] for r in py["ranking"]] == parts


def test_mcp_overview_academy_and_title():
    o = rt.kurs_radar(KURS)
    assert o["note"] == "BA only" and [a["academy"] for a in o["academies"]] == ["HR", "KI"]
    assert o["opportunities"]["not_rated"] == ["Prompt Engineer"]
    hr = rt.kurs_radar(KURS, academy="hr")
    assert [t["job_title"] for t in hr["job_titles"]] == ["Recruiter", "HR-Sachbearbeiter"]
    r = rt.kurs_radar(KURS, job_title="recruiter")
    assert (r["academy"], r["postings"], r["postings_is_lower_bound"], r["quereinsteiger_share"]) == ("HR", 1000, True, 0.16)
    assert r["top_skills"][0] == {"skill": "Recruiting / Sourcing", "share": 0.5, "postings": 10, "must_have": 8, "nice_to_have": 2}
    assert r["basics"] == [{"skill": "MS Office", "share": 0.25}]


def test_mcp_small_titles_get_no_shares():
    r = rt.kurs_radar(KURS, job_title="Prompt Engineer")
    assert r["too_few_postings"] is True and r["quereinsteiger_share"] is None and r["ai_share"] is None


@pytest.mark.parametrize("name, expected", [("KI-Manager", "KI-Manager / AI Specialist"), ("ai specialist", "KI-Manager / AI Specialist"),
                                            ("HR Sachbearbeiter", "HR-Sachbearbeiter"), ("RECRUITER", "Recruiter")])
def test_mcp_title_names(name, expected):
    assert rt.kurs_radar(KURS, job_title=name)["job_title"] == expected


def test_mcp_unknown_names_suggest():
    with pytest.raises(rt.UnknownValue, match="Did you mean: Recruiter"):
        rt.kurs_radar(KURS, job_title="Recrutier")
    with pytest.raises(rt.UnknownValue, match="Academys are: HR, KI|Did you mean"):
        rt.kurs_radar(KURS, academy="Finance")


def test_mcp_reads_kurs_json_from_the_source(tmp_path):
    (tmp_path / "kurs.json").write_text(json.dumps(KURS), encoding="utf-8")
    assert rt.kurs_radar(rd.Source(data_dir=str(tmp_path)).get("kurs"))["as_of"] == "2026-10-14"


@pytest.mark.anyio
async def test_server_tool(tmp_path):
    pytest.importorskip("mcp.server")
    from mcp import Client
    import radar_server
    (tmp_path / "kurs.json").write_text(json.dumps(KURS), encoding="utf-8")
    radar_server.configure(["--data-dir", str(tmp_path)])
    async with Client(radar_server.mcp) as c:
        ok = await c.call_tool("kurs_radar", {"job_title": "Recruiter"})
        bad = await c.call_tool("kurs_radar", {"academy": "Finanzen"})
    assert not ok.is_error and ok.structured_content["job_title"] == "Recruiter"
    assert bad.is_error and "academy" in bad.content[0].text


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_mcp_academy_shows_the_per_title_values():
    k = json.loads(json.dumps(KURS))
    hr = k["academies"][0]
    hr["weighting"] = {"method": "equal per job title", "min_postings": 20, "titles": ["recruiter", "hr_sb"]}
    for key in ("quereinsteiger", "ai", "basics", "new_7d"):
        hr[key]["by_title"] = [{"id": "recruiter", "name": "Recruiter", "share": 0.16, "base": 100, "weighted": True},
                               {"id": "hr_sb", "name": "HR-Sachbearbeiter", "share": 0.04, "base": 50, "weighted": True}]
    out = rt.kurs_radar(k, academy="HR")
    assert out["weighting"]["method"] == "equal per job title"
    assert out["by_job_title"]["quereinsteiger"][0] == {"job_title": "Recruiter", "share": 0.16, "postings_read": 100, "in_average": True}


@node
def test_equal_shares_weigh_each_job_title_the_same(tmp_path):
    """Jobs mode, one academy: 40 postings of title A (all ask for skill 0) and 20 of title B
    (none do) give 50%, not 40/60; a title under 20 postings is left out; one big title: null."""
    a = [[0]] * 40
    b = [[1]] * 20
    small = [[0, 1]] * 5
    out = run_node(tmp_path, f"[m.equalShares({json.dumps([a, b, small])}, 3), m.equalShares({json.dumps([a, small])}, 3)]")
    assert out[0] == [0.5, 0.5, 0] and out[1] is None

