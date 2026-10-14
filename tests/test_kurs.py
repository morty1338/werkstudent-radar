import csv
import os

import pytest

from radar import api, kurs

REAL = kurs.load_config()

SMALL_YAML = """
settings:
  cap_per_title: 3
  max_pages_per_query: 5
  exclude: ['werkstudent', 'leiter(in)?\\b']
academies:
  - id: hr
    name: HR
    titles:
      - id: recruiter
        name: Recruiter
        queries: ["Recruiter", "Talent Acquisition"]
        match: ['recruit', 'talent acquisition']
      - id: hr_sb
        name: HR-Sachbearbeiter
        queries: ["Personalsachbearbeiter"]
        match: ['personalsachbearb']
  - id: ja
    name: Job Academy
    titles:
      - id: sb
        name: Sachbearbeitung
        queries: ["Sachbearbeiter"]
        match: ['sachbearb']
"""


@pytest.fixture
def small(tmp_path):
    path = tmp_path / "kurs.yaml"
    path.write_text(SMALL_YAML, encoding="utf-8")
    return str(path)


def rec(ref, title, **extra):
    return {"referenznummer": ref, "stellenangebotsTitel": title, "firma": "Acme GmbH",
            "stellenlokationen": [{"adresse": {"ort": "Berlin", "region": "BERLIN"}, "breite": 52.52, "laenge": 13.405}],
            "datumErsteVeroeffentlichung": "2026-10-10", **extra}


class FakeSearch:
    """Pages of 100 per query from a dict {query: [records]}; counts calls."""

    def __init__(self, results, fail_on=None):
        self.results, self.fail_on, self.calls = results, fail_on, []

    def __call__(self, query, page, angebotsart):
        self.calls.append((query, page))
        if query == self.fail_on:
            raise api.ApiError("HTTP 503")
        rows = self.results.get(query, [])
        return len(rows), rows[(page - 1) * 100:page * 100]


# --- Config and title patterns --------------------------------------------------------------------

def test_real_config_has_the_five_academies():
    assert [a["id"] for a in REAL["academies"]] == ["sales", "marketing", "hr", "ki", "job_academy"]
    assert len(REAL["titles"]) == 16
    assert all(t["queries"] and t["match"] for t in REAL["titles"])
    assert REAL["settings"]["angebotsart"] == 1


@pytest.mark.parametrize("title, expected", [
    ("Account Executive (m/w/d) SaaS", "saas_sales"),
    ("SaaS Sales Manager (m/w/d)", "saas_sales"),
    ("Sales Development Representative (SDR) (m/w/d)", "bdr"),
    ("Business Development Representative - Outbound (m/w/d)", "bdr"),
    ("Mitarbeiter Vertriebsinnendienst (m/w/d)", "vertrieb_innendienst"),
    ("Sachbearbeiter im Vertriebsinnendienst (m/w/d)", "vertrieb_innendienst"),
    ("Inside Sales Representative (m/w/d)", "vertrieb_innendienst"),
    ("Social-Media-Manager/in", "social_media"),
    ("Content Marketing Manager (m/w/d)", "content_marketing"),
    ("SEO/SEA Manager (m/w/d)", "seo"),
    ("SEA-Manager (m/w/d)", "seo"),
    ("CRM Manager Salesforce (m/w/d)", "crm"),
    ("Online-Marketing-Manager (m/w/d)", "online_marketing"),
    ("Performance Marketing Manager (m/w/d)", "online_marketing"),
    ("Personalreferent (m/w/d)", "personalreferent"),
    ("HR-Generalist (m/w/d)", "personalreferent"),
    ("HR Business Partner (m/w/d)", "personalreferent"),
    ("Recruiter (m/w/d)", "recruiter"),
    ("Talent Acquisition Specialist (m/w/d)", "recruiter"),
    ("Personalsachbearbeiter (m/w/d)", "hr_sachbearbeiter"),
    ("HR Sachbearbeiter:in (m/w/d)", "hr_sachbearbeiter"),
    ("Personalassistenz (m/w/d) in Teilzeit", "hr_sachbearbeiter"),
    ("Prompt Engineer (m/w/d)", "prompt_engineer"),
    ("Mitarbeiter Customer Support & KI-Projektassistenz (m/w/d)", "ki_projektassistenz"),
    ("KI-Manager (m/w/d) in Vollzeit", "ki_manager"),
    ("AI Transformation Specialist (m/w/d)", "ki_manager"),
    ("Bürokaufmann/-frau", "buero_assistenz"),
    ("Kaufmann/-frau für Büromanagement (m/w/d)", "buero_assistenz"),
    ("Kaufmännische Assistenz (m/w/d)", "buero_assistenz"),
    ("Assistenz der Geschäftsführung (m/w/d)", "buero_assistenz"),
    ("Assistenz (m/w/d) der Leitung", "buero_assistenz"),
    ("Bürokraft (m/w/d)", "buero_assistenz"),
    ("Sachbearbeiter Einkauf (m/w/d)", "sachbearbeitung"),
    ("Sachbearbeitung in der Ausländerbehörde (m/w/d)", "sachbearbeitung"),
])
def test_titles_go_to_the_right_job_title(title, expected):
    assert kurs.classify(title, REAL)[0] == expected


