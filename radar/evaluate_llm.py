"""Pilot: Claude Haiku vs. the extraction rules on the 100 labelled postings.

    python -m radar.evaluate_llm run --tag v3              # Messages API, one request per posting
    python -m radar.evaluate_llm run --tag v3 --effort low # the same with adaptive thinking
    python -m radar.evaluate_llm run --tag batch --batch   # through the Message Batches API
    python -m radar.evaluate_llm report --main v3          # write data/eval/report_llm.md

Needs ANTHROPIC_API_KEY and the local job texts (data/eval/sample.json). Answers are
kept in data/eval/llm_results.json (reference numbers, extracted facts, token counts;
no job text) keyed by refnr and a hash of the posting, so a posting is only sent again
when its text changed or with --force.
"""

import argparse
import csv
import json
import os
from collections import Counter

from . import llm
from .evaluate import (EVAL_DIR, GERMAN_LEVELS, LABELS_CSV, PANEL, ROOT, SAMPLE_IDS, SAMPLE_JSON, panel_labels,
                       pct, predictions, prf)

RESULTS_JSON = os.path.join(EVAL_DIR, "llm_results.json")
EXTRA_LABELS_CSV = os.path.join(EVAL_DIR, "labels_extra.csv")
REPORT_MD = os.path.join(EVAL_DIR, "report_llm.md")
EXTRA = ["word", "outlook", "teams"]          # labelled separately (labels_extra.csv)
FOCUS = ["ms_office", "excel", "word", "outlook", "teams"]
MONTHLY = [500, 5_000, 50_000]
RUNS_PER_MONTH = 30


def load():
    with open(SAMPLE_JSON, encoding="utf-8") as f:
        sample = json.load(f)
    with open(SAMPLE_IDS, newline="", encoding="utf-8") as f:
        strata = {r["refnr"]: r["stratum"] for r in csv.DictReader(f)}
    with open(LABELS_CSV, newline="", encoding="utf-8") as f:
        labels = {r["refnr"]: r for r in csv.DictReader(f) if r["refnr"] in strata}
    with open(EXTRA_LABELS_CSV, newline="", encoding="utf-8") as f:
        extra = {r["refnr"]: set(filter(None, r["skills"].split("|"))) for r in csv.DictReader(f)}
    return sample, strata, labels, extra


def load_results(mode):
    if not os.path.exists(RESULTS_JSON):
        return {}
    with open(RESULTS_JSON, encoding="utf-8") as f:
        return json.load(f).get(mode, {})


