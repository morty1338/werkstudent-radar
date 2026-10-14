"""Daily step: skills and requirements of new or changed postings, extracted by Claude Haiku.

Usage:
    python -m radar.extract_llm             # after radar.enrich in the daily run
    python -m radar.extract_llm --dry-run   # what would be sent and what it would cost; no API call

Only postings whose text is available today (new Bundesagentur postings, whose text
radar.enrich just fetched, and all Arbeitnow postings) and that have no result for
this exact text yet are processed: results are keyed by refnr and a hash of title and
text, so an unchanged posting is never sent twice. Postings without task or
requirement sections are not sent (radar/llm.py); the rules cover them.

The requests go out as one Message Batch (half price). The step waits up to
LLM_WAIT_MINUTES for it; a batch that isn't done by then is collected by the next
run (data/history/llm_pending.json), and until then the rules' results are used.
Without ANTHROPIC_API_KEY, or when the API fails, nothing is sent and the step
still succeeds: the pipeline never fails because of the LLM.

Budget guards (environment, set in daily.yml): LLM_MAX_POSTINGS per run (newest
first) and LLM_MAX_USD, the estimated cost a run may not exceed. Every run's
postings, tokens and cost go to data/history/llm_runs.csv and the run summary,
together with how far the LLM and the rules agree on the same postings.

Committed text tables (no job texts):
    data/history/llm.csv          one row per posting: text hash, skills (must / nice to have,
                                  ids from radar/skills.py, other terms), German and English,
                                  hourly pay, weekly hours
    data/history/llm_runs.csv     one row per run: counts, tokens, cost, agreement with the rules
    data/history/llm_pending.json a batch still being processed, if any
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

from . import arbeitnow, llm
from .enrich import JOBS_CSV, RAW_DIR, latest_raw_dir, load_detail_cache, read_jsonl_gz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(ROOT, "data", "history")
STORE_CSV = os.path.join(HISTORY_DIR, "llm.csv")
RUNS_CSV = os.path.join(HISTORY_DIR, "llm_runs.csv")
PENDING_JSON = os.path.join(HISTORY_DIR, "llm_pending.json")

STORE_COLUMNS = ["refnr", "text_hash", "extracted", "status", "model", "prompt",
                 "must", "nice", "other_must", "other_nice",
                 "german_level", "german_req", "english_level", "english_req",
                 "pay_min", "pay_max", "hours_min", "hours_max"]
RUN_COLUMNS = ["date", "run_at", "trigger", "candidates", "no_sections", "sent", "done", "failed", "pending", "over_budget",
               "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "cost_usd",
               "compared", "same_skills", "skill_agreement", "german_agreement"]

DEFAULTS = {"LLM_MAX_POSTINGS": 1000, "LLM_MAX_USD": 1.0, "LLM_WAIT_MINUTES": 20, "LLM_POLL_SECONDS": 30}
# Per posting at batch prices, from the pilot (data/eval/report_llm.md): about $0.15 per 1,000.
# Used only to stop a run before it gets expensive; the real cost comes from the API's usage.
EST_USD_PER_POSTING = 0.0002
PENDING_MAX_HOURS = 26        # a batch older than this is given up (results expire, the postings are sent again)
SPEND_LIMIT_NOTE = ("Set a monthly spend limit in the Claude Console (platform.claude.com → Settings → Limits) "
                    "so a bug can never cost more than you chose.")

log = logging.getLogger("extract_llm")


def setting(name, cast):
    raw = os.environ.get(name, "").strip()
    try:
        return cast(raw) if raw else DEFAULTS[name]
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, DEFAULTS[name])
        return DEFAULTS[name]


def prompt_version():
    """Short id of the instructions, skill list and schema; stored with each result."""
    blob = llm.system_prompt() + json.dumps(llm.SCHEMA, sort_keys=True) + llm.MODEL + str(llm.EFFORT)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


# --- Inputs ------------------------------------------------------------------------------

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def todays_texts(jobs, raw_dir):
    """{refnr: posting} for today's active postings whose text is available here."""
    day = max(r["last_seen"] for r in jobs.values())
    active = {ref: r for ref, r in jobs.items() if r["last_seen"] == day}
    texts = {}
    for ref, rec in load_detail_cache().items():
        if ref in active and rec.get("stellenangebotsBeschreibung"):
            texts[ref] = rec["stellenangebotsBeschreibung"]
    an_path = os.path.join(raw_dir, "arbeitnow.jsonl.gz")
    if os.path.exists(an_path):
        for job in read_jsonl_gz(an_path):
            ref = arbeitnow.ref(job)
            if ref in active and job.get("description"):
                texts[ref] = arbeitnow.html_to_text(job["description"])
    return day, active, {ref: {"refnr": ref, "title": active[ref]["title"], "text": t} for ref, t in texts.items()}