@pytest.mark.parametrize("title", [
    "Werkstudent (m/w/d) Recruiting",            # the main radar has these
    "Praktikum Online Marketing",
    "Trainee Online-Marketing-Manager/-in (m/w/d)",
    "Head of Content Management (m/w/d)",
    "Teamleiter Kundenservice Innendienst (m/w/d)",
    "Sales Manager (m/w/d) Online Marketing",    # sells ads: no sales title fits, not online marketing
    "Sales Manager Sea & Air Freight (m/w/d)",   # SEA is not search engine advertising here
    "Zahnmedizinische Fachangestellte, MVZ Zahnärzte und Ki",   # "Ki" cut off, not KI
    "AI Engineer (m/w/d)",                       # a developer job, not a course outcome
    "Customer Success Manager (B2B SaaS)",
])
def test_titles_that_count_nowhere(title):
    assert kurs.classify(title, REAL) == []


def test_first_matching_title_wins_and_the_rest_are_kept():
    assert kurs.classify("Personalsachbearbeiter (m/w/d)", REAL) == ["hr_sachbearbeiter", "sachbearbeitung"]
    assert kurs.classify("Personalreferent / Recruiter (m/w/d)", REAL)[:2] == ["personalreferent", "recruiter"]


@pytest.mark.parametrize("bad, message", [
    ("academies: [{id: hr, name: HR, titles: [{id: x, name: X, queries: [a], match: ['(']}]}]", "bad pattern"),
    ("academies: [{id: hr, name: HR, titles: [{id: x, name: X, queries: [a]}]}]", "needs a name"),
    ("academies: [{id: hr, name: HR, titles: [{id: X Y, name: X, queries: [a], match: [a]}]}]", "a-z0-9_"),
    ("academies: [{id: hr, name: HR, titles: [{id: x, name: X, queries: [a], match: [a]},"
     " {id: x, name: Y, queries: [b], match: [b]}]}]", "used twice"),
    ("academies: []", "no job titles"),
])
def test_broken_config_is_refused(tmp_path, bad, message):
    path = tmp_path / "bad.yaml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(kurs.KursError, match=message):
        kurs.load_config(str(path))


# --- Search: caps, dedupe, estimate --------------------------------------------------------------

def test_every_query_reads_page_one_and_stops_at_the_cap(small):
    config = kurs.load_config(small)
    search = FakeSearch({
        "Recruiter": [rec(f"R{i}", f"Recruiter {i}") for i in range(250)],
        "Talent Acquisition": [rec("T1", "Talent Acquisition Manager")],
        "Personalsachbearbeiter": [rec("P1", "Personalsachbearbeiter")],
        "Sachbearbeiter": [rec("P1", "Personalsachbearbeiter"), rec("S1", "Sachbearbeiter Einkauf")],
    })
    found, counts, queries = kurs.collect(config, search, sleep=lambda s: None)
    # Recruiter: page 1 already holds more than the cap of 3, so page 2 is never read;
    # Talent Acquisition still gets its first page (total + share for the estimate).
    assert ("Recruiter", 2) not in search.calls
    assert ("Talent Acquisition", 1) in search.calls
    c = counts["recruiter"]
    assert c["postings"] == 3 and c["matched"] == 101 and not c["complete"]
    assert c["estimated"] == 250          # largest query: 250 results, all fit the title
    assert c["ba_total"] == 251
    # Only the first 3 (most relevant) recruiter postings are kept.
    kept = [r for r, h in found.items() if h["titles"][:1] == ["recruiter"] and not h.get("dropped")]
    assert kept == ["R0", "R1", "R2"]
    # P1 comes from two queries, stored once, counts for HR-Sachbearbeiter, also fits Sachbearbeitung.
    assert found["P1"]["titles"] == ["hr_sb", "sb"] and found["P1"]["query"] == "Personalsachbearbeiter"
    assert counts["hr_sb"] == {**counts["hr_sb"], "postings": 1, "estimated": 1, "complete": 1}
    assert counts["sb"]["postings"] == 1      # only S1; P1 counts once, for hr_sb
    assert {q["query"]: q["hits"] for q in queries} == {
        "Recruiter": 100, "Talent Acquisition": 1, "Personalsachbearbeiter": 1, "Sachbearbeiter": 1}


