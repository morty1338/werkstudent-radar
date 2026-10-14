"""Kurs-Radar, daily step 2: texts, career-changer phrases and skills of the postings
that radar.kurs found (data/kurs/postings.csv).

Usage:
    python -m radar.kurs_extract              # after radar.kurs in the daily run
    python -m radar.kurs_extract --dry-run    # what would be fetched and sent; nothing written

Every active posting is read once: its text is fetched from the Bundesagentur (at most
KURS_MAX_FETCH per run, newest first; texts stay local in data/raw/<date>/kurs_details.jsonl.gz),
checked for phrases that welcome career changers (`career_changer`) and AI requirements
(`ai_in_requirements`), and tagged with the rules' skills (radar.extract.find_skills). Only
these facts are kept, in data/kurs/features.csv, never the text.

Then the same Claude Haiku extraction as the Werkstudent postings (radar/llm.py: must have /
nice to have, evidence quotes, structured output) for postings without a result for their
text, as one Message Batch (half price, cached prompt). The run doesn't wait by default
(KURS_LLM_WAIT_MINUTES=0): the batch is collected by the next run (data/kurs/llm_pending.json)
and until then the rules' skills count. Results in data/kurs/llm.csv (same columns as
data/history/llm.csv), runs in data/kurs/llm_runs.csv. The answers with their evidence quotes
stay local (data/raw/<date>/kurs_llm.jsonl.gz, git-ignored).

Without ANTHROPIC_API_KEY, or when an API fails, the step still succeeds (exit 0).
Budget: KURS_LLM_MAX_POSTINGS, KURS_LLM_MAX_USD (estimate per posting as in extract_llm).
"""

import argparse
import glob
import gzip
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from . import llm
from .enrich import WORKERS, fetch_details, read_jsonl_gz
from .extract import find_skills
from .extract_llm import (EST_USD_PER_POSTING, SPEND_LIMIT_NOTE, collect_pending, read_csv, store_row,
                          STORE_COLUMNS, write_csv)
from .kurs import KURS_DIR, RAW_DIR

FEATURES_CSV = os.path.join(KURS_DIR, "features.csv")
STORE_CSV = os.path.join(KURS_DIR, "llm.csv")
RUNS_CSV = os.path.join(KURS_DIR, "llm_runs.csv")
PENDING_JSON = os.path.join(KURS_DIR, "llm_pending.json")
POSTINGS_CSV = os.path.join(KURS_DIR, "postings.csv")

FEATURE_COLUMNS = ["refnr", "text_hash", "checked", "status", "version", "qe_text", "qe_kind", "ai_text", "rules_skills"]
# Raise when the phrases or the AI terms change: postings are checked again (their texts are fetched again).
DETECT_VERSION = "2"
RUN_COLUMNS = ["date", "run_at", "trigger", "active", "fetched_texts", "no_text", "candidates", "sent", "done",
               "failed", "pending", "over_budget", "input_tokens", "cache_read_tokens", "output_tokens", "cost_usd"]
DEFAULTS = {"KURS_MAX_FETCH": 600, "KURS_LLM_MAX_POSTINGS": 1000, "KURS_LLM_MAX_USD": 0.5,
            "KURS_LLM_WAIT_MINUTES": 0, "KURS_LLM_POLL_SECONDS": 30}
NO_TEXT_RETRY_DAYS = 3     # a posting whose text couldn't be fetched is tried again after this many days

log = logging.getLogger("kurs_extract")


# --- Detection --------------------------------------------------------------------------------

_OPT = (r"(?:(?:abgeschlossene|kaufmännische|einschlägige|passende|entsprechende|fachliche|spezifische|besondere|vorherige)"
        r"[nr]?\s+)?")
