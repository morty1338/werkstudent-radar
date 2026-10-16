"""The n8n workflow for Telegram alerts (integrations/n8n/): its two Code node scripts run in
Node against payloads signed by radar/notify.py, and the importable JSON is up to date."""

import importlib.util
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from radar import notify

N8N = Path(__file__).resolve().parent.parent / "integrations" / "n8n"
SECRET = "test-secret"

spec = importlib.util.spec_from_file_location("build_workflow", N8N / "build_workflow.py")
build_workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_workflow)

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

# A Code node script runs as the body of an async function, like in n8n: $input, $env,
# $getWorkflowStaticData, require and this.helpers.getBinaryDataBuffer are stand-ins.
# URL and URLSearchParams are hidden: n8n's Code node sandbox doesn't have them.
HARNESS = """
const fs = require('fs');
const ctx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const code = fs.readFileSync(process.argv[3], 'utf8');
const state = ctx.state || {};
const $input = { first: () => ({ json: ctx.json }) };
const $env = ctx.envBlocked ? new Proxy({}, { get() { throw new Error('access to env vars denied'); } }) : (ctx.env || {});
const $getWorkflowStaticData = () => state;
const self = { helpers: { getBinaryDataBuffer: async (i, name) => Buffer.from(ctx.raw, 'base64') } };
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('$input', '$env', '$getWorkflowStaticData', 'require', 'URL', 'URLSearchParams', code);
fn.call(self, $input, $env, $getWorkflowStaticData, require, undefined, undefined)
  .then((out) => console.log(JSON.stringify({ out, state })))
  .catch((e) => { console.error(e); process.exit(1); });
"""


