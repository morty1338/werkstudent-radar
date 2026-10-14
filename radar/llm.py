"""Skill and requirement extraction with Claude Haiku (pilot; not part of the daily run yet).

A posting is split into sections by its headings (tasks, requirements, nice to have,
benefits, company); only the tasks and requirements ("Deine Aufgaben", "Dein Profil",
"Das bringst du mit", "Von Vorteil" …) go to the model, plus single lines elsewhere
that mention pay or hours. A posting without such sections isn't sent at all: the
rules handle it, which costs nothing. The model answers
in a fixed JSON schema (structured outputs), mapping skills to the ids in
radar/skills.py; anything else comes back as "other" with the posting's own term.
The instructions and the skill list are one cached system prompt, identical for
every posting, so after the first request they are read from the cache at a tenth
of the input price. Every answer is validated again here before it is used.

The API key comes from ANTHROPIC_API_KEY (never stored in the repository).
"""

import hashlib
import json
import re

from .skills import SKILLS

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 1500
# US$ per million tokens for Claude Haiku 5.5, prompts up to 100,000 tokens
# (platform.claude.com/docs/en/models/haiku-5-5/overview, checked 2026-10-14).
PRICE = {"input": 0.10, "output": 0.50, "cache_write_5m": 0.125, "cache_write_1h": 0.20, "cache_read": 0.01}
BATCH_DISCOUNT = 0.5          # Message Batches API: half price on all of the above
MAX_INPUT_CHARS = 8000        # one section is cut here
EFFORT = "low"                # adaptive thinking at low effort: +2 points F1 for +25% cost in the pilot

LEVELS = ["none", "basic", "fluent", "c1_plus", "native", "unknown"]
NEED = ["must_have", "nice_to_have", "not_needed", "not_stated"]
IMPORTANCE = ["must_have", "nice_to_have"]
SKILL_IDS = [sid for sid, _, _, _ in SKILLS]

# --- Sections ---------------------------------------------------------------------------

# Heading keywords per section, German and English, checked in this order (the first
# match wins): specific benefit phrases before tasks ("Das erwartet dich bei uns"),
# tasks before the general "bieten wir" ("Diese Aufgabenvielfalt bieten wir").
SECTION_RULES = [
    ("nice_to_have", r"von vorteil|wünschenswert|nice[- ]to[- ]have|bonus points|ein plus\b|is a plus|pluspunkte"),
    ("benefits", r"das erwartet dich bei uns|darauf kannst du dich freuen|freuen sie sich auf|freu dich auf|deine chance|our offer|unser angebot|what we offer|benefits|perks|deine vorteile|ihre vorteile|diese vorteile|vergütung|why you should apply|why join|working with us|das beste an"),
    ("tasks", r"aufgabe|tätigkeit|was dich erwartet|was sie erwartet|erwartet dich|erwartet sie|das erwartet (?:dich|sie)|es erwartet dich|deine rolle|ihre rolle|your role|tasks|responsibilities|what you(?:'|´|’)?ll (?:do|build)|what you will do|make an impact|your impact|dein impact|job description|stellenbeschreibung|deine mission|key accountabilities|verantwortlichkeiten|was du bei uns bewegst|das machst du|dein job|einsatzbereich|wofür wir"),
    ("requirements", r"profil|anforderung|qualifikation|mitbring|bring\w* (?:du|sie) mit|was dich auszeichnet|was sie auszeichnet|was uns überzeugt|damit überzeugen|win us over|voraussetzung|fähigkeiten|skillset|what you bring|what you(?:'|´|’)?ll bring|what you need|requirements|qualifications|desired skills|your skills|who you are|looking for|was wir uns wünschen|von (?:dir|ihnen) erwarten|schlüssel zum erfolg|kenntnisse|das solltest du|du passt|features, die dich"),
    ("benefits", r"wir bieten|bieten wir|anbieten|was (?:wir )?(?:dir|ihnen) bieten"),
    ("company", r"über uns|wer wir sind|unternehmensbeschreibung|das unternehmen|about us|who we are|about the company|kontakt|ansprechpartner|bewerbung|apply now|zusätzliche informationen|where you belong|we win with inclusion|bring out your best|your location|standort"),
]
_SECTION_RX = [(name, re.compile(rx, re.I)) for name, rx in SECTION_RULES]
SENT_SECTIONS = ("tasks", "requirements", "nice_to_have")   # intro, benefits and company stay out
_PAY_OR_HOURS = re.compile(r"€|\beur\b|euro|stunde|std\b|std\.|hours?\b|vergütung|gehalt|salary|lohn|wochenstunden|h/woche|h/week", re.I)


