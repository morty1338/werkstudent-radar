"""The application tracker's data and rules (docs/assets/tracker-store.js), run with Node
against an in-memory stand-in for localStorage."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STORE_JS = (Path(__file__).resolve().parent.parent / "docs" / "assets" / "tracker-store.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PRELUDE = f"""
import * as T from {json.dumps(STORE_JS)};
class Memory {{
  constructor(data = {{}}) {{ this.data = {{ ...data }}; }}
  getItem(k) {{ return k in this.data ? this.data[k] : null; }}
  setItem(k, v) {{ this.data[k] = String(v); }}
  removeItem(k) {{ delete this.data[k]; }}
}}
class Blocked {{
  getItem() {{ throw new Error("SecurityError"); }}
  setItem() {{ throw new Error("SecurityError"); }}
  removeItem() {{}}
}}
const out = {{}};
"""


def run(tmp_path, body):
    script = tmp_path / "tracker.mjs"
    script.write_text(PRELUDE + body + "\nconsole.log(JSON.stringify(out));\n")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_add_cleans_input(tmp_path):
    out = run(tmp_path, """
const s = T.createStore(new Memory());
out.a = s.add({ title: "  Werkstudent Data  ", company: " Acme ", url: "javascript:alert(1)", note: "x".repeat(3000) }, "2026-10-13");
out.noTitle = s.add({ title: "   " });
out.http = s.add({ title: "B", url: "http://example.org/job" }, "2026-10-13").url;
out.count = s.all().length;
""")
    a = out["a"]
    assert (a["title"], a["company"], a["status"], a["source"], a["added"]) == ("Werkstudent Data", "Acme", "saved", "manual", "2026-10-13")
    assert a["url"] == ""                                     # only http(s) links, never javascript:
    assert len(a["note"]) == 2000
    assert a["history"] == [{"status": "saved", "date": "2026-10-13"}]
    assert out["noTitle"] is None and out["http"] == "http://example.org/job" and out["count"] == 2


def test_a_posting_is_tracked_once(tmp_path):
    out = run(tmp_path, """
const s = T.createStore(new Memory());
const job = { ref: "p:123-S", title: "Werkstudent IT", company: "Acme", city: "Berlin", url: "https://x.org/1", source: "radar" };
const first = s.add(job);
const second = s.add({ ...job, title: "changed" });
out.same = first.id === second.id;
out.count = s.all().length;
out.found = s.findByRef("p:123-S")?.title;
out.none = s.findByRef(null);
""")
    assert out == {"same": True, "count": 1, "found": "Werkstudent IT", "none": None}


def test_status_history_and_follow_up(tmp_path):
    out = run(tmp_path, """
const s = T.createStore(new Memory());
const a = s.add({ title: "Job" }, "2026-10-01");
s.setStatus(a.id, "applied", "2026-10-02");
s.setStatus(a.id, "applied", "2026-10-03");                 // same status: nothing new
s.update(a.id, { followUp: "2026-10-09", note: "  called  " });
out.dueBefore = T.isOverdue(s.get(a.id), "2026-10-08");
out.dueOn = T.isOverdue(s.get(a.id), "2026-10-09");
s.setStatus(a.id, "interview", "2026-10-10");
out.stillDue = T.isOverdue(s.get(a.id), "2026-10-10");
s.setStatus(a.id, "offer", "2026-10-20");                   // a final status ends the follow-up
out.final = s.get(a.id);
s.update(a.id, { followUp: "not a date", url: "ftp://x" });
out.badFields = [s.get(a.id).followUp, s.get(a.id).url];
s.setStatus(a.id, "hired");                                 // unknown status: ignored
out.status = s.get(a.id).status;
""")
    assert (out["dueBefore"], out["dueOn"], out["stillDue"]) == (False, True, True)
    assert [h["status"] + "@" + h["date"] for h in out["final"]["history"]] == [
        "saved@2026-10-01", "applied@2026-10-02", "interview@2026-10-10", "offer@2026-10-20"]
    assert out["final"]["followUp"] is None and out["final"]["note"] == "called"
    assert out["badFields"] == [None, ""] and out["status"] == "offer"


def test_order_and_counts(tmp_path):
    out = run(tmp_path, """
const day = "2026-10-13";
const items = [
  T.clean({ id: "old", title: "Old", status: "applied", history: [{ status: "applied", date: "2026-10-01" }] }, day),
  T.clean({ id: "new", title: "New", status: "saved", history: [{ status: "saved", date: "2026-10-12" }] }, day),
  T.clean({ id: "due", title: "Due", status: "applied", followUp: "2026-10-10", history: [{ status: "applied", date: "2026-09-01" }] }, day),
  T.clean({ id: "no", title: "No", status: "rejected", followUp: "2026-10-01", history: [{ status: "rejected", date: "2026-10-05" }] }, day),
];
out.order = T.sorted(items, day).map((a) => a.id);
out.counts = T.counts(items, day);
""")
    assert out["order"] == ["due", "new", "no", "old"]       # overdue first, then latest change
    assert out["counts"] == {"saved": 1, "applied": 2, "interview": 0, "offer": 0, "rejected": 1, "all": 4, "overdue": 1}


def test_saved_in_storage_and_read_back(tmp_path):
    out = run(tmp_path, """