def run_code(tmp_path, name, *, json_in, raw=b"", env=None, env_blocked=False, state=None, code=None):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS)
    script = tmp_path / f"{name}.js"
    script.write_text(code if code is not None else (N8N / "code" / f"{name}.js").read_text())
    ctx = tmp_path / "ctx.json"
    import base64
    ctx.write_text(json.dumps({"json": json_in, "raw": base64.b64encode(raw).decode(), "env": env or {},
                               "envBlocked": env_blocked, "state": state or {}}))
    r = subprocess.run(["node", str(harness), str(ctx), str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def job(i, **kw):
    j = {"id": f"r{i}", "title": f"Werkstudent {i}", "company": f"Firma {i}", "city": "Berlin", "field": "it",
         "field_label": "IT & Software", "skills": [], "pay": None, "german": "required", "no_german": False,
         "remote": False, "source": "ba", "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/r{i}"}
    return {**j, **kw}


def signed_call(data, *, secret=SECRET, now=None, body=None):
    body = body if body is not None else notify.encode(data)
    h = notify.headers(secret, body, now=now)
    # n8n lower-cases header names
    return {"headers": {k.lower(): v for k, v in h.items()}, "body": json.loads(body) if body[:1] == b"{" else {}}, body


# --- Check signature -----------------------------------------------------------------------------

def check(tmp_path, data=None, **kw):
    data = data or notify.payload("2026-10-14", [job(1)])
    sign_kw = {k: kw.pop(k) for k in ("secret", "now", "body") if k in kw}
    json_in, raw = signed_call(data, **sign_kw)
    kw.setdefault("env", {"WEBHOOK_SECRET": SECRET})
    return run_code(tmp_path, "check_signature", json_in=json_in, raw=raw, **kw)


@needs_node
def test_a_call_signed_by_the_pipeline_passes(tmp_path):
    data = notify.payload("2026-10-14", [job(1, title="Werkstudent Ä & <b>")])
    r = check(tmp_path, data)
    assert r["out"] == [{"json": {"ok": True, "status": 200, "payload": data}}]
    assert r["state"] == {}                                   # "Build messages" marks the day as sent


@needs_node
@pytest.mark.parametrize("kw, reason", [
    ({"secret": "wrong"}, "bad signature"),
    ({"now": time.time() - 3600}, "missing or expired timestamp"),
    ({"now": time.time() + 3600}, "missing or expired timestamp"),
])
def test_fake_or_replayed_calls_are_rejected(tmp_path, kw, reason):
    assert check(tmp_path, **kw)["out"] == [{"json": {"ok": False, "status": 401, "reason": reason}}]


@needs_node
def test_a_changed_body_is_rejected(tmp_path):
    data = notify.payload("2026-10-14", [job(1)])
    json_in, raw = signed_call(data)
    raw = raw.replace(b"Werkstudent 1", b"Werkstudent X")
    r = run_code(tmp_path, "check_signature", json_in=json_in, raw=raw, env={"WEBHOOK_SECRET": SECRET})
    assert r["out"][0]["json"]["reason"] == "bad signature"


@needs_node
def test_missing_headers_and_missing_secret(tmp_path):
    r = run_code(tmp_path, "check_signature", json_in={"headers": {}}, raw=b"{}", env={"WEBHOOK_SECRET": SECRET})
    assert r["out"][0]["json"]["status"] == 401
    assert check(tmp_path, env={})["out"][0]["json"] == {"ok": False, "status": 500, "reason": "WEBHOOK_SECRET is not set in n8n"}
    # n8n blocks $env by default: then the secret has to be pasted into the script
    assert check(tmp_path, env_blocked=True)["out"][0]["json"]["status"] == 500
    pasted = (N8N / "code" / "check_signature.js").read_text().replace("const SECRET = '';", f"const SECRET = '{SECRET}';")
    assert check(tmp_path, env_blocked=True, code=pasted)["out"][0]["json"]["ok"] is True


@needs_node
def test_signed_but_not_ours(tmp_path):
    assert check(tmp_path, body=b"not json")["out"][0]["json"] == {"ok": False, "status": 400, "reason": "body is not JSON"}
    assert check(tmp_path, {"event": "other"})["out"][0]["json"]["reason"] == "unknown event"


@needs_node
def test_a_posting_is_sent_once_but_tests_always(tmp_path):
    sent = {"lastAsOf": "2026-10-14", "sentIds": ["r1"]}
    r = check(tmp_path, state=sent)
    assert r["out"][0]["json"] == {"ok": False, "status": 200, "reason": "2026-10-14 was already sent"}
    r = check(tmp_path, notify.payload("2026-10-14", [job(1)], test=True), state=sent)
    assert r["out"][0]["json"]["ok"] is True
    r = check(tmp_path, notify.payload("2026-10-15", [job(1)]), state=sent)
    assert r["out"][0]["json"] == {"ok": True, "status": 200, "payload": notify.payload("2026-10-15", [job(1)])}
    assert r["state"] == sent                                 # only "Build messages" changes it


@needs_node
def test_a_repeated_run_passes_only_the_postings_found_since(tmp_path):
    """A second collection on the same day finds postings published later that day."""
    data = notify.payload("2026-10-14", [job(1), job(2), job(3)])
    data["total"] = 7                                         # more than MAX_JOBS fit in the payload
    r = check(tmp_path, data, state={"lastAsOf": "2026-10-14", "sentIds": ["r1", "r3"]})
    assert r["out"][0]["json"] == {"ok": True, "status": 200,
                                   "payload": {**data, "jobs": [job(2)], "total": 5, "more": True}}


@needs_node
def test_a_day_stored_without_ids_counts_as_sent(tmp_path):
    """State written before the ids were stored: posting the day again could repeat everything."""
    r = check(tmp_path, notify.payload("2026-10-14", [job(1), job(2)]), state={"lastAsOf": "2026-10-14"})
    assert r["out"][0]["json"]["reason"] == "2026-10-14 was already sent"


# --- Build messages ------------------------------------------------------------------------------

def messages(tmp_path, jobs, targets, *, test=False, state=False, before=None, more=False, as_of="2026-10-14"):
    code = (N8N / "code" / "build_messages.js").read_text()
    code, n = re.subn(r"const TARGETS = \[.*?\n\];", lambda _: f"const TARGETS = {json.dumps(targets)};", code, flags=re.S)
    assert n == 1
    data = notify.payload(as_of, jobs, test=test)
    if more:
        data["more"] = True
    r = run_code(tmp_path, "build_messages", json_in={"ok": True, "payload": data}, code=code, state=before)
    return (r["out"], r["state"]) if state else r["out"]


def target(**kw):
    return {"target": "me", "chatId": "42", "cities": [], "includeRemote": False, "fields": [], "skills": [],
            "noGerman": False, **kw}


@needs_node
def test_filters(tmp_path):
    jobs = [
        job(1),
        job(2, city="München"),
        job(3, city="", remote=True),
        job(4, field="data", skills=["Python", "SQL"]),
        job(5, no_german=True, german="none"),
    ]
    ids = lambda t: re.findall(r"jobdetail/(r\d)", messages(tmp_path, jobs, [t])[0]["json"]["text"])
    assert sorted(ids(target(cities=["Berlin"]))) == ["r1", "r4", "r5"]
    assert sorted(ids(target(cities=["Berlin"], includeRemote=True))) == ["r1", "r3", "r4", "r5"]
    assert ids(target(fields=["data"])) == ["r4"]
    assert ids(target(skills=["sql", "Go"])) == ["r4"]                  # any of them, any case
    assert ids(target(noGerman=True)) == ["r5"]
    assert messages(tmp_path, jobs, [target(cities=["Hamburg"])]) == []  # nothing to send


@needs_node
def test_the_day_and_its_postings_are_marked_as_sent_after_building(tmp_path):
    day = {"lastAsOf": "2026-10-14", "sentIds": ["r1"]}
    assert messages(tmp_path, [job(1)], [target()], state=True)[1] == day
    assert messages(tmp_path, [job(1)], [target(cities=["Hamburg"])], state=True)[1] == day   # unwanted too
    assert messages(tmp_path, [job(1)], [target()], test=True, state=True)[1] == {}       # tests don't count
    # the same day again adds to the ids, a new day starts over
    assert messages(tmp_path, [job(2)], [target()], state=True, before=day, more=True)[1] == {
        "lastAsOf": "2026-10-14", "sentIds": ["r1", "r2"]}
    assert messages(tmp_path, [job(2)], [target()], state=True, before=day, as_of="2026-10-15")[1] == {
        "lastAsOf": "2026-10-15", "sentIds": ["r2"]}


@needs_node
def test_postings_found_later_that_day_say_more(tmp_path):
    text = messages(tmp_path, [job(2), job(3)], [target(cities=["Berlin"])], more=True)[0]["json"]["text"]
    assert text.startswith("<b>2 more new Werkstudent jobs · Berlin</b>\n14 Oct 2026\n\n")


@needs_node
def test_one_message_per_target(tmp_path):
    out = messages(tmp_path, [job(1), job(2, city="München")], [
        target(target="channel", chatId="@radar_berlin", cities=["Berlin"]),
        target(chatId="123", cities=["Hamburg"]),
        target(target="me", chatId="456"),
    ])
    assert [(o["json"]["target"], o["json"]["chatId"], o["json"]["count"]) for o in out] == [
        ("channel", "@radar_berlin", 1), ("me", "456", 2)]


@needs_node
def test_message_text(tmp_path):
    jobs = [job(1, title="Werkstudent <Data> & BI", company="A&B GmbH", pay=17.5, field_label="Data & Analytics"),
            job(2, pay=None), job(3, city="", remote=True, pay=21)]
    text = messages(tmp_path, jobs, [target(cities=["Berlin"], includeRemote=True)], test=True)[0]["json"]["text"]
    assert text.startswith("🧪 Test · <b>3 new Werkstudent jobs · Berlin</b>\n14 Oct 2026\n\n")
    lines = text.split("\n\n")[1].split("\n")
    assert lines[0] == '• <a href="https://www.arbeitsagentur.de/jobsuche/jobdetail/r3">Werkstudent 3</a>'   # best pay first
    assert lines[1] == "   Firma 3 · Remote (DE) · €21.00/h · IT &amp; Software"
    assert lines[2] == '• <a href="https://www.arbeitsagentur.de/jobsuche/jobdetail/r1">Werkstudent &lt;Data&gt; &amp; BI</a>'
    assert lines[3] == "   A&amp;B GmbH · Berlin · €17.50/h · Data &amp; Analytics"
    assert lines[5] == "   Firma 2 · Berlin · IT &amp; Software"                                            # no pay known
    assert text.endswith('<a href="https://morty1338.github.io/werkstudent-radar/?city=Berlin">Werkstudent Radar</a>')


@needs_node
def test_at_most_ten_then_a_link_to_the_site(tmp_path):
    jobs = [job(i, field="data") for i in range(14)]
    out = messages(tmp_path, jobs, [target(cities=["Berlin"], fields=["data"], noGerman=False), target(skills=["x"])])
    text = out[0]["json"]["text"]
    assert out[0]["json"]["count"] == 14 and len(out) == 1
    assert text.count("• ") == 10
    assert text.endswith('<a href="https://morty1338.github.io/werkstudent-radar/?field=data&amp;city=Berlin">'
                         "+4 more on Werkstudent Radar</a>")
    assert "🧪" not in text and len(text) < 4096


# --- The importable workflow ---------------------------------------------------------------------

def test_workflow_json_is_up_to_date():
    assert (N8N / "werkstudent-radar-alerts.json").read_text() == build_workflow.render(), \
        "run python integrations/n8n/build_workflow.py"


def test_workflow_wiring_and_no_secrets():
    wf = json.loads((N8N / "werkstudent-radar-alerts.json").read_text())
    names = {n["name"] for n in wf["nodes"]}
    for src, conns in wf["connections"].items():
        assert src in names
        assert all(c["node"] in names for out in conns["main"] for c in out)
    hook = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["options"]["rawBody"] is True                      # the signature needs the raw bytes
    assert hook["parameters"]["path"] == "werkstudent-radar"
    text = json.dumps(wf)
    assert "const SECRET = '';" in text and "@your_channel" in text
    assert not re.search(r"\d{8,10}:[A-Za-z0-9_-]{30,}", text)                   # no Telegram bot token
    telegram = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.telegram"]
    assert len(telegram) == 2 and all(n["credentials"]["telegramApi"]["id"] == "" for n in telegram)