def test_lower_bound_uses_the_share_of_results_that_fit(small):
    config = kurs.load_config(small)
    rows = [rec(f"S{i}", "Sachbearbeiter" if i % 2 else "Lagerhelfer") for i in range(1000)]
    _, counts, _ = kurs.collect(config, FakeSearch({"Sachbearbeiter": rows}), sleep=lambda s: None)
    c = counts["sb"]
    # Page 1: 50 of 100 fit -> 1000 * 0.5 = 500 on the board (not 1000).
    assert c["matched"] == 50 and c["estimated"] == 500 and c["postings"] == 3


def test_excluded_titles_are_stored_nowhere(small):
    config = kurs.load_config(small)
    search = FakeSearch({"Recruiter": [rec("W", "Werkstudent Recruiting"), rec("L", "Teamleiter Recruiting")]})
    found, counts, _ = kurs.collect(config, search, sleep=lambda s: None)
    assert found["W"]["titles"] == [] and found["L"]["titles"] == []
    assert counts["recruiter"]["postings"] == 0


def test_request_limit_stops_a_runaway_config(small, monkeypatch):
    monkeypatch.setattr(kurs, "MAX_REQUESTS", 2)
    config = kurs.load_config(small)
    with pytest.raises(kurs.KursError, match="requests"):
        kurs.collect(config, FakeSearch({}), sleep=lambda s: None)


# --- Storage -------------------------------------------------------------------------------------

def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def results(day_titles):
    return FakeSearch({"Recruiter": [rec(r, t, quereinstiegGeeignet=r == "R1", arbeitszeitVollzeit=True,
                                         arbeitszeitTeilzeitFlexibel=r == "R2")
                                     for r, t in day_titles]})


def test_run_writes_postings_and_counts_and_keeps_first_seen(small, tmp_path):
    kdir, raw = str(tmp_path / "kurs"), str(tmp_path / "raw")
    kurs.run("2026-10-14", config_path=small, kurs_dir=kdir, raw_dir=raw,
             search=results([("R1", "Recruiter"), ("R2", "Recruiter (m/w/d)")]), sleep=lambda s: None)
    kurs.run("2026-10-15", config_path=small, kurs_dir=kdir, raw_dir=raw,
             search=results([("R2", "Recruiter (m/w/d)"), ("R3", "Senior Recruiter")]), sleep=lambda s: None)
    rows = {r["refnr"]: r for r in read(os.path.join(kdir, "postings.csv"))}
    assert list(rows["R1"]) == kurs.POSTING_COLUMNS
    assert (rows["R1"]["first_seen"], rows["R1"]["last_seen"]) == ("2026-10-14", "2026-10-14")
    assert (rows["R2"]["first_seen"], rows["R2"]["last_seen"]) == ("2026-10-14", "2026-10-15")
    assert rows["R3"]["first_seen"] == "2026-10-15"
    assert (rows["R1"]["quereinstieg"], rows["R2"]["quereinstieg"]) == ("1", "0")
    assert (rows["R2"]["fulltime"], rows["R2"]["parttime"], rows["R1"]["parttime"]) == ("1", "1", "0")
    assert rows["R1"]["academy"] == "hr" and rows["R1"]["job_title"] == "recruiter"
    assert rows["R1"]["city"] == "Berlin" and rows["R1"]["lat"] == "52.52"
    counts = read(os.path.join(kdir, "counts.csv"))
    assert [(r["date"], r["job_title"], r["postings"]) for r in counts if r["job_title"] == "recruiter"] == [
        ("2026-10-14", "recruiter", "2"), ("2026-10-15", "recruiter", "2")]
    assert os.path.exists(os.path.join(raw, "2026-10-15", "kurs.jsonl.gz"))