def save_results(mode, records):
    data = {}
    if os.path.exists(RESULTS_JSON):
        with open(RESULTS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    # The checked result is recomputed from the raw answer when needed (llm.clean), so only the raw one is kept.
    data[mode] = {r: {k: v for k, v in rec.items() if not (rec.get("raw") and k in ("result", "problems"))}
                  for r, rec in sorted(records.items())}
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def run(tag, batch=False, limit=None, force=False, effort=None):
    sample, *_ = load()
    mode = tag
    done = load_results(mode)
    todo = [p for p in sample["postings"][:limit]
            if force or done.get(p["refnr"], {}).get("hash") != llm.text_hash(p) or "error" in done.get(p["refnr"], {})]
    print(f"{len(todo)} postings to send ({len(sample['postings'][:limit]) - len(todo)} unchanged, kept)")
    if not todo:
        return
    api = llm.client()
    if batch:
        batch_id, ids = llm.submit_batch(api, todo, effort=effort)
        print(f"batch {batch_id} submitted; waiting for it to end")
        new = llm.collect_batch(api, batch_id, ids, todo, poll_s=20, timeout_s=24 * 3600)
    else:
        n = [0]

        def progress(ref, rec):
            n[0] += 1
            if n[0] % 10 == 0:
                print(f"  {n[0]}/{len(todo)}")
        new = llm.extract_sync(api, todo, on_done=progress, effort=effort)
    done.update(new)
    save_results(mode, done)
    errors = [r for r, rec in new.items() if "error" in rec]
    print(f"saved {len(new)} answers ({len(errors)} errors) to {os.path.relpath(RESULTS_JSON, ROOT)}")


# --- Report ---------------------------------------------------------------------------

def llm_skills(rec, check_ids=True):
    """Skill ids an LLM answer found; MS Teams comes back as "other". check_ids=False
    re-reads the raw answer without the id check."""
    result = (llm.clean(rec["raw"], check_ids=check_ids)[0] if rec.get("raw") else rec.get("result")) or {}
    out = set()
    for s in result.get("skills", []):
        if s["id"] != "other":
            out.add(s["id"])
        elif "teams" in s["term"].casefold():
            out.add("teams")
    return out


def score(refs, truth, found, skills):
    rows, total = {}, [0, 0, 0]
    for sid in skills:
        tp = sum(sid in truth[r] and sid in found[r] for r in refs)
        fp = sum(sid not in truth[r] and sid in found[r] for r in refs)
        fn = sum(sid in truth[r] and sid not in found[r] for r in refs)
        rows[sid] = (tp, fp, fn)
        total = [total[0] + tp, total[1] + fp, total[2] + fn]
    return rows, tuple(total)


def usage_total(records):
    keys = ["input", "output", "cache_read", "cache_write_5m", "cache_write_1h", "cache_write"]
    return {k: sum((r.get("usage") or {}).get(k, 0) for r in records) for k in keys}


def build(sample, strata, labels, extra, results, batch_results, others=()):
    names = dict(panel_labels()) | {"word": "Word", "outlook": "Outlook", "teams": "MS Teams"}
    postings = sample["postings"]
    rules = predictions(sample)
    # Postings without task/requirement sections were not sent ("skipped"): the rules' answer counts.
    from_rules = lambda r: {"skills": [{"id": s, "term": s, "importance": "must_have"} for s in rules[r]["skills"].split("|") if s],
                            "german_requirement": {v: k for k, v in llm.GERMAN_TO_RULES.items()}.get(rules[r]["german"], "not_stated"),
                            "german_level": "unknown",
                            "pay_hour_min": float(rules[r]["pay_min"]) if rules[r]["pay_min"] else None,
                            "pay_hour_max": float(rules[r]["pay_max"]) if rules[r]["pay_max"] else None,
                            "hours_week_min": None, "hours_week_max": float(rules[r]["hours"]) if rules[r]["hours"] else None}
    # Stored raw answers are checked again, so changes to the validation apply without new API calls.
    results = {r: ({**rec, "result": from_rules(r)} if rec.get("skipped")
                   else {**rec, "result": llm.clean(rec["raw"])[0], "problems": llm.clean(rec["raw"])[1]} if rec.get("raw") else rec)
               for r, rec in results.items()}
    skipped = [r for r, rec in results.items() if rec.get("skipped")]
    refs_all = [p["refnr"] for p in postings if p["refnr"] in labels and p["refnr"] in results and results[p["refnr"]].get("result")]
    rnd = [r for r in refs_all if strata.get(r) == "random"]
    truth = {r: set(filter(None, labels[r]["skills"].split("|"))) | extra.get(r, set()) for r in refs_all}
    by_rules = {r: set(filter(None, rules[r]["skills"].split("|"))) for r in refs_all}
    by_llm = {r: llm_skills(results[r]) for r in refs_all}
    variants = [("LLM (main) + id check", by_llm),
                ("LLM (main), ids as returned", {r: llm_skills(results[r], check_ids=False) for r in refs_all})]
    for name, recs in others:
        variants.append((f"LLM {name}", {r: by_rules[r] if recs.get(r, {}).get("skipped") else llm_skills(recs[r]) if r in recs else set()
                                         for r in refs_all}))
    skills = PANEL + EXTRA

    L = ["# Skill extraction: Claude Haiku 5.5 vs. the rules", "",
         f"Model `{llm.MODEL}`, thinking off, structured JSON output, cached system prompt "
         f"({len(llm.system_prompt()):,} characters: instructions and the {len(llm.SKILL_IDS)} skills). "
         f"Labels: `labels.csv` (14 skills) and `labels_extra.csv` (Word, Outlook, MS Teams), see `LABELS.md`.", "",
         f"Answers: {len(refs_all)} of {len(postings)} postings; {len(skipped)} had no task or requirement section and "
         f"were not sent (the rules' answer counts for them).", "",
         "Versions: **v1** is the first run (prompt written before looking at any answer). After a smoke test on 3 "
         "postings and v1's disagreements, **v2** puts the term before the id in the schema (the id is chosen with the "
         "term already written), counts skill areas from the tasks (reports/KPIs, creditor work, social media content) "
         "and products of listed tools (SAP SuccessFactors). The **id check** then keeps an id only when the term fits "
         "it (the rules' pattern or a word of the skill's label), else moves it to the listed skill that fits or to "
         "\"other\". v2 was tuned on these same postings, so its scores are optimistic, like the rules' (tuned in pass 3 "
         "of `LABELS.md`; their untuned baseline is in `report_baseline.md`). **v3** also sends the untitled intro "
         "(v1/v2 lost requirements that unstructured postings put there); **v3-thinking-low** is v3 with adaptive "
         "thinking at effort `low` instead of thinking off.", ""]

    sent = [llm.model_input(p) for p in postings]
    n_sent = sum(ok for _, ok in sent)
    chars_sent = sum(len(m) for m, ok in sent if ok)
    chars_full = sum(len(p["text"]) + len(p["title"]) for p in postings)
    L += ["## Input", "",
          f"Only the task and requirement sections (plus single lines about pay or hours) go to the model. They were found in "
          f"{n_sent} of {len(postings)} postings; the others go to the rules only. Sent: {chars_sent / chars_full:.0%} of all "
          f"posting text ({chars_sent // max(1, n_sent):,} characters per sent posting, {chars_full // len(postings):,} for a whole posting).", ""]

    for title, refs in (("random 70", rnd), ("all 100", refs_all)):
        rr, (rtp, rfp, rfn) = score(refs, truth, by_rules, skills)
        lr, (ltp, lfp, lfn) = score(refs, truth, by_llm, skills)
        L += [f"## Skills, {title} ({len(refs)} postings)", "",
              "| Skill | In labels | Rules P / R / F1 | LLM (main) + id check P / R / F1 | Rules FP / FN | LLM FP / FN |",
              "|---|---|---|---|---|---|"]
        for sid in skills:
            a, b = rr[sid], lr[sid]
            L.append(f"| {names[sid]} | {a[0] + a[2]} | {' / '.join(prf(*a))} | {' / '.join(prf(*b))} | {a[1]} / {a[2]} | {b[1]} / {b[2]} |")
        L += ["", "| Overall | Skills | P / R / F1 | FP / FN |", "|---|---|---|---|"]
        for name, found in [("Rules", by_rules)] + variants:
            for group, ids in ((f"14 skills (as in the README)", PANEL), (f"all {len(skills)}", skills)):
                _, (tp, fp, fn) = score(refs, truth, found, ids)
                L.append(f"| {name} | {group} | {' / '.join(prf(tp, fp, fn))} | {fp} / {fn} |")
        L.append("")

    # German
    g = [r for r in rnd if labels[r]["german"] in GERMAN_LEVELS]
    lg = {r: llm.german_label(results[r]["result"]) for r in refs_all}
    open_ = lambda v: v in ("none", "plus")
    L += ["## German requirement (random 70)", "",
          "| Check | Rules | LLM |", "|---|---|---|",
          f"| Same category as the label (required / plus / none / implicit) | {pct(sum(labels[r]['german'] == rules[r]['german'] for r in g), len(g))} | {pct(sum(labels[r]['german'] == lg[r] for r in g), len(g))} |",
          f"| Needs German: yes / no | {pct(sum(open_(labels[r]['german']) == open_(rules[r]['german']) for r in g), len(g))} | {pct(sum(open_(labels[r]['german']) == open_(lg[r]) for r in g), len(g))} |"]
    claimed_r = [r for r in refs_all if open_(rules[r]["german"]) and labels[r]["german"] in GERMAN_LEVELS]
    claimed_l = [r for r in refs_all if open_(lg[r]) and labels[r]["german"] in GERMAN_LEVELS]
    L += [f"| Shown as “open without German” and really are (all strata) | {sum(open_(labels[r]['german']) for r in claimed_r)}/{len(claimed_r)} | {sum(open_(labels[r]['german']) for r in claimed_l)}/{len(claimed_l)} |", ""]
    levels = Counter(results[r]["result"]["german_level"] for r in refs_all)
    L += ["German level found by the LLM (all 100): " + ", ".join(f"{k} {v}" for k, v in levels.most_common()), ""]

    # Pay and hours
    has = lambda v: v not in (None, "")
    def same(lmin, lmax, pmin, pmax):
        return abs(float(lmin) - float(pmin)) < 0.01 and abs(float(lmax or lmin) - float(pmax)) < 0.01
    pay_lab = [r for r in refs_all if has(labels[r]["pay_min"])]
    res = {r: results[r]["result"] for r in refs_all}
    L += ["## Hourly pay and hours (all 100)", "", "| Check | Rules | LLM |", "|---|---|---|",
          f"| Labelled hourly rate found | {sum(has(rules[r]['pay_min']) for r in pay_lab)}/{len(pay_lab)} | {sum(res[r]['pay_hour_min'] is not None for r in pay_lab)}/{len(pay_lab)} |",
          f"| … and the amount is right | {sum(has(rules[r]['pay_min']) and same(labels[r]['pay_min'], labels[r]['pay_max'], rules[r]['pay_min'], rules[r]['pay_max']) for r in pay_lab)}/{len(pay_lab)} | {sum(res[r]['pay_hour_min'] is not None and same(labels[r]['pay_min'], labels[r]['pay_max'], res[r]['pay_hour_min'], res[r]['pay_hour_max']) for r in pay_lab)}/{len(pay_lab)} |",
          f"| A rate where the label has none | {sum(has(rules[r]['pay_min']) and not has(labels[r]['pay_min']) for r in refs_all)} | {sum(res[r]['pay_hour_min'] is not None and not has(labels[r]['pay_min']) for r in refs_all)} |"]
    hours_r = [r for r in refs_all if has(rules[r]["hours"])]
    hours_l = [r for r in refs_all if res[r]["hours_week_max"] is not None or res[r]["hours_week_min"] is not None]
    both = [r for r in hours_r if r in hours_l]
    agree = sum(abs(float(rules[r]["hours"]) - (res[r]["hours_week_max"] or res[r]["hours_week_min"])) < 0.5 for r in both)
    L += [f"| Hours per week found (no labels) | {len(hours_r)} | {len(hours_l)} |",
          f"| Same hours when both found them | {agree}/{len(both)} | |", ""]

    # Must vs nice, other
    imp = Counter(s["importance"] for r in refs_all for s in res[r]["skills"])
    other = Counter(s["term"].casefold() for r in refs_all for s in res[r]["skills"] if s["id"] == "other")
    n_sk = sum(len(res[r]["skills"]) for r in refs_all)
    L += ["## What else the LLM returns", "",
          f"- {n_sk} skills in {len(refs_all)} postings: must have {imp['must_have']}, nice to have {imp['nice_to_have']}; "
          f"{sum(other.values())} of them not on our list (\"other\").",
          "- Most frequent \"other\" terms: " + ", ".join(f"{t} ({n})" for t, n in other.most_common(25)), ""]

    # Disagreements in the focus skills
    rows = []
    for r in refs_all:
        for sid in FOCUS:
            t, a, b = sid in truth[r], sid in by_rules[r], sid in by_llm[r]
            if not (t == a == b):
                rows.append(f"| {r} | {names[sid]} | {'yes' if t else '–'} | {'yes' if a else '–'} | {'yes' if b else '–'} |")
    L += ["## Disagreements: MS Office, Excel, Word, Outlook, MS Teams", "",
          "| Posting | Skill | Label | Rules | LLM |", "|---|---|---|---|---|", *rows, ""]

    # Validation
    problems = Counter(p for r in refs_all for p in results[r].get("problems", []))
    stops = Counter(results[r].get("stop_reason") for r in refs_all)
    L += ["## Answers", "",
          f"- Stop reasons: {dict(stops)}; schema problems after the API's own check: {sum(problems.values())}"
          + (f" ({dict(problems.most_common(5))})" if problems else ""),
          f"- Errors: {sum('error' in rec for rec in results.values())}", ""]

    # Cost
    def cost_block(name, recs, batch):
        u = usage_total(recs)
        n = len(postings)                              # every posting, also the ones not sent (they cost nothing)
        if not any(r.get("usage") for r in recs):
            return []
        total = llm.cost(u, batch=batch)
        write = llm.cost({**{k: 0 for k in u}, "cache_write_5m": u["cache_write_5m"], "cache_write_1h": u["cache_write_1h"],
                          "cache_write": u["cache_write"]}, batch=batch)
        per = (total - write) / n
        lines = [f"**{name}** ({n} postings): input {u['input']:,}, cache read {u['cache_read']:,}, cache write {u['cache_write']:,}, "
                 f"output {u['output']:,} tokens → ${total:.4f}; per posting {u['input'] // n:,} input + {u['cache_read'] // n:,} cached + "
                 f"{u['output'] // n:,} output tokens.", "",
                 f"- Per 1,000 postings: **${per * 1000 + write:.2f}** (one cache write per run: ${write:.4f})"]
        for m in MONTHLY:
            lines.append(f"- {m:,} new postings a month (daily runs): **${per * m + write * RUNS_PER_MONTH:.2f}** a month")
        return lines + [""]

    sync_recs = [results[r] for r in refs_all]
    L += ["## Tokens and cost", "", f"Prices for `{llm.MODEL}` (US$ per million tokens): input {llm.PRICE['input']}, output "
          f"{llm.PRICE['output']}, cache read {llm.PRICE['cache_read']}, cache write {llm.PRICE['cache_write_5m']} (5 min) / "
          f"{llm.PRICE['cache_write_1h']} (1 h); the Batch API halves all of them.", ""]
    L += cost_block("Messages API (measured)", sync_recs, batch=False)
    L += cost_block("Batch API, same tokens at batch prices (estimate)", sync_recs, batch=True)
    batch_recs = [rec for rec in batch_results.values() if rec.get("usage")]
    if batch_recs:
        L += cost_block("Batch API (measured, same configuration)", batch_recs, batch=True)
    return "\n".join(L)


def report(main):
    sample, strata, labels, extra = load()
    with open(RESULTS_JSON, encoding="utf-8") as f:
        tags = [t for t in json.load(f) if t != main]
    others = [(t, load_results(t)) for t in tags if not t.startswith("batch")]
    batch_tag = f"batch-{main}"
    batch = load_results(batch_tag) if batch_tag in tags else {}
    text = build(sample, strata, labels, extra, load_results(main), batch, others)
    text = text.replace("(main)", f"({main})")
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    print(f"\nwrote {os.path.relpath(REPORT_MD, ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["run", "report"])
    ap.add_argument("--batch", action="store_true", help="use the Message Batches API")
    ap.add_argument("--limit", type=int, help="only the first N postings")
    ap.add_argument("--force", action="store_true", help="send again even if unchanged")
    ap.add_argument("--tag", default="main", help="name the run's answers are stored under")
    ap.add_argument("--effort", choices=["low", "medium", "high"], help="let the model think at this effort")
    ap.add_argument("--main", default="v3", help="the run the report's tables describe")
    args = ap.parse_args()
    if args.command == "run":
        run(args.tag, batch=args.batch, limit=args.limit, force=args.force, effort=args.effort)
    else:
        report(args.main)


if __name__ == "__main__":
    main()