_NOT_NEEDED = r"(?:ist\s+|sind\s+)?(?:nicht\s+(?:zwingend\s+)?(?:erforderlich|notwendig|nötig|vorausgesetzt|voraussetzung)|kein\s+muss)"
# (kind, pattern). Kinds are stored, the matched words are not (they are job text).
CAREER_CHANGER = [
    ("quereinstieg", re.compile(r"quer-?\s?einst(?:ei|ie)g\w*", re.I)),
    ("ohne_ausbildung", re.compile(rf"\bohne\s+{_OPT}(?:berufs)?ausbildung|\bausbildung\s+{_NOT_NEEDED}", re.I)),
    ("ohne_erfahrung", re.compile(
        rf"\b(?:keine|ohne)\s+{_OPT}(?:berufs|vor|branchen)?erfahrung|\b(?:keine|ohne)\s+(?:vorkenntnisse|vorwissen)"
        rf"|\b(?:berufs)?erfahrung\s+{_NOT_NEEDED}|\bvorkenntnisse\s+{_NOT_NEEDED}", re.I)),
    # About the applicant, not the audience ("SEO für fachfremde Teams erklären").
    ("branchenfremd", re.compile(r"\bgerne?\s+(?:auch\s+)?(?:branchen|fach)fremd"
                                 r"|\bauch\s+(?:branchen|fach)fremd\w*\b(?!\s+\w)"
                                 r"|(?:branchen|fach)fremde[nr]?\s+(?:bewerb|kandidat|interessent|quer|personen)"
                                 r"|auch\s+aus\s+anderen\s+(?:branchen|berufen|berufsfeldern)", re.I)),
    ("english", re.compile(r"career[- ]?(?:changer|switcher)s?|no\s+(?:prior\s+|previous\s+)?experience\s+(?:is\s+)?"
                           r"(?:required|needed|necessary)|without\s+(?:prior\s+|previous\s+)?experience", re.I)),
]
# "keine Quereinsteiger", "Quereinstieg nicht möglich", "Quereinsteiger sind leider ausgeschlossen".
_NEG_BEFORE = re.compile(r"\bkein(?:e|en)?\s+$", re.I)
_NEG_AFTER = re.compile(r"^\w*\s+(?:sind\s+|ist\s+)?(?:leider\s+)?(?:nicht\b|ausgeschlossen)", re.I)

# AI as a requirement or task, in the task/requirement sections only (an AI company's
# "about us" doesn't count). KI and AI only in capitals.
AI_TERMS = re.compile(r"(?-i:\b(?:KI|AI)\b)|künstliche[rn]?\s+intelligenz|artificial intelligence|chatgpt|copilot"
                      r"|\bgpt\b|\bllms?\b|large language|generative[nr]?\s+(?:ki|ai)|\bgenai\b|prompt"
                      r"|machine[- ]learning|claude|gemini|midjourney", re.I)
AI_SKILLS = {"genai", "ml", "deep_learning", "nlp"}


def career_changer(title, text):
    """The kind of the first phrase that welcomes career changers, or None."""
    full = f"{title or ''}\n{text or ''}"
    for kind, rx in CAREER_CHANGER:
        for m in rx.finditer(full):
            if kind == "quereinstieg" and (_NEG_BEFORE.search(full[max(0, m.start() - 20):m.start()])
                                           or _NEG_AFTER.search(full[m.start():m.end() + 40])):
                continue
            return kind
    return None


def ai_in_requirements(title, text):
    """True when the task or requirement sections (or the title) ask for AI."""
    sections = llm.split_sections(text or "")
    parts = [title or ""] + [sections[s] for s in ("tasks", "requirements", "nice_to_have") if s in sections]
    return bool(AI_TERMS.search("\n".join(parts)))


def features_row(posting, day):
    title, text = posting["title"], posting["text"]
    kind = career_changer(title, text)
    return {"refnr": posting["refnr"], "text_hash": llm.text_hash(posting), "checked": day, "status": "ok",
            "version": DETECT_VERSION,
            "qe_text": int(bool(kind)), "qe_kind": kind or "", "ai_text": int(ai_in_requirements(title, text)),
            "rules_skills": "|".join(find_skills(f"{title}\n{text}"))}


# --- Inputs -----------------------------------------------------------------------------------

def setting(name, cast):
    raw = os.environ.get(name, "").strip()
    try:
        return cast(raw) if raw else DEFAULTS[name]
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, DEFAULTS[name])
        return DEFAULTS[name]


def active_postings(path=POSTINGS_CSV):
    rows = read_csv(path)
    if not rows:
        return None, {}
    day = max(r["last_seen"] for r in rows)
    return day, {r["refnr"]: r for r in rows if r["last_seen"] == day}


