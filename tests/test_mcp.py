"""The MCP server in mcp/: its tools on a small hand-made copy of the data (expected
numbers worked out by hand), the data source, the server over MCP, and agreement
with the website (gap.js, summary.json)."""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import radar_data as rd  # noqa: E402
import radar_tools as rt  # noqa: E402

# --- A small copy of the data ------------------------------------------------------------------
# Fields: it, marketing, other. Cities: Berlin, München, Hamburg, Frankfurt (Oder).
# Skills: 0 Python, 1 SQL, 2 Excel, 3 Power BI, 4 SAP.
# German levels: 0 none, 1 plus, 2 implicit, 3 required.

BERLIN_IT_PAY = [14, 15, 15, 16, 16, 17, 18, 18, 19, 20, 22, 25]       # 6 employers, 2 each
BERLIN_IT_SKILLS = [[0, 1], [0, 1], [0, 1], [0, 3], [1, 3], [1, 3], [2], [0, 1, 3], [], [4], [0], [1]]


def rows():
    out = []   # [field, city, german, pay, skills, role_first, role_city_first, company]
    for i, (pay, skills) in enumerate(zip(BERLIN_IT_PAY, BERLIN_IT_SKILLS)):
        out.append([0, 0, 0 if i % 2 == 0 else 3, pay, skills, 1, 1, i // 2])
    out += [[1, 2, 3, 13, [2], 1, 1, 6], [1, 2, 3, 14, [2, 4], 1, 1, 7], [1, 2, 2, 15, [], 1, 1, 8]]   # Hamburg marketing
    out += [[2, 1, 3, None, [], 1, 1, 9], [2, 1, 1, None, [4], 1, 1, 9]]                              # München other
    out.append([0, 1, 3, 30, [0], 0, 1, 0])   # an IT role first seen in Berlin, again in München
    return out


@pytest.fixture
def data_dir(tmp_path):
    jobs = rows()
    checker = {
        "as_of": "2026-10-13",
        "fields": ["category", "city", "german", "pay", "skills", "role_first", "role_city_first", "company"],
        "skills": [{"id": "python", "label": "Python", "group": "Programming", "jobs": 7},
                   {"id": "sql", "label": "SQL", "group": "Data", "jobs": 7},
                   {"id": "excel", "label": "Excel", "group": "Office & Tools", "jobs": 50},
                   {"id": "powerbi", "label": "Power BI", "group": "Data", "jobs": 4},
                   {"id": "sap", "label": "SAP", "group": "Business software", "jobs": 10}],
        "categories": ["it", "marketing", "other"],
        "category_labels": {"it": "IT & Software", "marketing": "Marketing & Communication", "other": "Other"},
        "cities": [{"name": n, "jobs": 1} for n in ("Berlin", "München", "Hamburg", "Frankfurt (Oder)")],
        "german": ["none", "plus", "implicit", "required"],
        "min_pay_sample": 10, "max_employer_share": 0.35, "max_ci_rel_width": 0.25,
        "jobs": jobs,
    }
    cities = ["Berlin", "München", "Hamburg", "Frankfurt (Oder)"]
    postings = {
        "as_of": "2026-10-13",
        "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
        "fields": ["refnr", "title", "company", "city", "published", "url"],
        "rows": [[f"R{i}", f"Werkstudent {i}", f"Firma {j[7]}", cities[j[1]], f"2026-10-{i + 1:02d}", "" if i % 3 else f"https://example.org/{i}"]
                 for i, j in enumerate(jobs)],
    }
    summary = {
        "as_of": "2026-10-13",
        "totals": {"jobs": len(jobs), "published_7d": 9, "companies": 10, "cities": 3, "by_source": {"ba": 15, "arbeitnow": 3},
                   "median_pay": 17.0, "median_pay_lo": 16.0, "median_pay_hi": 18.0, "p25_pay": 15.0, "p75_pay": 19.0,
                   "with_pay": 15, "no_german_share": 0.39, "remote_share": 0.0},
        "cities": [{"city": "Berlin", "jobs": 12}, {"city": "Hamburg", "jobs": 3}, {"city": "München", "jobs": 3}],
        "categories": [{"key": "it", "label": "IT & Software", "jobs": 13}, {"key": "marketing", "label": "Marketing & Communication", "jobs": 3}],
        "skills": [{"label": "SQL", "share": 0.39}, {"label": "Python", "share": 0.39}],
    }
    for name, content in (("checker", checker), ("postings", postings), ("summary", summary)):
        (tmp_path / f"{name}.json").write_text(json.dumps(content), encoding="utf-8")
    return tmp_path


@pytest.fixture
def d(data_dir):
    return rd.load(rd.Source(data_dir=str(data_dir)))


# --- Tools -------------------------------------------------------------------------------------

def test_get_overview(d):
    o = rt.get_overview(d)
    assert (o["as_of"], o["postings"], o["pay"]["median_eur_per_hour"]) == ("2026-10-13", 18, 17.0)
    assert o["top_cities"][0] == {"city": "Berlin", "postings": 12}
    assert o["by_source"] == {"Bundesagentur": 15, "company career sites (Arbeitnow)": 3}
    assert len(json.dumps(o)) < 2000


def test_search_jobs_filters_and_sorts_newest_first(d):
    r = rt.search_jobs(d, city="Berlin", skill="python")
    assert r["filters"] == {"city": "Berlin", "skill": "Python"}
    assert r["matches"] == 6 and r["shown"] == 6
    assert [j["title"] for j in r["jobs"]] == ["Werkstudent 10", "Werkstudent 7", "Werkstudent 3", "Werkstudent 2", "Werkstudent 1", "Werkstudent 0"]
    first = r["jobs"][0]
    assert first == {"title": "Werkstudent 10", "company": "Firma 5", "city": "Berlin", "field": "IT & Software",
                     "pay_eur_per_hour": 22, "german": "none", "published": "2026-10-11",
                     "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/R10"}
    assert r["jobs"][2]["url"] == "https://example.org/3"   # the posting's own link when it has one


def test_search_jobs_no_german_and_limit(d):
    r = rt.search_jobs(d, field="it", no_german=True, limit=2)
    assert r["matches"] == 6 and r["shown"] == 2 and r["filters"] == {"field": "IT & Software", "no_german": True}
    assert {j["german"] for j in rt.search_jobs(d, no_german=True, limit=50)["jobs"]} == {"none", "plus"}
    assert rt.search_jobs(d, limit=500)["shown"] == 18   # capped at 50, here all 18


def test_pay_stats_reliable_median(d):
    r = rt.pay_stats(d, field="IT", city="Berlin")
    assert (r["postings"], r["rates"], r["employers"]) == (12, 12, 6)
    assert r["median_eur_per_hour"] == 17.5 and r["middle_half"] == [15.75, 19.25]
    lo, hi = rt.bootstrap_median_ci(sorted(BERLIN_IT_PAY))
    assert r["ci95"] == [round(lo, 2), round(hi, 2)] and r["reliable"] is True


def test_pay_stats_says_when_there_are_too_few_rates(d):
    r = rt.pay_stats(d, city="Hamburg")
    assert (r["rates"], r["reliable"]) == (3, False)
    assert r["note"].startswith("Only 3 stated rates") and "at least 10" in r["note"]
    assert "ci95" not in r
    assert rt.pay_stats(d, field="other")["note"] == "None of these postings states an hourly rate."
    assert rt.pay_stats(d, city="Frankfurt (Oder)")["note"] == "No postings match these filters today."


def test_pay_stats_counts_each_role_once(d):
    # The München IT posting repeats a Berlin role: out of the overall figures, in München's.
    assert rt.pay_stats(d, field="it")["rates"] == 12
    assert rt.pay_stats(d, field="it", city="München")["rates"] == 1


def test_top_skills(d):
    r = rt.top_skills(d, city="Berlin", limit=3)
    assert r["postings"] == 12
    assert r["skills"] == [
        {"skill": "SQL", "group": "Data", "postings": 7, "share": round(7 / 12, 4)},
        {"skill": "Python", "group": "Programming", "postings": 6, "share": 0.5},
        {"skill": "Power BI", "group": "Data", "postings": 4, "share": round(4 / 12, 4)},
    ]


def test_match_skills_counts_fits_and_plans_what_to_learn(d):
    r = rt.match_skills(d, ["sql"], city="Berlin")
    # 11 Berlin postings list skills; only [SQL] fits. 8 are one skill short.
    assert (r["postings_listing_skills"], r["fit"], r["one_skill_short"]) == (11, 1, 8)
    assert r["fit_share"] == round(1 / 11, 4)
    # Python completes 4 postings, then Power BI 4 more, then Excel beats SAP on demand (50 > 10).
    assert r["learn_next"] == [{"skill": "Python", "extra_postings": 4, "extra_total": 4},
                               {"skill": "Power BI", "extra_postings": 4, "extra_total": 8},
                               {"skill": "Excel", "extra_postings": 1, "extra_total": 9}]
    assert "unknown_skills" not in r
    assert [j["title"] for j in r["examples"]] == ["Werkstudent 11"]          # the one fitting posting
    assert len(rt.match_skills(d, ["sql", "python", "power bi"], examples=2)["examples"]) == 2
    assert rt.match_skills(d, ["sql"], examples=0)["examples"] == []


def test_match_skills_reports_unknown_skills_with_suggestions(d):
    r = rt.match_skills(d, ["Pyhton", "SQL", "Cobol"])
    assert r["skills"] == ["SQL"]
    assert r["unknown_skills"] == [{"name": "Pyhton", "did_you_mean": ["Python"]}, {"name": "Cobol", "did_you_mean": []}]


# --- Names and close matches -------------------------------------------------------------------

@pytest.mark.parametrize("name, city", [("berlin", "Berlin"), ("Munich", "München"), ("Muenchen", "München"),
                                        ("munchen", "München"), ("Frankfurt Oder", "Frankfurt (Oder)")])
def test_city_names(d, name, city):
    assert d.cities[rt.resolve_city(d, name)] == city


@pytest.mark.parametrize("name, field", [("it", "it"), ("IT & Software", "it"), ("Marketing", "marketing"), ("OTHER", "other")])
def test_field_names(d, name, field):
    assert d.fields[rt.resolve_field(d, name)] == field


@pytest.mark.parametrize("name, skill", [("Power BI", "powerbi"), ("power-bi", "powerbi"), ("POWERBI", "powerbi"), ("sql", "sql")])
def test_skill_names(d, name, skill):
    assert d.skills[rt.resolve_skill(d, name)]["id"] == skill


@pytest.mark.parametrize("call, message", [
    (lambda d: rt.resolve_city(d, "Berln"), "unknown city 'Berln'. Did you mean: Berlin?"),
    (lambda d: rt.resolve_city(d, "Atlantis"), "unknown city 'Atlantis'. No Werkstudent postings there today; most are in Berlin, München, Hamburg, Frankfurt (Oder)."),
    (lambda d: rt.resolve_field(d, "Marketng"), "unknown field 'Marketng'. Did you mean: Marketing & Communication?"),
    (lambda d: rt.resolve_skill(d, "Exel"), "unknown skill 'Exel'. Did you mean: Excel?"),
    (lambda d: rt.search_jobs(d, skill="Powr BI"), "unknown skill 'Powr BI'. Did you mean: Power BI?"),
])
def test_unknown_names_suggest_close_matches(d, call, message):
    with pytest.raises(rt.UnknownValue) as e:
        call(d)
    assert str(e.value) == message


def test_unknown_field_without_a_close_match_lists_all_fields(d):
    with pytest.raises(rt.UnknownValue, match=r"Fields are: it \(IT & Software\), marketing"):
        rt.resolve_field(d, "zzz")


# --- Data source -------------------------------------------------------------------------------

def test_postings_are_only_joined_for_the_same_day(data_dir):
    p = json.loads((data_dir / "postings.json").read_text())
    p["as_of"] = "2026-10-12"
    (data_dir / "postings.json").write_text(json.dumps(p))
    d = rd.load(rd.Source(data_dir=str(data_dir)))
    assert d.postings is None
    r = rt.search_jobs(d, city="Hamburg")
    assert r["matches"] == 3 and "title" not in r["jobs"][0] and "note" in r


def test_missing_files_raise_data_error(tmp_path):
    with pytest.raises(rd.DataError, match="not found"):
        rd.load(rd.Source(data_dir=str(tmp_path)))


def test_store_rereads_changed_files(data_dir):
    store = rd.Store(rd.Source(data_dir=str(data_dir)))
    assert store.data().as_of == "2026-10-13"
    assert store.data() is store.data()
    for name in ("checker", "summary", "postings"):
        f = data_dir / f"{name}.json"
        f.write_text(f.read_text().replace("2026-10-13", "2026-10-14"))
        stat = f.stat()
        os.utime(f, (stat.st_atime, stat.st_mtime + 5))
    assert store.data().as_of == "2026-10-14"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_live_source_caches_and_falls_back(data_dir):
    calls = []
    fail = {"on": False}

    def opener(req, timeout):
        calls.append(req.full_url)
        if fail["on"]:
            raise OSError("offline")
        return FakeResponse((data_dir / req.full_url.rsplit("/", 1)[1]).read_bytes())

    src = rd.Source(live=True, base_url="https://example.org/data/", ttl=600, opener=opener)
    assert rd.load(src).as_of == "2026-10-13"
    assert sorted(calls) == ["https://example.org/data/checker.json", "https://example.org/data/postings.json", "https://example.org/data/summary.json"]
    rd.load(src)
    assert len(calls) == 3                       # served from memory within the TTL
    src.ttl, fail["on"] = 0, True
    assert rd.load(src).as_of == "2026-10-13"    # offline after the TTL: the last copy
    with pytest.raises(rd.DataError, match="can't fetch"):
        rd.Source(live=True, base_url="https://example.org/data/", opener=opener).get("summary")


# --- The server over MCP -----------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(data_dir):
    pytest.importorskip("mcp.server")
    import radar_server
    radar_server.configure(["--data-dir", str(data_dir)])
    return radar_server


@pytest.mark.anyio
async def test_server_lists_the_tools_with_descriptions(server):
    from mcp import Client
    async with Client(server.mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {"get_overview", "search_jobs", "pay_stats", "top_skills", "match_skills", "kurs_radar"}
    assert all(len(t.description or "") > 80 for t in tools.values())
    assert tools["match_skills"].input_schema["required"] == ["skills"]
    assert set(tools["search_jobs"].input_schema["properties"]) == {"city", "field", "skill", "no_german", "limit"}


@pytest.mark.anyio
async def test_server_answers_and_explains_unknown_names(server):
    from mcp import Client
    async with Client(server.mcp) as c:
        ok = await c.call_tool("pay_stats", {"city": "Berlin", "field": "it"})
        bad = await c.call_tool("search_jobs", {"city": "Berln"})
    assert not ok.is_error and ok.structured_content["median_eur_per_hour"] == 17.5
    text = ok.content[0].text                       # what the model reads: compact JSON, same data
    assert "\n" not in text and ", " not in text and json.loads(text) == ok.structured_content
    assert bad.is_error and "Did you mean: Berlin?" in bad.content[0].text


# --- Agreement with the website ----------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gap_plan_matches_gap_js_on_the_real_data(tmp_path):
    d = rd.load(rd.Source())
    jobs = [j[rd.SKILLS] for j in d.jobs if j[rd.SKILLS]]
    demand = {i: s["jobs"] for i, s in enumerate(d.skills)}
    have_sets = [[], [rt.resolve_skill(d, "SQL")], [rt.resolve_skill(d, n) for n in ("Python", "SQL", "Excel")]]
    script = tmp_path / "gap.mjs"
    script.write_text(
        f"import {{ gapPlan }} from {json.dumps((ROOT / 'docs' / 'assets' / 'gap.js').as_uri())};\n"
        f"const jobs = {json.dumps(jobs)}, demand = {json.dumps(demand)};\n"
        f"console.log(JSON.stringify({json.dumps(have_sets)}.map((h) => gapPlan(jobs, new Set(h), {{ steps: 3, demand }}))));\n")
    js = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    assert js == [rt.gap_plan(jobs, set(h), steps=3, demand=demand) for h in have_sets]


def test_pay_figures_match_summary_json():
    d = rd.load(rd.Source())
    t = d.summary["totals"]
    overall = rt.pay_stats(d)
    assert (overall["rates"], overall["median_eur_per_hour"], overall["ci95"]) == (t["with_pay"], t["median_pay"], [t["median_pay_lo"], t["median_pay_hi"]])
    for c in d.summary["cities"][:3]:
        r = rt.pay_stats(d, city=c["city"])
        assert (r["rates"], r["median_eur_per_hour"]) == (c["with_pay"], c["median_pay"])
