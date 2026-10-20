"""The match score in docs/assets/score.js (run with Node)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCORE_JS = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "score.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def score(tmp_path, skills, nice, have, basic=()):
    script = tmp_path / "score.mjs"
    script.write_text(f"import {{ scoreJob }} from {json.dumps(SCORE_JS)};\n"
                      f"console.log(JSON.stringify(scoreJob({json.dumps(skills)}, {json.dumps(nice)}, "
                      f"new Set({json.dumps(have)}), new Set({json.dumps(list(basic))}))));\n")
    return json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)


def test_required_count_80_and_nice_to_have_20_percent(tmp_path):
    # Required 1, 2, 3, 4 (have 1, 2, 3); nice to have 5, 6 (have 5).
    r = score(tmp_path, [1, 2, 3, 4, 5, 6], [5, 6], [1, 2, 3, 5, 9])
    assert r["score"] == pytest.approx(0.8 * 3 / 4 + 0.2 * 1 / 2)
    assert r["few"] is False
    assert (r["have"], r["missingMust"], r["missingNice"]) == ([1, 2, 3, 5], [4], [6])


def test_all_skills_give_100_and_none_give_0(tmp_path):
    assert score(tmp_path, [1, 2, 3], [3], [1, 2, 3])["score"] == 1
    assert score(tmp_path, [1, 2, 3], [3], [])["score"] == 0


def test_without_nice_to_have_skills_the_required_ones_carry_the_score(tmp_path):
    # Rules-only postings have no nice-to-have split: every skill counts as required.
    r = score(tmp_path, [1, 2, 3, 4], [], [1, 2, 3])
    assert r["score"] == pytest.approx(0.75)
    assert r["missingNice"] == []


def test_only_nice_to_have_skills_carry_the_whole_score(tmp_path):
    # No required skill to miss: having one of two nice-to-have skills is 50 %, not 80 % + 10 %.
    r = score(tmp_path, [1, 2], [1, 2], [1])
    assert r["score"] == pytest.approx(0.5)
    assert (r["missingMust"], r["missingNice"]) == ([], [2])


def test_fewer_than_two_skills_get_a_flag_instead_of_a_score(tmp_path):
    r = score(tmp_path, [7], [], [7])
    assert (r["score"], r["few"], r["have"]) == (None, True, [7])
    r = score(tmp_path, [7], [7], [])
    assert (r["score"], r["few"], r["missingNice"]) == (None, True, [7])


def test_basic_skills_weigh_a_quarter(tmp_path):
    # Required: 1 MS Office, 2 Excel (basic, have both), 3 SAP (missing): 0.5 / 1.5 instead of 2 / 3.
    r = score(tmp_path, [1, 2, 3], [], [1, 2], basic=[1, 2])
    assert r["score"] == pytest.approx(0.5 / 1.5)
    # Having the specific skill and missing the basics: 1 / 1.5.
    assert score(tmp_path, [1, 2, 3], [], [3], basic=[1, 2])["score"] == pytest.approx(1 / 1.5)
    # Nice to have weighs the same way: required 3 (have), nice 1 basic (have) and 4 (missing).
    r = score(tmp_path, [1, 3, 4], [1, 4], [1, 3], basic=[1])
    assert r["score"] == pytest.approx(0.8 + 0.2 * 0.25 / 1.25)


def test_required_basics_only_get_a_quarter_of_their_80_percent(tmp_path):
    # Required: 1 MS Office, 2 Excel (basic, have both); nice to have: 3 Python, 4 SQL (missing).
    # Weights 0.8 × 0.25 and 0.2: having all the basics is 50 %, not 80 %.
    r = score(tmp_path, [1, 2, 3, 4], [3, 4], [1, 2], basic=[1, 2])
    assert r["score"] == pytest.approx(0.5)
    assert score(tmp_path, [1, 2, 3, 4], [3, 4], [3, 4], basic=[1, 2])["score"] == pytest.approx(0.5)


def test_basic_skills_only_get_a_flag_instead_of_a_score(tmp_path):
    r = score(tmp_path, [1, 2, 5], [], [1, 2, 5], basic=[1, 2, 5])
    assert (r["score"], r["few"], r["have"]) == (None, True, [1, 2, 5])
    # One specific skill next to the basics is enough for a score.
    assert score(tmp_path, [1, 9], [], [1, 9], basic=[1])["score"] == 1


def test_no_skills_at_all(tmp_path):
    assert score(tmp_path, [], [], [1, 2]) == {"score": None, "few": True, "have": [], "missingMust": [], "missingNice": []}