def local_texts(refs, raw_dir=RAW_DIR):
    """Texts already fetched on this machine (data/raw/*/kurs_details.jsonl.gz)."""
    texts = {}
    for path in sorted(glob.glob(os.path.join(raw_dir, "*", "kurs_details.jsonl.gz"))):
        for rec in read_jsonl_gz(path):
            ref = rec.get("referenznummer")
            if ref in refs and rec.get("stellenangebotsBeschreibung"):
                texts[ref] = rec["stellenangebotsBeschreibung"]
    return texts


def todo(active, features, store, pending_refs, day):
    """Active postings that still need a text check or an LLM result, newest first."""
    retry = (date.fromisoformat(day) - timedelta(days=NO_TEXT_RETRY_DAYS)).isoformat()
    out = []
    for ref, r in active.items():
        f = features.get(ref)
        if ref in pending_refs or (f and f["status"] == "no_text" and f["checked"] > retry):
            continue
        if (not f or f.get("version") != DETECT_VERSION
                or (store.get(ref) or {}).get("text_hash") != f["text_hash"]):
            out.append(ref)
    return sorted(out, key=lambda ref: (active[ref]["first_seen"], ref), reverse=True)


# --- The run ----------------------------------------------------------------------------------

def save_answers(records, postings, day, raw_dir=RAW_DIR):
    """The model's raw answers (with evidence quotes) and the text sent, local only."""
    out = os.path.join(raw_dir, day)
    os.makedirs(out, exist_ok=True)
    with gzip.open(os.path.join(out, "kurs_llm.jsonl.gz"), "at", encoding="utf-8") as f:
        for ref, rec in records.items():
            p = postings.get(ref)
            f.write(json.dumps({"refnr": ref, "hash": rec.get("hash"), "raw": rec.get("raw"), "error": rec.get("error"),
                                "sent": llm.model_input(p, True)[0] if p else None}, ensure_ascii=False) + "\n")