def test_running_a_day_twice_replaces_its_counts(small, tmp_path):
    kdir = str(tmp_path / "kurs")
    for titles in ([("R1", "Recruiter")], [("R1", "Recruiter"), ("R2", "Recruiter")]):
        kurs.run("2026-10-14", config_path=small, kurs_dir=kdir, raw_dir=str(tmp_path / "raw"),
                 search=results(titles), sleep=lambda s: None)
    counts = [r for r in read(os.path.join(kdir, "counts.csv")) if r["job_title"] == "recruiter"]
    assert [(r["date"], r["postings"]) for r in counts] == [("2026-10-14", "2")]


def test_dry_run_writes_nothing(small, tmp_path):
    kdir = tmp_path / "kurs"
    kurs.run("2026-10-14", dry_run=True, config_path=small, kurs_dir=str(kdir), raw_dir=str(tmp_path / "raw"),
             search=results([("R1", "Recruiter")]), sleep=lambda s: None)
    assert not kdir.exists() and not (tmp_path / "raw").exists()


def test_a_failed_request_writes_nothing(small, tmp_path):
    kdir = tmp_path / "kurs"
    search = FakeSearch({"Recruiter": [rec("R1", "Recruiter")]}, fail_on="Sachbearbeiter")
    with pytest.raises(api.ApiError):
        kurs.run("2026-10-14", config_path=small, kurs_dir=str(kdir), raw_dir=str(tmp_path / "raw"),
                 search=search, sleep=lambda s: None)
    assert not kdir.exists()


def test_a_sudden_drop_writes_nothing(small, tmp_path):
    kdir = str(tmp_path / "kurs")
    many = [(f"R{i}", "Recruiter") for i in range(3)]
    kurs.run("2026-10-14", config_path=small, kurs_dir=kdir, raw_dir=str(tmp_path / "raw"),
             search=results(many), sleep=lambda s: None)
    with pytest.raises(kurs.KursError, match="down from 3"):
        kurs.run("2026-10-15", config_path=small, kurs_dir=kdir, raw_dir=str(tmp_path / "raw"),
                 search=results([("R9", "Recruiter")]), sleep=lambda s: None)
    assert {r["date"] for r in read(os.path.join(kdir, "counts.csv"))} == {"2026-10-14"}


def test_nothing_found_is_an_error(small, tmp_path):
    with pytest.raises(kurs.KursError, match="no posting"):
        kurs.run("2026-10-14", config_path=small, kurs_dir=str(tmp_path / "k"), raw_dir=str(tmp_path / "r"),
                 search=FakeSearch({}), sleep=lambda s: None)


def test_main_exits_1_and_writes_nothing_when_the_api_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(kurs, "KURS_DIR", str(tmp_path / "kurs"))
    monkeypatch.setattr(kurs, "search_page", FakeSearch({}, fail_on="Account Executive"))
    monkeypatch.setattr(kurs.time, "sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["kurs"])
    # run() reads its defaults at definition time, so patch it to use the patched search.
    real_run = kurs.run
    monkeypatch.setattr(kurs, "run", lambda day, dry_run=False: real_run(
        day, dry_run, kurs_dir=str(tmp_path / "kurs"), raw_dir=str(tmp_path / "raw"),
        search=kurs.search_page, sleep=lambda s: None))
    assert kurs.main() == 1
    assert not (tmp_path / "kurs").exists()


def test_table_marks_estimates():
    config = {"academies": [{"id": "hr", "name": "HR", "titles": ["recruiter"]}],
              "titles": [{"id": "recruiter", "name": "Recruiter"}]}
    counts = {"recruiter": {"postings": 500, "estimated": 1084, "complete": 0, "ba_total": 2343}}
    assert "| HR | Recruiter | 500 | ≈ 1084 | 2343 |" in kurs.table(counts, config)
