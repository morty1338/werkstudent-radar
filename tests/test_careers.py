import csv

import pytest

from radar import careers

ACME = {"name": "Acme", "ats": "greenhouse", "slug": "acme", "cities": ["Berlin"], "careers_url": "https://acme.de/jobs",
        "ba_names": ["Acme Deutschland GmbH"]}
BETA = {"name": "Beta", "ats": "lever", "slug": "beta", "cities": ["München"], "careers_url": None, "ba_names": []}


def job(company="Acme", title="Werkstudent Data (m/w/d)", city="Berlin", url="https://x/1"):
    return {"company": company, "title": title, "city": city, "remote": False, "url": url,
            "posted_at": "2026-10-01", "source_ats": "greenhouse"}


def ok(board, jobs):
    return {"ok": True, "board": board, "jobs": jobs, "error": None}


@pytest.mark.parametrize("a, b", [
    ("Werkstudent Data (m/w/d)", "Werkstudent:in Data (w/m/d)"),
    ("Werkstudentin Data", "Working Student – Data (all genders)"),
    ("Werkstudierende Data (gn)", "werkstudent data"),
    ("Werkstudent*in (all gender) Data", "Werkstudent Data (m|w|d)"),
])
def test_norm_title_matches_variants(a, b):
    assert careers.norm_title(a) == careers.norm_title(b)


def test_norm_title_keeps_what_differs():
    assert careers.norm_title("Werkstudent Data") != careers.norm_title("Werkstudent Marketing")
    assert careers.norm_company("Acme Group GmbH") == careers.norm_company("ACME")


def test_online_postings_reads_the_latest_day(tmp_path):
    path = tmp_path / "jobs.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "city", "title", "source", "last_seen"])
        w.writerow(["Acme Deutschland GmbH", "Berlin", "Werkstudent Data (m/w/d)", "ba", "2026-10-13"])
        w.writerow(["Acme GmbH", "Berlin", "Working Student Data", "arbeitnow", "2026-10-13"])
        w.writerow(["Old AG", "Berlin", "Werkstudent X", "ba", "2026-10-01"])
    assert careers.online_postings(path) == {
        ("ba", "Acme Deutschland GmbH", "Berlin", "werkstudent data"),
        ("arbeitnow", "acme", "Berlin", "werkstudent data"),
    }


def test_also_on_needs_company_title_and_city():
    postings = {("ba", "Acme Deutschland GmbH", "Berlin", "werkstudent data"),
                ("arbeitnow", "acme", "Berlin", "werkstudent data"),
                ("ba", "Other GmbH", "Berlin", "werkstudent marketing")}
    assert careers.also_on(job(), ACME, postings) == ["arbeitnow", "ba"]
    assert careers.also_on(job(city="München"), ACME, postings) == []
    assert careers.also_on(job(title="Werkstudent Marketing"), ACME, postings) == []
    assert careers.also_on(job(city=""), ACME, postings) == ["arbeitnow", "ba"]     # remote: any city


def test_first_day_has_no_new_jobs_and_fields_are_set():
    data, warnings = careers.build([ACME], [job()], {"Acme": ok(10, 1)}, None, set(), "2026-10-13")
    assert warnings == []
    (j,) = data["jobs"]
    assert (j["first_seen"], j["new"], j["field"], j["also_on"]) == ("2026-10-13", False, "data", [])
    assert data["companies"] == [{"name": "Acme", "ats": "greenhouse", "careers_url": "https://acme.de/jobs", "cities": ["Berlin"],
                                  "board": 10, "jobs": 1, "ok": True, "since": "2026-10-13"}]
    assert "description" not in j and "text" not in j


def test_next_day_carries_first_seen_and_marks_new_jobs():
    day1, _ = careers.build([ACME], [job()], {"Acme": ok(10, 1)}, None, set(), "2026-10-13")
    fetched = [job(), job(title="Werkstudent HR", url="https://x/2")]
    day2, _ = careers.build([ACME], fetched, {"Acme": ok(11, 2)}, day1, set(), "2026-10-14")
    seen = {j["url"]: (j["first_seen"], j["new"]) for j in day2["jobs"]}
    assert seen == {"https://x/1": ("2026-10-13", False), "https://x/2": ("2026-10-14", True)}
    # A company added today doesn't mark its jobs as new.
    day2b, _ = careers.build([ACME, BETA], fetched + [job("Beta", city="München", url="https://y/1")],
                             {"Acme": ok(11, 2), "Beta": ok(5, 1)}, day1, set(), "2026-10-14")
    assert [j["new"] for j in day2b["jobs"] if j["company"] == "Beta"] == [False]
    # Running twice on the same day gives the same result.
    again, _ = careers.build([ACME], fetched, {"Acme": ok(11, 2)}, day2, set(), "2026-10-14")
    assert again["jobs"] == day2["jobs"]


def test_unreadable_board_keeps_previous_jobs_for_a_few_days_and_warns():
    day1, _ = careers.build([ACME], [job()], {"Acme": ok(10, 1)}, None, set(), "2026-10-13")
    failed = {"Acme": {"ok": False, "board": None, "jobs": 0, "error": "HTTP 500 from …", "not_found": False}}
    day2, warnings = careers.build([ACME], [], failed, day1, set(), "2026-10-14")
    assert [j["url"] for j in day2["jobs"]] == ["https://x/1"]
    assert day2["companies"][0]["ok"] is False and day2["companies"][0]["board"] == 10
    assert warnings == ["Acme: the greenhouse board couldn't be read: HTTP 500 from …"]
    later, _ = careers.build([ACME], [], failed, day1, set(), "2026-10-20")
    assert later["jobs"] == []


def test_warnings_for_404_and_an_emptied_board():
    day1, _ = careers.build([ACME, BETA], [job()], {"Acme": ok(10, 1), "Beta": ok(3, 0)}, None, set(), "2026-10-13")
    status = {"Acme": ok(0, 0), "Beta": {"ok": False, "board": None, "jobs": 0, "error": "HTTP 404", "not_found": True}}
    _, warnings = careers.build([ACME, BETA], [], status, day1, set(), "2026-10-14")
    assert warnings == [
        "Acme: 0 postings on the greenhouse board after 10 on 2026-10-13; check ats/slug",
        "Beta: the lever board 'beta' answers 404; fix ats/slug in data/companies.yaml",
    ]