def run(dry_run=False, kurs_dir=KURS_DIR, raw_dir=RAW_DIR, fetch=fetch_details, api=None, sleep=time.sleep, now=None):
    paths = {k: os.path.join(kurs_dir, f) for k, f in
             (("features", "features.csv"), ("store", "llm.csv"), ("runs", "llm_runs.csv"),
              ("pending", "llm_pending.json"), ("postings", "postings.csv"))}
    day, active = active_postings(paths["postings"])
    if not active:
        log.info("no Kurs-Radar postings yet; nothing to do")
        return None
    features = {r["refnr"]: r for r in read_csv(paths["features"])}
    store = {r["refnr"]: r for r in read_csv(paths["store"])}
    pending = json.load(open(paths["pending"])) if os.path.exists(paths["pending"]) else None
    pending_refs = {v["refnr"] for v in (pending or {}).get("requests", {}).values()}

    refs = todo(active, features, store, pending_refs, day)
    texts = local_texts(set(refs), raw_dir)
    missing = [r for r in refs if r not in texts][:setting("KURS_MAX_FETCH", int)]
    stats = Counter(active=len(active), fetched_texts=0, no_text=0)
    log.info("%s: %d active postings, %d need a text check or skills, %d texts here, %d to fetch",
             day, len(active), len(refs), len(texts), len(missing))
    if dry_run:
        return stats

    if missing:
        os.makedirs(os.path.join(raw_dir, day), exist_ok=True)
        try:
            got, errors = fetch(missing, os.path.join(raw_dir, day, "kurs_details.jsonl.gz"), WORKERS)
        except Exception as e:                                   # noqa: BLE001 – the BA API failing must not stop the step
            log.warning("fetching %d texts failed (%s); they wait for the next run", len(missing), type(e).__name__)
            got = {}
        for ref, rec in got.items():
            if rec and rec.get("stellenangebotsBeschreibung"):
                texts[ref] = rec["stellenangebotsBeschreibung"]
                stats["fetched_texts"] += 1
            elif features.get(ref, {}).get("status") != "ok":
                # Taken down, empty or a failed request: try again in a few days.
                features[ref] = {"refnr": ref, "text_hash": "", "checked": day, "status": "no_text", "version": DETECT_VERSION}
                stats["no_text"] += 1

    postings = {ref: {"refnr": ref, "title": active[ref]["title"], "text": t} for ref, t in texts.items() if ref in active}
    for ref, p in postings.items():
        f = features.get(ref, {})
        if f.get("text_hash") != llm.text_hash(p) or f.get("version") != DETECT_VERSION:
            features[ref] = features_row(p, day)

    # The LLM: postings with a text here whose text has no result yet.
    to_send = [postings[ref] for ref in refs if ref in postings
               and (store.get(ref) or {}).get("text_hash") != llm.text_hash(postings[ref])]
    budget = min(setting("KURS_LLM_MAX_POSTINGS", int), int(setting("KURS_LLM_MAX_USD", float) / EST_USD_PER_POSTING))
    stats.update(candidates=len(to_send), over_budget=max(0, len(to_send) - budget))
    to_send = to_send[:budget]
    records, usage = {}, Counter()
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        log.info("ANTHROPIC_API_KEY not set: nothing sent, the rules' skills are used")
    else:
        try:
            api = api or llm.client()
            if pending:
                got, pending = collect_pending(api, pending, postings)
                records.update(got)
                log.info("earlier batch: %s", f"{len(got)} results collected" if got else "still running" if pending else "given up")
            if to_send and not pending:
                batch_id, ids = llm.submit_batch(api, to_send, full_text=True)
                stats["sent"] = len(to_send)
                wait_s = setting("KURS_LLM_WAIT_MINUTES", float) * 60
                log.info("batch %s: %d postings sent; waiting up to %.0f minutes", batch_id, len(to_send), wait_s / 60)
                got = llm.collect_batch(api, batch_id, ids, to_send, poll_s=setting("KURS_LLM_POLL_SECONDS", float),
                                        timeout_s=wait_s, sleep=sleep)
                if got is None:
                    pending = {"id": batch_id, "submitted": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
                               "requests": {cid: {"refnr": ref, "hash": llm.text_hash(postings[ref])} for cid, ref in ids.items()}}
                    log.info("batch not done yet: the next run collects it; the rules' skills count until then")
                else:
                    records.update(got)
        except Exception as e:                                   # noqa: BLE001 – never fail the pipeline
            log.warning("LLM part skipped after an API error (%s); the rules' skills are used", type(e).__name__)

    for ref, rec in records.items():
        usage.update(rec.get("usage") or {})
        if "error" in rec or not rec.get("result"):
            stats["failed"] += 1
            continue
        row = store_row(postings.get(ref) or {"refnr": ref, "title": "", "text": ""}, rec["result"], day)
        row["text_hash"] = rec["hash"]
        store[ref] = row
        stats["done"] += 1
    if records:
        save_answers(records, postings, day, raw_dir)
    stats["pending"] = len((pending or {}).get("requests", {}))

    write_csv(paths["features"], FEATURE_COLUMNS, sorted(features.values(), key=lambda r: r["refnr"]))
    write_csv(paths["store"], STORE_COLUMNS, sorted(store.values(), key=lambda r: r["refnr"]))
    if pending:
        with open(paths["pending"], "w", encoding="utf-8") as f:
            json.dump(pending, f, indent=1)
    elif os.path.exists(paths["pending"]):
        os.remove(paths["pending"])
    zero = {k: 0 for k in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h", "cache_write")}
    row = {"date": day, "run_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
           "trigger": os.environ.get("GITHUB_EVENT_NAME") or "local",
           **{k: stats.get(k, 0) for k in RUN_COLUMNS[3:12]},
           "input_tokens": usage["input"], "cache_read_tokens": usage["cache_read"], "output_tokens": usage["output"],
           "cost_usd": round(llm.cost({**zero, **usage}, batch=True), 4)}
    write_csv(paths["runs"], RUN_COLUMNS, read_csv(paths["runs"]) + [row])
    report(row)
    return row


def report(r):
    line = (f"{r['date']}: {r['active']} active, {r['fetched_texts']} texts fetched ({r['no_text']} without text), "
            f"{r['candidates']} for the LLM, {r['sent']} sent, {r['done']} results stored, {r['failed']} failed, "
            f"{r['pending']} pending" + (f", {r['over_budget']} over budget (next run)" if r["over_budget"] else "")
            + f"; cost ${r['cost_usd']:.4f}")
    log.info(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Kurs-Radar texts and skills\n\n- {line}\n- {SPEND_LIMIT_NOTE}\n\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="show what would be fetched; nothing written or sent")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        run(dry_run=args.dry_run)
    except Exception:                                            # noqa: BLE001 – this step never fails the run
        log.exception("Kurs-Radar extraction failed; the rules' skills and yesterday's results stand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
