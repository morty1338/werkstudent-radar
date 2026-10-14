"""The Werkstudent | Jobs switch (docs/assets/mode.js) and the Jobs-mode strings (i18n.js), in Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(tmp_path, search, expr, module="mode.js"):
    script = tmp_path / "m.mjs"
    script.write_text(
        f"globalThis.location = {{ search: {json.dumps(search)}, href: {json.dumps('https://x.test/index.html' + search)} }};\n"
        f"const m = await import({json.dumps((ASSETS / module).as_uri())});\n"
        f"console.log(JSON.stringify({expr}));\n")
    return json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize("search, expected", [
    ("", "werkstudent"),
    ("?mode=jobs", "werkstudent"),             # Jobs mode is behind the beta flag
    ("?mode=jobs&beta=1", "jobs"),
    ("?beta=1", "werkstudent"),
])
def test_mode_needs_the_beta_flag(tmp_path, search, expected):
    out = run(tmp_path, search, "[m.mode, m.JOBS_PUBLIC]")
    assert out[0] == expected and out[1] is False


def test_jobs_mode_reads_the_jobs_files_and_tracks_with_its_own_keys(tmp_path):
    out = run(tmp_path, "?mode=jobs&beta=1", "[m.dataUrl('summary'), m.dataUrl('companies'), m.dataUrl('patterns'), m.refPrefix]")
    assert out == ["data/jobs_summary.json", "data/jobs_companies.json", "data/patterns.json", {"posting": "k:", "company": "kc:"}]
    out = run(tmp_path, "", "[m.dataUrl('summary'), m.refPrefix]")
    assert out == ["data/summary.json", {"posting": "p:", "company": "c:"}]


def test_switching_keeps_compatible_filters(tmp_path):
    url = run(tmp_path, "?mode=jobs&beta=1&academy=hr&field=recruiter&city=Berlin&german=no&skills=excel&lang=de&co=muenchen",
              "m.modeUrl('werkstudent')")
    assert url == "https://x.test/index.html?beta=1&city=Berlin&german=no&skills=excel&lang=de"
    url = run(tmp_path, "?beta=1&field=it&city=Hamburg", "m.modeUrl('jobs')")
    assert url == "https://x.test/index.html?beta=1&city=Hamburg&mode=jobs"


def test_jobs_strings_replace_the_werkstudent_ones(tmp_path):
    out = run(tmp_path, "?mode=jobs&beta=1&lang=de", "[m.t('tile.median'), m.t('filter.field'), m.t('pay.axis', {n: '615'}), m.mode]",
              module="i18n.js")
    assert out == ["Mediangehalt", "Berufsbild", "€ pro Jahr (Vollzeit) · 615 Rollen mit Gehaltsangabe", "jobs"]
    out = run(tmp_path, "?lang=de", "[m.t('tile.median'), m.mode]", module="i18n.js")
    assert out == ["Medianlohn", "werkstudent"]


def test_every_jobs_string_has_a_werkstudent_original(tmp_path):
    keys = run(tmp_path, "", "m.KEYS", module="i18n.js")
    jobs = [k for k in keys if k.endswith("@jobs")]
    assert len(jobs) > 20
    assert [k for k in jobs if k[:-5] not in keys] == []