def _heading(line):
    """The line's text if it looks like a heading (short, no sentence), else None."""
    s = line.strip()
    if not s or s.startswith(("- ", "* ", "• ", "✓")) or "|" in s:
        return None
    plain = re.sub(r"^[#*_\\\s]+|[#*_\\\s:]+$", "", s).strip()
    question = plain.endswith("?") and len(plain.split()) <= 6          # "Was bringst du mit?"
    if not plain or len(plain) > 60 or plain.endswith((".", "!", "?")) and not s.rstrip().endswith(":") and not question:
        return None
    marked = s.startswith("#") or s.startswith("**") or s.rstrip().endswith(":")
    if marked or len(plain.split()) <= 6:
        return plain
    return None


# Headings inside postings that come as one paragraph: put them on a line of their own.
_INLINE_HEADING = re.compile(
    r"(?i)\b(deine aufgaben|ihre aufgaben|dein profil|ihr profil|das bringst du mit|das bringen sie mit|"
    r"was du mitbringst|was sie mitbringen|was bringst du mit\??|was erwartet dich\??|das erwartet dich|"
    r"wir bieten|das bieten wir(?: dir| ihnen)?|was wir (?:dir|ihnen) bieten|deine benefits|your tasks|your profile|"
    r"what you bring|what we offer)\b\s*:?")


def split_sections(text):
    """{section: text} by headings; text before the first known heading is "intro".
    Unknown headings don't start a new section."""
    text = text or ""
    if text.count("\n") < 3:
        text = _INLINE_HEADING.sub(lambda m: f"\n{m.group(0).strip()}\n", text)
    sections = {"intro": []}
    current = "intro"
    for line in text.splitlines():
        head = _heading(line)
        if head:
            name = next((n for n, rx in _SECTION_RX if rx.search(head)), None)
            if name:
                current = name
                sections.setdefault(current, [])
                continue
        sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


def model_input(posting):
    """(user message, True) for one posting, or (None, False) when it has no task or
    requirement section: then the model isn't asked and the rules' result is used."""
    sections = split_sections(posting["text"])
    if not any(s in sections for s in ("tasks", "requirements")):
        return None, False
    parts = [f"Title: {posting['title']}"]
    for name in SENT_SECTIONS:
        if name in sections:
            parts.append(f"## {name.replace('_', ' ').capitalize()}\n{sections[name][:MAX_INPUT_CHARS]}")
    other = [line.strip()[:200] for name, body in sections.items() if name not in SENT_SECTIONS
             for line in body.splitlines() if _PAY_OR_HOURS.search(line)]
    if other:
        parts.append("## Lines about pay or hours from the rest of the posting\n" + "\n".join(other[:8]))
    return "\n\n".join(parts), True


def text_hash(posting):
    return hashlib.sha256(f"{posting['title']}\n{posting['text']}".encode("utf-8")).hexdigest()[:16]


# --- Prompt and schema --------------------------------------------------------------------