def candidates(active, postings, store, pending_refs=()):
    """Postings with a text that have no result for this text yet, newest first."""
    out = [p for ref, p in postings.items()
           if ref not in pending_refs and (store.get(ref) or {}).get("text_hash") != llm.text_hash(p)]
    return sorted(out, key=lambda p: (active[p["refnr"]].get("first_seen", ""), p["refnr"]), reverse=True)


# --- Results -----------------------------------------------------------------------------

def _terms(skills, importance):
    return ";".join(s["term"].replace(";", ",").replace("\n", " ") for s in skills if s["id"] == "other" and s["importance"] == importance)


def store_row(posting, result, day, status="ok"):
    row = {"refnr": posting["refnr"], "text_hash": llm.text_hash(posting), "extracted": day, "status": status,
           "model": llm.MODEL, "prompt": prompt_version()}
    if result:
        listed = [s for s in result["skills"] if s["id"] != "other"]
        row.update({
            "must": "|".join(sorted(s["id"] for s in listed if s["importance"] == "must_have")),
            "nice": "|".join(sorted(s["id"] for s in listed if s["importance"] == "nice_to_have")),
            "other_must": _terms(result["skills"], "must_have"),
            "other_nice": _terms(result["skills"], "nice_to_have"),
            "german_level": result["german_level"], "german_req": result["german_requirement"],
            "english_level": result["english_level"], "english_req": result["english_requirement"],
            **{k: "" if result[f] is None else f"{result[f]:g}" for k, f in
               (("pay_min", "pay_hour_min"), ("pay_max", "pay_hour_max"), ("hours_min", "hours_week_min"), ("hours_max", "hours_week_max"))},
        })
    return row


def agreement(rows, jobs):
    """How far the LLM and the rules agree on the postings done in this run."""
    pairs = [(set(filter(None, (r["must"] + "|" + r["nice"]).split("|"))), set(filter(None, jobs[r["refnr"]].get("skills", "").split("|"))),
              r, jobs[r["refnr"]]) for r in rows if r["status"] == "ok" and r["refnr"] in jobs]
    if not pairs:
        return {"compared": 0}, Counter(), Counter()
    both = sum(len(a & b) for a, b, _, _ in pairs)
    total = sum(len(a) + len(b) for a, b, _, _ in pairs)
    german = [llm.german_label({"german_requirement": r["german_req"]}, j.get("lang")) == j.get("german")
              for _, _, r, j in pairs if j.get("german") and r["german_req"]]
    only_llm = Counter(s for a, b, _, _ in pairs for s in a - b)
    only_rules = Counter(s for a, b, _, _ in pairs for s in b - a)
    return {
        "compared": len(pairs),
        "same_skills": round(sum(a == b for a, b, _, _ in pairs) / len(pairs), 3),
        "skill_agreement": round(2 * both / total, 3) if total else 1.0,       # Dice: 1 = identical skill lists
        "german_agreement": round(sum(german) / len(german), 3) if german else "",
    }, only_llm, only_rules


