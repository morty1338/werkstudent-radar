"""The read-only live check before a demo (radar/smoke.py), with a stand-in for the network."""

import json
from datetime import date

from radar import smoke

TODAY = date(2026, 11, 1)


def site(as_of="2026-11-01", override=None):
    """A stand-in for the live services: url -> (status, text); `override` maps a URL's end to an answer."""
    pages = {smoke.SITE_URL: (200, "<html>"), f"{smoke.MCP_URL}/health": (200, json.dumps({"status": "ok", "data_as_of": as_of}))}
    for name in smoke.DATA_FILES:
        pages[f"{smoke.SITE_URL}data/{name}"] = (200, json.dumps({"as_of": as_of}))
    for end, answer in (override or {}).items():
        pages[next(u for u in pages if u.endswith(end))] = answer
    return lambda url: pages[url.split("?")[0]]   # data URLs carry a cache-busting ?t=


def results(get):
    return {name: (ok, detail) for ok, name, detail in smoke.checks(get, TODAY)}


def test_everything_up_and_fresh_passes():
    res = results(site())
    assert all(ok for ok, _ in res.values())
    assert res["MCP server"][1] == "HTTP 200, data as of 2026-11-01"


def test_yesterdays_data_is_fresh_enough_but_two_days_is_not():
    assert all(ok for ok, _ in results(site("2026-10-31")).values())
    res = results(site("2026-10-30"))
    assert res["Werkstudent data"] == (False, "as_of 2026-10-30 (2 days old)")
    assert res["Site answers"][0] and not res["MCP server"][0]   # MCP serves the same stale day


def test_the_mcp_server_fails_on_stale_data_but_not_on_a_cold_start():
    stale = json.dumps({"status": "ok", "data_as_of": "2026-10-20"})
    assert results(site(override={"/health": (200, stale)}))["MCP server"] == (False, "HTTP 200, data as of 2026-10-20")
    cold = json.dumps({"status": "ok", "data_as_of": None})
    assert results(site(override={"/health": (200, cold)}))["MCP server"] == (True, "HTTP 200")


def test_a_date_in_the_future_fails():
    assert results(site("2026-11-02"))["Werkstudent data"][0] is False


def test_missing_file_or_bad_json_fails_with_the_status():
    res = results(site(override={"companies.json": (404, "Not Found"), "/kurs.json": (200, "<html>")}))
    assert res["Company career pages"] == (False, "HTTP 404, no as_of")
    assert res["Opportunities"] == (False, "HTTP 200, no as_of")


def test_a_network_error_names_only_its_type():
    res = results(site(override={"/health": (0, "ConnectionError"), "data/summary.json": (0, "ReadTimeout")}))
    assert res["MCP server"] == (False, "ConnectionError")
    assert res["Werkstudent data"] == (False, "ReadTimeout, no as_of")


def test_main_exits_1_and_says_how_many_failed(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "fetch", site("2026-10-20"))
    assert smoke.main([], today=TODAY) == 1
    out = capsys.readouterr().out
    assert "✗ Werkstudent data: as_of 2026-10-20 (12 days old)" in out
    assert out.rstrip().endswith(f"{len(smoke.DATA_FILES) + 1} check(s) failed.")   # the data files and MCP; the site passes


def test_data_urls_bypass_the_pages_cache():
    urls = []
    list(smoke.checks(lambda url: urls.append(url) or (404, ""), TODAY))
    assert all("?t=" in u for u in urls if "/data/" in u) and smoke.SITE_URL in urls