const mem = new Memory();
const s = T.createStore(mem);
const a = s.add({ title: "Kept" });
s.setStatus(a.id, "applied");
const again = T.createStore(mem);
out.persistent = again.persistent;
out.items = again.all().map((x) => [x.title, x.status]);
out.version = JSON.parse(mem.getItem(T.KEY)).version;
again.remove(a.id);
out.afterRemove = T.createStore(mem).all().length;
""")
    assert out == {"persistent": True, "items": [["Kept", "applied"]], "version": 1, "afterRemove": 0}


def test_blocked_storage_keeps_working_in_memory(tmp_path):
    out = run(tmp_path, """
let changes = 0;
const s = T.createStore(new Blocked(), { onChange: () => changes++ });
s.add({ title: "Only in memory" });
out.persistent = s.persistent;
out.count = s.all().length;
out.changes = changes;
const none = T.createStore(null);
out.nullStorage = none.persistent;
""")
    assert out == {"persistent": False, "count": 1, "changes": 1, "nullStorage": False}


def test_export_import_merges_and_rejects_bad_files(tmp_path):
    out = run(tmp_path, """
const a = T.createStore(new Memory());
const x = a.add({ title: "Shared" }, "2026-10-01");
a.add({ title: "Only in A" }, "2026-10-02");
const file = a.exportJson();

const b = T.createStore(new Memory());
out.first = b.importJson(file);
out.again = b.importJson(file);                          // nothing new the second time
b.setStatus(x.id, "interview", "2026-10-11");             // B changed it later
out.older = b.importJson(file);
out.keptNewer = b.get(x.id).status;

const c = T.createStore(new Memory());
c.importJson(b.exportJson());
out.c = c.all().map((i) => [i.title, i.status]).sort();

const errors = [];
for (const raw of ["not json", "{}", '{"items": 5}']) {
  try { c.importJson(raw); errors.push(null); } catch (e) { errors.push(e.message); }
}
out.errors = errors;
out.junk = c.importJson(JSON.stringify({ items: [
  { title: "" }, { title: "<b>x</b>", status: "hired", history: "no", followUp: "soon", id: "../../x", url: "javascript:1" }, 42,
] }));
const junk = c.all().find((i) => i.title === "<b>x</b>");
out.junkItem = [junk.status, junk.followUp, junk.url, /^[\\w-]+$/.test(junk.id)];
""")
    assert out["first"] == {"added": 2, "updated": 0}
    assert out["again"] == {"added": 0, "updated": 0}
    assert out["older"] == {"added": 0, "updated": 0} and out["keptNewer"] == "interview"
    assert out["c"] == [["Only in A", "saved"], ["Shared", "interview"]]
    assert out["errors"] == ["not a JSON file", "no applications in this file", "no applications in this file"]
    assert out["junk"] == {"added": 1, "updated": 0}
    assert out["junkItem"] == ["saved", None, "", True]     # text is stored as given; the panel escapes it


def test_today_is_a_local_calendar_day(tmp_path):
    out = run(tmp_path, """
out.day = T.today(new Date(2026, 9, 13, 23, 30));
""")
    assert out["day"] == "2026-10-13"


def test_backup_link_round_trip_merges_into_another_browser(tmp_path):
    """The link carries the whole list (compressed, base64url) and imports into a second browser."""
    out = run(tmp_path, """
const a = T.createStore(new Memory());
a.add({ title: "Recruiter (m/w/d)", company: "Acme", city: "Berlin", url: "https://x.test/1", note: "Grüße, ä ö ü ß €" }, "2026-10-01");
a.add({ title: "Sachbearbeiter", ref: "k:123", source: "radar" }, "2026-10-02");
const data = await T.toLinkData(a.exportJson());
out.urlSafe = /^[A-Za-z0-9_-]+$/.test(data);
out.length = data.length;
const b = T.createStore(new Memory());
b.add({ title: "Already here" }, "2026-10-03");
out.result = b.importJson(await T.fromLinkData(data));
out.titles = b.all().map((x) => x.title).sort();
out.note = b.all().find((x) => x.company === "Acme").note;
out.again = b.importJson(await T.fromLinkData(data));          // opening the link twice adds nothing
""")
    assert out["urlSafe"] and out["length"] < 1000
    assert out["result"] == {"added": 2, "updated": 0} and out["again"] == {"added": 0, "updated": 0}
    assert out["titles"] == ["Already here", "Recruiter (m/w/d)", "Sachbearbeiter"]
    assert out["note"] == "Grüße, ä ö ü ß €"


def test_a_bad_link_is_an_error_not_a_crash(tmp_path):
    out = run(tmp_path, """
try { await T.fromLinkData("not-a-real-link"); out.ok = true; } catch (e) { out.error = true; }
""")
    assert out == {"error": True}


def test_quiet_update_saves_without_redrawing(tmp_path):
    out = run(tmp_path, """
const mem = new Memory();
let redraws = 0;
const s = T.createStore(mem, { onChange: () => redraws++ });
const a = s.add({ title: "Recruiter" });
const before = redraws;
s.update(a.id, { note: "typed, tab closed right after" }, { quiet: true });
out.redraws = redraws - before;
out.saved = JSON.parse(mem.getItem(T.KEY)).items[0].note;
""")
    assert out == {"redraws": 0, "saved": "typed, tab closed right after"}


def test_days_since_backup(tmp_path):
    out = run(tmp_path, """
const mem = new Memory();
out.none = T.daysSinceBackup(mem, "2026-10-15");
T.markBackup(mem, "2026-10-01");
out.days = T.daysSinceBackup(mem, "2026-10-15");
out.blocked = T.daysSinceBackup(new Blocked(), "2026-10-15");
""")
    assert out == {"none": None, "days": 14, "blocked": None}
