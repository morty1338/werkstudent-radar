"""The questions the MCP server can answer, as plain functions over a radar_data.Dataset.

Same rules as the website (docs/assets/app.js, match.js, gap.js), so the answers
match the page:

- Filters: field, city, and "no German needed" (German level none or plus).
- Pay: hourly rates, each role (company + title) counted once, or once per city
  when a city is chosen. A median is reported as reliable only with at least 10
  rates from at least 5 employers and a 95% confidence interval (bootstrap, same
  code as the pipeline) no wider than a quarter of the median.
- Skills fit a posting when you lack at most a quarter (rounded down) of the
  skills it lists; the next skills to learn are picked greedily, each one making
  the most extra postings fit.

Every function returns a small JSON-ready dict. No job texts exist in the data.
"""

import difflib
import math
import re
import sys
import unicodedata

import radar_data as rd

sys.path.insert(0, rd.ROOT)
from radar.stats import bootstrap_median_ci, quantile  # noqa: E402  (shared with the pipeline)

SITE = "https://morty1338.github.io/werkstudent-radar/"
GERMAN_OK = ("none", "plus")       # "no German needed": not asked for, or only a plus
MAX_LIMIT = 50

# English / spelling variants of German city names.
CITY_ALIASES = {
    "munich": "München", "muenchen": "München", "cologne": "Köln", "koeln": "Köln",
    "nuremberg": "Nürnberg", "nuernberg": "Nürnberg", "frankfurt": "Frankfurt am Main",
    "hanover": "Hannover", "dusseldorf": "Düsseldorf", "duesseldorf": "Düsseldorf",
    "brunswick": "Braunschweig", "wuerzburg": "Würzburg", "tubingen": "Tübingen",
    "tuebingen": "Tübingen", "goettingen": "Göttingen", "lubeck": "Lübeck", "luebeck": "Lübeck",
}


class UnknownValue(ValueError):
    """A city, field or skill that isn't in the data, with the closest names that are."""

    def __init__(self, kind, name, suggestions, hint=""):
        self.kind, self.name, self.suggestions = kind, name, suggestions
        msg = f"unknown {kind} {name!r}"
        if suggestions:
            msg += ". Did you mean: " + ", ".join(suggestions) + "?"
        elif hint:
            msg += f". {hint}"
        super().__init__(msg)


