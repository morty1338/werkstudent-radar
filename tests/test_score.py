"""The match score in docs/assets/score.js (run with Node)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCORE_JS = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "score.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def score(tmp_path, skills, nice, have):
    script = tmp_path / "score.mjs"
    script.write_text(f"import {{ scoreJob }} from {json.dumps(SCORE_JS)};\n"
                      f"console.log(JSON.stringify(scoreJob({json.dumps(skills)}, {json.dumps(nice)}, new Set({json.dumps(have)}))));\n")
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


def test_no_skills_at_all(tmp_path):
    assert score(tmp_path, [], [], [1, 2]) == {"score": None, "few": True, "have": [], "missingMust": [], "missingNice": []}