def system_prompt():
    skills = "\n".join(f"{sid}: {label}" for sid, label, _, _ in SKILLS)
    return f"""Extract facts from a German or English Werkstudent (working student) job posting: its title, task and requirement sections, sometimes lines about pay or hours. Use only this text.

SKILLS: hard skills, tools, software, programming languages and licences the posting asks for (required or nice to have) or names as tools for the tasks.
- Not: soft skills, fields of study, spoken languages, the employer's own products or clients.
- Only what the text names; never infer a tool from a task ("Bestellanfragen bearbeiten" names no CRM, "Datenanalyse" no Excel). But skill areas on the list count when the job involves them: reports/dashboards/KPIs = controlling, creditor or booking work = accounting, social media content = social_media.
- term: the posting's own words, at most 5 words. Then id: the one id from the list that means exactly that term, else "other".
- "MS Office (Excel, PowerPoint)" = ms_office + each named program. Plain "MS Office", "Office-Anwendungen", "M365" = ms_office only. Products of a listed tool count as it (SAP SuccessFactors = sap). "EDV-Kenntnisse" is no skill.
- importance: "nice_to_have" if marked optional (von Vorteil, wünschenswert, idealerweise, gerne, ein Plus, nice to have, a plus, preferred), else "must_have".
- Each id once; each "other" term once.

LANGUAGES (German, English): level "basic" (Grundkenntnisse, A1–B1), "fluent" (gut, fließend, B2), "c1_plus" (sehr gut, verhandlungssicher, C1, C2, business fluent), "native" (Muttersprache), "none" (not needed), "unknown" (no level). requirement "must_have", "nice_to_have", "not_needed" (explicitly), "not_stated" (not mentioned).

PAY: pay_hour_min/max only for a stated hourly rate in euros (one rate: both equal); null for monthly or yearly pay or "Mindestlohn" without a number.
HOURS: hours_week_min/max only when stated per week during the semester ("bis zu 20 Std./Woche" = null, 20); null if only per month or year.

SKILL LIST (id: label)
{skills}
"""


_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["skills", "german_level", "german_requirement", "english_level", "english_requirement",
                 "pay_hour_min", "pay_hour_max", "hours_week_min", "hours_week_max"],
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["term", "id", "importance"],
                "properties": {
                    "term": {"type": "string"},
                    "id": {"type": "string", "enum": SKILL_IDS + ["other"]},
                    "importance": {"type": "string", "enum": IMPORTANCE},
                },
            },
        },
        "german_level": {"type": "string", "enum": LEVELS},
        "german_requirement": {"type": "string", "enum": NEED},
        "english_level": {"type": "string", "enum": LEVELS},
        "english_requirement": {"type": "string", "enum": NEED},
        "pay_hour_min": _NUM,
        "pay_hour_max": _NUM,
        "hours_week_min": _NUM,
        "hours_week_max": _NUM,
    },
}


def request_params(user_text, *, cache_ttl=None, effort=EFFORT):
    """Messages API parameters for one posting. cache_ttl "1h" for batches (they can run
    longer than the default 5 minutes). effort None turns thinking off (its tokens are
    billed as output); with an effort ("low", …) the model may think first."""
    cache = {"type": "ephemeral", **({"ttl": cache_ttl} if cache_ttl else {})}
    output = {"format": {"type": "json_schema", "schema": SCHEMA}}
    if effort:
        output["effort"] = effort
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS if not effort else 4 * MAX_TOKENS,
        "system": [{"type": "text", "text": system_prompt(), "cache_control": cache}],
        "thinking": {"type": "adaptive"} if effort else {"type": "disabled"},
        "output_config": output,
        "messages": [{"role": "user", "content": user_text}],
    }


# --- Validation ---------------------------------------------------------------------------

# The rules' own patterns, used to check the id the model picked for a term.
_SKILL_RX = {sid: re.compile("|".join(f"(?:{p})" for p in pats), re.I) for sid, _, _, pats in SKILLS}


_LABEL_WORDS = {sid: {w for w in re.findall(r"[a-zäöüß0-9+#]{3,}", label.casefold()) if w not in ("any", "other")}
                for sid, label, _, _ in SKILLS}


def check_id(sid, term):
    """The id to keep for a (listed id, term) pair. The term has to fit the id (the rules'
    pattern or a word of its label); else it is another listed skill when exactly one
    fits ("ERP-System" given as ms_office), or "other" ("Auswertungen" given as excel).
    None when the term is empty."""
    if not term:
        return None
    t = term.casefold()
    if _SKILL_RX[sid].search(term) or any(w in t for w in _LABEL_WORDS[sid]):
        return sid
    fits = [o for o, rx in _SKILL_RX.items() if o != sid and rx.search(term)]
    return fits[0] if len(fits) == 1 else "other"