def fold(s):
    """Lowercase, umlauts written out, accents and punctuation removed: 'Power BI' -> 'powerbi'."""
    s = (s or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9+#]", "", s)


def plain(s):
    """Like fold, but umlauts lose their dots: 'Köln' and 'Koln' -> 'koln'."""
    s = unicodedata.normalize("NFKD", (s or "").lower().replace("ß", "ss")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9+#]", "", s)


# --- Resolving names ---------------------------------------------------------------------------

def suggest(name, names, n=3):
    """Up to n of `names` that look like `name`: close spellings first, then names containing it."""
    key = fold(name)
    by_key = {}
    for x in names:
        by_key.setdefault(fold(x), x)
    hits = difflib.get_close_matches(key, list(by_key), n=n, cutoff=0.75)
    if len(key) >= 3:
        hits += [k for k in by_key if k not in hits and (k.startswith(key) or key in k)]
    return list(dict.fromkeys(by_key[k] for k in hits))[:n]


def resolve_field(d, name):
    """Field index for an id ('it') or label ('IT & Software'); None for no filter."""
    if not name:
        return None
    key = fold(name)
    for i, fid in enumerate(d.fields):
        if key in (fold(fid), fold(d.field_labels.get(fid, ""))):
            return i
    # first word of a label: "Marketing" -> Marketing & Communication, "Data" -> Data & Analytics
    hits = [i for i, fid in enumerate(d.fields) if fold(re.split(r"[ &/]", d.field_labels.get(fid, ""))[0]) == key]
    if len(hits) == 1:
        return hits[0]
    labels = [d.field_labels.get(f, f) for f in d.fields]
    by_id = dict(zip(d.fields, labels))
    close = list(dict.fromkeys(by_id.get(x, x) for x in suggest(name, labels + list(d.fields))))
    raise UnknownValue("field", name, close,
                       "Fields are: " + ", ".join(f"{f} ({lab})" for f, lab in zip(d.fields, labels)))


def resolve_city(d, name):
    """City index for a place name ('Berlin', 'Munich', 'Muenchen'); None for no filter."""
    if not name:
        return None
    key = fold(name)
    target = fold(CITY_ALIASES.get(key, name))
    for i, city in enumerate(d.cities):
        if fold(city) == target or plain(city) == plain(name):
            return i
    # checker.json lists cities by postings, most first
    raise UnknownValue("city", name, suggest(name, d.cities),
                       "No Werkstudent postings there today; most are in " + ", ".join(d.cities[:5]) + ".")


def resolve_skill(d, name):
    """Skill index for an id ('powerbi') or label ('Power BI')."""
    key = fold(name)
    for i, s in enumerate(d.skills):
        if key in (fold(s["id"]), fold(s["label"])) or plain(name) == plain(s["label"]):
            return i
    raise UnknownValue("skill", name, suggest(name, [s["label"] for s in d.skills]))


# --- Shared pieces -----------------------------------------------------------------------------

def pool(d, field=None, city=None, no_german=False):
    """Indices of the postings that pass the filters (already resolved)."""
    german_ok = {d.checker["german"].index(g) for g in GERMAN_OK}
    return [i for i, j in enumerate(d.jobs)
            if (field is None or j[rd.CAT] == field)
            and (city is None or j[rd.CITY] == city)
            and (not no_german or j[rd.DE] in german_ok)]


def filters_used(d, field=None, city=None, no_german=False, skill=None):
    out = {}
    if field is not None:
        out["field"] = d.field_labels.get(d.fields[field], d.fields[field])
    if city is not None:
        out["city"] = d.cities[city]
    if skill is not None:
        out["skill"] = d.skills[skill]["label"]
    if no_german:
        out["no_german"] = True
    return out


def pay_summary(d, idx, per_city=False):
    """Pay figures for postings `idx`, with the site's reliability rule."""
    flag = rd.ROLE_CITY if per_city else rd.ROLE
    values, employers = [], set()
    for i in idx:
        j = d.jobs[i]
        if j[rd.PAY] is None or not j[flag]:
            continue
        values.append(j[rd.PAY])
        employers.add(j[rd.COMPANY])
    values.sort()
    min_n = d.checker.get("min_pay_sample", 10)
    out = {"rates": len(values), "employers": len(employers)}
    if not idx:
        return out | {"reliable": False, "note": "No postings match these filters today."}
    if not values:
        return out | {"reliable": False, "note": "None of these postings states an hourly rate."}
    median = quantile(values, 0.5)
    out |= {"median_eur_per_hour": round(median, 2), "middle_half": [round(quantile(values, 0.25), 2), round(quantile(values, 0.75), 2)]}
    if len(values) < min_n or len(employers) < 5:
        return out | {"reliable": False,
                      "note": f"Only {len(values)} stated rates from {len(employers)} employers; the site needs at least {min_n} rates from 5 employers, so treat these numbers as anecdotal."}
    lo, hi = bootstrap_median_ci(values)
    out["ci95"] = [round(lo, 2), round(hi, 2)]
    if (hi - lo) / median > d.checker.get("max_ci_rel_width", 0.25):
        return out | {"reliable": False, "note": "The 95% confidence interval is wider than a quarter of the median, so the median is uncertain."}
    return out | {"reliable": True}


def fits(skills, have):
    """A posting fits when at most a quarter (rounded down) of its skills are missing (gap.js)."""
    missing = sum(1 for s in skills if s not in have)
    return missing <= len(skills) // 4


def gap_plan(jobs, have, steps=3, demand=None):
    """Greedy: the skills that make the most extra postings fit, one after another (gap.js)."""
    owned = set(have)
    open_ = [s for s in jobs if s and not fits(s, owned)]
    plan, total = [], 0
    for _ in range(steps):
        gains = {}
        for skills in open_:
            missing = [s for s in skills if s not in owned]
            if len(missing) == len(skills) // 4 + 1:
                for s in missing:
                    gains[s] = gains.get(s, 0) + 1
        if not gains:
            break
        best = max(gains, key=lambda s: (gains[s], (demand or {}).get(s, 0), -s))
        owned.add(best)
        open_ = [s for s in open_ if not fits(s, owned)]
        total += gains[best]
        plan.append({"skill": best, "gain": gains[best], "total": total})
    return plan


def posting_out(d, i):
    j = d.jobs[i]
    p = d.posting(i) or {}
    out = {
        "title": p.get("title"),
        "company": p.get("company"),
        "city": d.cities[j[rd.CITY]] if j[rd.CITY] is not None and j[rd.CITY] >= 0 else p.get("city"),
        "field": d.field_labels.get(d.fields[j[rd.CAT]], d.fields[j[rd.CAT]]),
        "pay_eur_per_hour": j[rd.PAY],
        "german": d.checker["german"][j[rd.DE]],
        "published": p.get("published") or None,
        "url": p.get("url"),
    }
    return {k: v for k, v in out.items() if v is not None}


# --- The tools ---------------------------------------------------------------------------------

def get_overview(d):
    s, t = d.summary, d.summary["totals"]
    return {
        "as_of": d.as_of,
        "postings": t["jobs"],
        "published_last_7_days": t.get("published_7d"),
        "employers": t.get("companies"),
        "places": t.get("cities"),
        "by_source": {"Bundesagentur": t["by_source"].get("ba"), "company career sites (Arbeitnow)": t["by_source"].get("arbeitnow")},
        "pay": {"median_eur_per_hour": t.get("median_pay"), "ci95": [t.get("median_pay_lo"), t.get("median_pay_hi")],
                "middle_half": [t.get("p25_pay"), t.get("p75_pay")], "roles_with_stated_rate": t.get("with_pay")},
        "no_german_needed_share": t.get("no_german_share"),
        "remote_share": t.get("remote_share"),
        "top_cities": [{"city": c["city"], "postings": c["jobs"]} for c in s["cities"][:5]],
        "top_fields": [{"field": c["label"], "postings": c["jobs"]} for c in s["categories"][:5]],
        "top_skills": [{"skill": k["label"], "share": k["share"]} for k in s["skills"][:5]],
        "site": SITE,
    }


def search_jobs(d, city=None, field=None, skill=None, no_german=False, limit=10):
    fi, ci = resolve_field(d, field), resolve_city(d, city)
    si = resolve_skill(d, skill) if skill else None
    idx = [i for i in pool(d, fi, ci, no_german) if si is None or si in d.jobs[i][rd.SKILLS]]
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    idx = newest_first(d, idx)
    return {
        "as_of": d.as_of,
        "filters": filters_used(d, fi, ci, no_german, si),
        "matches": len(idx),
        "shown": min(limit, len(idx)),
        "jobs": [posting_out(d, i) for i in idx[:limit]],
        **({} if d.postings is not None else {"note": "Titles and links are unavailable (postings.json doesn't match today's data)."}),
    }


def pay_stats(d, field=None, city=None):
    fi, ci = resolve_field(d, field), resolve_city(d, city)
    idx = pool(d, fi, ci)
    return {
        "as_of": d.as_of,
        "filters": filters_used(d, fi, ci),
        "postings": len(idx),
        **pay_summary(d, idx, per_city=ci is not None),
        "method": "Hourly rates stated in the postings; each role (company + title) counts once" + (" per city." if ci is not None else "."),
    }


# Asked for in most office jobs; summary.json names them (older files: this list).
BASIC_SKILLS = ["ms_office", "excel", "powerpoint", "word", "outlook", "teams", "driving_licence"]


def basic_skill_ids(d):
    return set((d.summary or {}).get("basic_skills") or BASIC_SKILLS)


def top_skills(d, field=None, city=None, limit=10, exclude_basics=False):
    fi, ci = resolve_field(d, field), resolve_city(d, city)
    idx = pool(d, fi, ci)
    basics = basic_skill_ids(d)
    counts = {}
    for i in idx:
        for s in d.jobs[i][rd.SKILLS]:
            if exclude_basics and d.skills[s]["id"] in basics:
                continue
            counts[s] = counts.get(s, 0) + 1
    # Postings read by the LLM also say how they ask for a skill (rows of older files lack it).
    must, nice, read = {}, {}, 0
    for i in idx:
        row = d.jobs[i]
        if len(row) > rd.LLM and row[rd.LLM]:
            read += 1
            for s in row[rd.SKILLS]:
                target = nice if s in row[rd.NICE] else must
                target[s] = target.get(s, 0) + 1
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    top = sorted(counts.items(), key=lambda x: (-x[1], d.skills[x[0]]["label"]))[:limit]

    def entry(s, n):
        e = {"skill": d.skills[s]["label"], "group": d.skills[s]["group"], "postings": n,
             "share": round(n / len(idx), 4) if idx else 0}
        if must.get(s) or nice.get(s):
            e["must_have"], e["nice_to_have"] = must.get(s, 0), nice.get(s, 0)
        return e

    out = {
        "as_of": d.as_of,
        "filters": filters_used(d, fi, ci),
        "postings": len(idx),
        "skills": [entry(s, n) for s, n in top],
    }
    if exclude_basics:
        out["excluded_basics"] = sorted(s["label"] for s in d.skills if s["id"] in basics)
    if read:
        out["postings_read_by_llm"] = read
    return out


def newest_first(d, idx):
    """Postings `idx` with the latest publication date first (as listed when postings.json is missing)."""
    if d.postings is None:
        return list(idx)
    pos = d.posting_fields.index("published")
    return sorted(idx, key=lambda i: d.postings[i][pos] or "", reverse=True)


def match_skills(d, skills, city=None, field=None, examples=3):
    fi, ci = resolve_field(d, field), resolve_city(d, city)
    have, unknown = set(), []
    for name in skills or []:
        try:
            have.add(resolve_skill(d, name))
        except UnknownValue as e:
            unknown.append({"name": name, "did_you_mean": e.suggestions})
    idx = pool(d, fi, ci)
    listed = [d.jobs[i][rd.SKILLS] for i in idx if d.jobs[i][rd.SKILLS]]
    fitting = [i for i in idx if d.jobs[i][rd.SKILLS] and fits(d.jobs[i][rd.SKILLS], have)]
    fit = len(fitting)
    near = sum(1 for s in listed if not fits(s, have) and sum(1 for x in s if x not in have) == len(s) // 4 + 1)
    demand = {i: s["jobs"] for i, s in enumerate(d.skills)}
    plan = gap_plan(listed, have, steps=3, demand=demand)
    out = {
        "as_of": d.as_of,
        "filters": filters_used(d, fi, ci),
        "skills": sorted(d.skills[i]["label"] for i in have),
        "postings_listing_skills": len(listed),
        "fit": fit,
        "fit_share": round(fit / len(listed), 4) if listed else 0,
        "one_skill_short": near,
        "learn_next": [{"skill": d.skills[p["skill"]]["label"], "extra_postings": p["gain"], "extra_total": p["total"]} for p in plan],
        "examples": [posting_out(d, i) for i in newest_first(d, fitting)[:max(0, min(int(examples or 0), 10))]],
        "rule": "A posting fits when you lack at most a quarter (rounded down) of the skills it lists.",
    }
    if unknown:
        out["unknown_skills"] = unknown
    return out


# --- Kurs-Radar (kurs.json): job titles that career-changer courses train for ------------------

KURS_SITE = SITE + "kurs.html"
KURS_MIN_BASE = 20      # fewer postings behind a share: null, as on the page (kurs.js MIN_BASE)


def _kurs_share(m):
    return m["share"] if m and m.get("base", 0) >= KURS_MIN_BASE else None


def _kurs_wow(w):
    return round((w["now"] - w["before"]) / w["before"], 4) if w and w.get("before") else None


def _kurs_resolve(items, name, kind):
    key = fold(name)
    for x in items:
        if key in (fold(x["id"]), fold(x["name"])):
            return x
    # a word of the name: "Recruiter", "Personalreferent", "SEO"
    hits = [x for x in items if any(fold(w) == key for w in re.split(r"[ /()-]+", x["name"]) if w)]
    if len(hits) == 1:
        return hits[0]
    hits = [x for x in items if len(key) >= 3 and key in fold(x["name"])]
    if len(hits) == 1:
        return hits[0]
    raise UnknownValue(kind, name, suggest(name, [x["name"] for x in items]),
                       f"{kind.capitalize()}s are: " + ", ".join(x["name"] for x in items))


def _kurs_metrics(k, x, detail):
    out = {
        "postings": x["postings"],
        "postings_is_lower_bound": not x["postings_exact"],
        "sample": x["sample"],
        "quereinsteiger_share": _kurs_share(x["quereinsteiger"]),
        "ai_share": _kurs_share(x["ai"]),
        "basics_share": _kurs_share(x["basics"]),
        "new_in_7_days_share": x["new_7d"]["share"],
        "week_over_week_change": _kurs_wow(x.get("wow")),
    }
    if x["sample"] < KURS_MIN_BASE:
        out["too_few_postings"] = True
    label = lambda sid: k["skills"].get(sid, {}).get("label", sid)  # noqa: E731
    n = 15 if detail else 5
    out["top_skills"] = [
        {"skill": label(s["id"]), "share": s["share"], "postings": s["n"],
         **({"must_have": s["must"], "nice_to_have": s["nice"]} if s["must"] or s["nice"] else {})}
        for s in x["skills"][:n]
    ]
    if x.get("weighting"):
        # An academy: every job title with enough postings weighs the same; the per-title values next to it.
        out["weighting"] = x["weighting"]
        out["by_job_title"] = {key: [{"job_title": b["name"], "share": b["share"], "postings_read": b["base"],
                                      "in_average": b["weighted"]} for b in x[key]["by_title"]]
                               for key in ("quereinsteiger", "ai", "basics", "new_7d")}
    if detail:
        out["basics"] = [{"skill": label(s["id"]), "share": s["share"]} for s in x["basics_skills"]]
        out["other_terms"] = x.get("other_terms", [])
        out["counts"] = {"quereinsteiger": x["quereinsteiger"], "ai": x["ai"], "basics": x["basics"],
                         "read_by_llm": x["read_by_llm"], "with_text": x["with_text"]}
    return out


def _scaled(values):
    lo, hi = min(values, default=0), max(values, default=0)
    return [(v - lo) / (hi - lo) if hi > lo else 0.5 for v in values]


def _round(x):
    """Like JavaScript's Math.round (halves up), so the ranking matches the page."""
    return int(math.floor(x + 0.5))


def kurs_opportunities(k):
    """Same score as the page (kurs-rank.js `opportunities`): the mean of three parts, each scaled
    0–100 among the job titles with enough postings: volume (postings, log scale), momentum
    (share of postings new in the last 7 days) and openness (Quereinsteiger share)."""
    titles = [dict(x, academy=a["name"]) for a in k["academies"] for x in a["titles"]]
    rated = [x for x in titles if x["sample"] >= KURS_MIN_BASE and _kurs_share(x["quereinsteiger"]) is not None]
    vol = _scaled([math.log10(max(x["postings"], 1)) for x in rated])
    mom = _scaled([x["new_7d"]["share"] or 0 for x in rated])
    opn = _scaled([x["quereinsteiger"]["share"] for x in rated])
    rows = [{"job_title": x["name"], "academy": x["academy"], "score": _round((v + m + o) / 3 * 100),
             "parts": {"volume": _round(v * 100), "momentum": _round(m * 100), "openness": _round(o * 100)},
             "postings": x["postings"], "new_in_7_days_share": x["new_7d"]["share"],
             "quereinsteiger_share": x["quereinsteiger"]["share"]}
            for x, v, m, o in zip(rated, vol, mom, opn)]
    rows.sort(key=lambda y: (-y["score"], -y["postings"]))
    return {"how": "score 0-100 = mean of volume (postings, log scale), momentum (share new in the last 7 days) "
                   "and openness (Quereinsteiger share), each scaled 0-100 among the rated job titles",
            "ranking": rows, "not_rated": [x["name"] for x in titles if x not in rated]}


def kurs_radar(k, academy=None, job_title=None):
    """Kurs-Radar numbers: all academies, one academy with its job titles, or one job title."""
    base = {"as_of": k["as_of"], "note": k["note"]["en"], "page": KURS_SITE,
            "definitions": {
                "postings": "postings on the Bundesagentur's board for this title (a lower bound when capped)",
                "quereinsteiger_share": "open to career changers: text phrase or the employer's BA flag; base = postings with text",
                "ai_share": "AI asked for in the tasks or requirements",
                "basics_share": "MS Office, Excel, PowerPoint or Word asked for",
                "shares": f"null when fewer than {KURS_MIN_BASE} postings"}}
    academies = k["academies"]
    a = _kurs_resolve(academies, academy, "academy") if academy else None
    if job_title:
        pool_ = a["titles"] if a else [dict(x, _a=y) for y in academies for x in y["titles"]]
        t = _kurs_resolve(pool_, job_title, "job title")
        owner = a or t["_a"]
        return {**base, "academy": owner["name"], "job_title": t["name"], **_kurs_metrics(k, t, detail=True)}
    if a:
        return {**base, "academy": a["name"], **_kurs_metrics(k, a, detail=True),
                "job_titles": [{"job_title": x["name"], **_kurs_metrics(k, x, detail=False)} for x in a["titles"]]}
    return {**base, "academies": [{"academy": x["name"], **_kurs_metrics(k, x, detail=False),
                                   "job_titles": [t["name"] for t in x["titles"]]} for x in academies],
            "opportunities": kurs_opportunities(k)}


# --- Modes: the Werkstudent data (default) or the Jobs data set ------------------------------------

MODES = {"werkstudent": "", "jobs": "jobs_"}     # mode -> file prefix in docs/data


def resolve_mode(name):
    key = fold(name or "werkstudent")
    for m in MODES:
        if key == fold(m) or (m == "jobs" and key in ("job", "alljobs", "kursradar", "kurs")):
            return m
    raise UnknownValue("mode", name, [], "Modes are: werkstudent (default), jobs.")


def for_mode(data, mode):
    """A tool's answer for the Jobs data set: pay is a salary per year there, so the hourly
    names become yearly ones (rounded to whole euros), "field" means job title, and the
    answer says which data set it is about."""
    if mode != "jobs":
        return data

    def walk(x):
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                k2 = k.replace("eur_per_hour", "eur_per_year").replace("stated_rate", "stated_salary")
                v = walk(v)
                if k2 != k or k in ("middle_half", "ci95"):
                    v = [round(y) if isinstance(y, (int, float)) else y for y in v] if isinstance(v, list) else (round(v) if isinstance(v, float) else v)
                out[k2] = v
            return out
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str):
            return x.replace("an hourly rate", "a salary per year").replace("stated rates", "stated salaries")
        return x

    return {"mode": "jobs", "pay_unit": "eur_per_year_full_time",
            "note_mode": "Jobs mode: all working times for the Kurs-Radar job titles (fields are job titles), "
                         "Bundesagentur postings only; big job titles are a sample of the most relevant postings.",
            **walk(data)}