# --- The run -------------------------------------------------------------------------------

def collect_pending(api, pending, postings_by_ref):
    """Results of yesterday's unfinished batch, if it has ended. Returns (records, still pending)."""
    b = api.messages.batches.retrieve(pending["id"])
    if b.processing_status != "ended":
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(pending["submitted"])).total_seconds() / 3600
        return {}, (pending if age_h < PENDING_MAX_HOURS else None)
    refs = {cid: v["refnr"] for cid, v in pending["requests"].items()}
    known = {v["refnr"]: {"refnr": v["refnr"], "title": "", "text": ""} for v in pending["requests"].values()}
    recs = llm.collect_batch(api, b.id, refs, list(known.values()), poll_s=0, timeout_s=0) or {}
    for ref, rec in recs.items():
        rec["hash"] = pending["requests"][llm.batch_custom_id(ref)]["hash"]      # the hash of the text that was sent
    return recs, None


def run(dry_run=False, raw_dir=None, sleep=time.sleep, api=None):
    max_n = setting("LLM_MAX_POSTINGS", int)
    max_usd = setting("LLM_MAX_USD", float)
    wait_s = setting("LLM_WAIT_MINUTES", float) * 60
    poll_s = setting("LLM_POLL_SECONDS", float)

    jobs = {r["refnr"]: r for r in read_csv(JOBS_CSV)}
    day, active, postings = todays_texts(jobs, raw_dir or latest_raw_dir())
    store = {r["refnr"]: r for r in read_csv(STORE_CSV)}
    pending = json.load(open(PENDING_JSON)) if os.path.exists(PENDING_JSON) else None
    pending_refs = {v["refnr"] for v in (pending or {}).get("requests", {}).values()}

    todo = candidates(active, postings, store, pending_refs)
    no_sections = [p for p in todo if not llm.model_input(p)[1]]
    to_send = [p for p in todo if llm.model_input(p)[1]]
    budget_n = min(max_n, int(max_usd / EST_USD_PER_POSTING))
    over_budget = max(0, len(to_send) - budget_n)
    to_send = to_send[:budget_n]
    stats = {"date": day, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "trigger": os.environ.get("GITHUB_EVENT_NAME") or "local", "candidates": len(todo), "no_sections": len(no_sections), "sent": 0, "done": 0, "failed": 0,
             "pending": 0, "over_budget": over_budget}
    log.info("%s: %d postings with a text today, %d without a result for their text (%d without task/requirement "
             "sections, kept with the rules), %d to send%s", day, len(postings), len(todo), len(no_sections), len(to_send),
             f", {over_budget} left for later runs (budget)" if over_budget else "")
    if dry_run:
        log.info("dry run: about $%.3f at batch prices; nothing sent", len(to_send) * EST_USD_PER_POSTING)
        return stats, []

    new_rows = [store_row(p, None, day, status="no_sections") for p in no_sections]
    records = {}
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        log.info("ANTHROPIC_API_KEY not set: nothing sent, the rules' results are used")
    else:
        try:
            api = api or llm.client()
            if pending:
                got, pending = collect_pending(api, pending, postings)
                records.update(got)
                log.info("earlier batch: %s", f"{len(got)} results collected" if got else "still running" if pending else "given up")
            if to_send and not pending:
                batch_id, ids = llm.submit_batch(api, to_send)
                stats["sent"] = len(to_send)
                log.info("batch %s: %d postings sent; waiting up to %.0f minutes", batch_id, len(to_send), wait_s / 60)
                got = llm.collect_batch(api, batch_id, ids, to_send, poll_s=poll_s, timeout_s=wait_s, sleep=sleep)
                if got is None:
                    pending = {"id": batch_id, "submitted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "requests": {cid: {"refnr": ref, "hash": llm.text_hash(postings[ref])} for cid, ref in ids.items()}}
                    log.info("batch not done yet: the next run collects it; the rules' results are used until then")
                else:
                    records.update(got)
            elif to_send:
                log.info("an earlier batch is still running: today's postings wait for the next run")
        except Exception as e:                                   # noqa: BLE001 – never fail the pipeline
            log.warning("LLM step skipped after an API error (%s); the rules' results are used", type(e).__name__)

    by_ref = {**{r: p for r, p in postings.items()}}
    usage = Counter()
    for ref, rec in records.items():
        if rec.get("usage"):
            usage.update(rec["usage"])
        if "error" in rec or not rec.get("result"):
            stats["failed"] += 1                                   # not stored: tried again next run
            continue
        posting = by_ref.get(ref) or {"refnr": ref, "title": "", "text": ""}
        row = store_row(posting, rec["result"], day)
        row["text_hash"] = rec["hash"]
        new_rows.append(row)
        stats["done"] += 1
    stats["pending"] = len((pending or {}).get("requests", {}))

    for row in new_rows:
        store[row["refnr"]] = row
    write_csv(STORE_CSV, STORE_COLUMNS, sorted(store.values(), key=lambda r: r["refnr"]))
    if pending:
        with open(PENDING_JSON, "w", encoding="utf-8") as f:
            json.dump(pending, f, indent=1)
    elif os.path.exists(PENDING_JSON):
        os.remove(PENDING_JSON)

    agree, only_llm, only_rules = agreement(new_rows, jobs)
    stats.update({
        "input_tokens": usage["input"], "cache_read_tokens": usage["cache_read"], "cache_write_tokens": usage["cache_write"],
        "output_tokens": usage["output"], "cost_usd": round(llm.cost({**{k: 0 for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h", "cache_write")}, **usage}, batch=True), 4),
        **agree,
    })
    runs = read_csv(RUNS_CSV) + [stats]                         # every run is kept, also two on one day
    write_csv(RUNS_CSV, RUN_COLUMNS, runs)
    report(stats, only_llm, only_rules)
    return stats, new_rows


def report(s, only_llm, only_rules):
    lines = [
        f"{s['date']}: {s['candidates']} new or changed postings; {s['no_sections']} without sections (rules), "
        f"{s['sent']} sent, {s['done']} results stored, {s['failed']} failed (retried next run), {s['pending']} still pending"
        + (f", {s['over_budget']} over the per-run budget (next run)" if s["over_budget"] else ""),
        f"tokens: {s['input_tokens']:,} input, {s['cache_read_tokens']:,} cached, {s['output_tokens']:,} output; "
        f"cost ${s['cost_usd']:.4f} (batch prices)",
    ]
    if s.get("compared"):
        lines.append(f"LLM vs. rules on {s['compared']} postings: same skill list {s['same_skills']:.0%}, "
                     f"skill agreement {s['skill_agreement']:.0%}"
                     + (f", German requirement {s['german_agreement']:.0%}" if s["german_agreement"] != "" else "")
                     + "; only the LLM: " + (", ".join(f"{k} {v}" for k, v in only_llm.most_common(5)) or "–")
                     + "; only the rules: " + (", ".join(f"{k} {v}" for k, v in only_rules.most_common(5)) or "–"))
    for line in lines:
        log.info(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Skills by Claude Haiku\n\n" + "\n".join(f"- {line}" for line in lines) + f"\n- {SPEND_LIMIT_NOTE}\n\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent and its cost; no API call")
    ap.add_argument("--date", help="raw data folder to use (default: newest)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for name in ("httpx", "httpx2"):                           # one line per API request otherwise
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        run(dry_run=args.dry_run, raw_dir=os.path.join(RAW_DIR, args.date) if args.date else None)
    except Exception:                                            # noqa: BLE001 – the LLM step never fails the run
        log.exception("LLM step failed; the rules' results are used")
    return 0


if __name__ == "__main__":
    sys.exit(main())