def _num(v, lo, hi):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else None


def _range(a, b, lo, hi):
    a, b = _num(a, lo, hi), _num(b, lo, hi)
    if a is not None and b is not None and a > b:
        a, b = b, a
    if lo == 5 and (a is None) != (b is None):     # pay: a single stated rate is both bounds
        a = b = a if a is not None else b
    return a, b


def clean(raw, check_ids=True):
    """Check an answer against the schema and sane ranges. Returns (result, problems);
    result is None when the answer is unusable. check_ids=False keeps the model's ids
    as they are (to measure what the check adds)."""
    problems = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, ["not JSON"]
    if not isinstance(raw, dict) or not isinstance(raw.get("skills"), list):
        return None, ["no skills list"]
    skills, seen = [], {}
    for s in raw["skills"]:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            problems.append(f"no skill {s!r:.60}")
            continue
        term = str(s.get("term") or "").strip()[:80]
        if s["id"] not in SKILL_IDS and s["id"] != "other":
            problems.append(f"unknown id {s['id']!r:.30}")
            fits = [o for o, rx in _SKILL_RX.items() if term and rx.search(term)]
            s = {**s, "id": fits[0] if len(fits) == 1 else "other"}
        imp = s.get("importance") if s.get("importance") in IMPORTANCE else "must_have"
        if s["id"] != "other" and check_ids:
            sid = check_id(s["id"], term)
            if sid is None:
                problems.append(f"{s['id']} without a term")
                continue
            if sid != s["id"]:
                problems.append(f"{s['id']} -> {sid} for {term!r:.40}")
                s = {**s, "id": sid}
        key = s["id"] if s["id"] != "other" else "other:" + term.casefold()
        if s["id"] == "other" and not term:
            problems.append("other without a term")
            continue
        if key in seen:                                      # once per skill; must_have wins
            if imp == "must_have":
                skills[seen[key]]["importance"] = "must_have"
            continue
        seen[key] = len(skills)
        skills.append({"id": s["id"], "term": term, "importance": imp})
    out = {"skills": skills}
    for lang in ("german", "english"):
        level, need = raw.get(f"{lang}_level"), raw.get(f"{lang}_requirement")
        if level not in LEVELS:
            problems.append(f"{lang}_level {level!r}")
            level = "unknown"
        if need not in NEED:
            problems.append(f"{lang}_requirement {need!r}")
            need = "not_stated"
        out[f"{lang}_level"], out[f"{lang}_requirement"] = level, need
    out["pay_hour_min"], out["pay_hour_max"] = _range(raw.get("pay_hour_min"), raw.get("pay_hour_max"), 5, 100)
    out["hours_week_min"], out["hours_week_max"] = _range(raw.get("hours_week_min"), raw.get("hours_week_max"), 1, 40)
    return out, problems


GERMAN_TO_RULES = {"must_have": "required", "nice_to_have": "plus", "not_needed": "none", "not_stated": "implicit"}


def german_label(result, lang=None):
    """The rules' German categories (required / plus / none / implicit) for a result. As in
    the rules, a posting in English that never mentions German counts as "none"."""
    label = GERMAN_TO_RULES[result["german_requirement"]]
    return "none" if label == "implicit" and lang == "en" else label


# --- Cost ---------------------------------------------------------------------------------

def usage_dict(usage):
    """Token counts from an API usage object (or a dict)."""
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    cc = get("cache_creation")
    get_cc = (lambda k: (cc or {}).get(k)) if isinstance(cc, dict) or cc is None else (lambda k: getattr(cc, k, None))
    return {
        "input": get("input_tokens") or 0,
        "output": get("output_tokens") or 0,
        "cache_read": get("cache_read_input_tokens") or 0,
        "cache_write_5m": get_cc("ephemeral_5m_input_tokens") or 0,
        "cache_write_1h": get_cc("ephemeral_1h_input_tokens") or 0,
        "cache_write": get("cache_creation_input_tokens") or 0,
    }


def cost(u, *, batch=False):
    """US$ for token counts from usage_dict (summed or single)."""
    w5 = u["cache_write_5m"] or (u["cache_write"] - u["cache_write_1h"])
    usd = (u["input"] * PRICE["input"] + u["output"] * PRICE["output"] + u["cache_read"] * PRICE["cache_read"]
           + w5 * PRICE["cache_write_5m"] + u["cache_write_1h"] * PRICE["cache_write_1h"]) / 1e6
    return usd * (BATCH_DISCOUNT if batch else 1)


# --- Calling the API ----------------------------------------------------------------------

def client(max_retries=4):
    import anthropic                     # only needed when the model is actually called
    return anthropic.Anthropic(max_retries=max_retries)


def _answer(message):
    """(raw JSON text, stop_reason) from a Messages API response."""
    text = next((b.text for b in message.content if getattr(b, "type", "") == "text"), "")
    return text, message.stop_reason


def extract_sync(api, postings, workers=4, on_done=None, effort=EFFORT):
    """One request per posting with the Messages API. The first runs alone so it writes
    the prompt cache; the rest read it. Returns {refnr: record}."""
    from concurrent.futures import ThreadPoolExecutor

    def one(p):
        msg, found = model_input(p)
        if not found:
            return p["refnr"], {"hash": text_hash(p), "mode": "rules", "skipped": True}
        try:
            m = api.messages.create(**request_params(msg, effort=effort))
        except Exception as e:                       # noqa: BLE001 – one failure must not stop the rest
            return p["refnr"], {"hash": text_hash(p), "error": f"{type(e).__name__}: {str(e)[:200]}"}
        raw, stop = _answer(m)
        result, problems = clean(raw)
        rec = {"hash": text_hash(p), "mode": "sync", "stop_reason": stop, "result": result,
               "problems": problems, "usage": usage_dict(m.usage), "raw": raw}
        if on_done:
            on_done(p["refnr"], rec)
        return p["refnr"], rec

    out = {}
    if postings:
        ref, rec = one(postings[0])
        out[ref] = rec
    with ThreadPoolExecutor(workers) as pool:
        out.update(pool.map(one, postings[1:]))
    return out


def batch_custom_id(refnr):
    """Batch custom_id: ^[a-zA-Z0-9_-]{1,64}$ (refnrs may hold other characters)."""
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", refnr)[:48]
    return f"{cid}-{hashlib.sha1(refnr.encode()).hexdigest()[:8]}"


def submit_batch(api, postings, effort=EFFORT):
    """Create a Message Batch (half price, 1-hour prompt cache). Returns (batch id, {custom_id: refnr})."""
    postings = [p for p in postings if model_input(p)[1]]
    ids = {batch_custom_id(p["refnr"]): p["refnr"] for p in postings}
    requests = [{"custom_id": batch_custom_id(p["refnr"]), "params": request_params(model_input(p)[0], cache_ttl="1h", effort=effort)}
                for p in postings]
    batch = api.messages.batches.create(requests=requests)
    return batch.id, ids


def collect_batch(api, batch_id, ids, postings, poll_s=30, timeout_s=3600, sleep=None):
    """Wait for a batch to end (or give up after timeout_s) and return {refnr: record};
    postings that errored or expired get an "error" record."""
    import time
    sleep = sleep or time.sleep
    by_ref = {p["refnr"]: p for p in postings}
    waited = 0
    while True:
        b = api.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        if waited >= timeout_s:
            return None                              # not done: the caller falls back to the rules
        sleep(poll_s)
        waited += poll_s
    out = {}
    for r in api.messages.batches.results(batch_id):
        ref = ids.get(r.custom_id)
        if ref is None:
            continue
        rec = {"hash": text_hash(by_ref[ref]), "mode": "batch"}
        if r.result.type == "succeeded":
            raw, stop = _answer(r.result.message)
            result, problems = clean(raw)
            rec.update(stop_reason=stop, result=result, problems=problems, usage=usage_dict(r.result.message.usage), raw=raw)
        else:
            rec["error"] = r.result.type
        out[ref] = rec
    return out
