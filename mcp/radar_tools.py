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
    if d.postings is not None:   # newest first
        pos = d.posting_fields.index("published")
        idx.sort(key=lambda i: d.postings[i][pos] or "", reverse=True)
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


def top_skills(d, field=None, city=None, limit=10):
    fi, ci = resolve_field(d, field), resolve_city(d, city)
    idx = pool(d, fi, ci)
    counts = {}
    for i in idx:
        for s in d.jobs[i][rd.SKILLS]:
            counts[s] = counts.get(s, 0) + 1
    limit = max(1, min(int(limit or 10), MAX_LIMIT))
    top = sorted(counts.items(), key=lambda x: (-x[1], d.skills[x[0]]["label"]))[:limit]
    return {
        "as_of": d.as_of,
        "filters": filters_used(d, fi, ci),
        "postings": len(idx),
        "skills": [{"skill": d.skills[s]["label"], "group": d.skills[s]["group"], "postings": n,
                    "share": round(n / len(idx), 4) if idx else 0} for s, n in top],
    }


def match_skills(d, skills, city=None, field=None):
    fi, ci = resolve_field(d, field), resolve_city(d, city)
    have, unknown = set(), []
    for name in skills or []:
        try:
            have.add(resolve_skill(d, name))
        except UnknownValue as e:
            unknown.append({"name": name, "did_you_mean": e.suggestions})
    idx = pool(d, fi, ci)
    listed = [d.jobs[i][rd.SKILLS] for i in idx if d.jobs[i][rd.SKILLS]]
    fit = sum(1 for s in listed if fits(s, have))
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
        "rule": "A posting fits when you lack at most a quarter (rounded down) of the skills it lists.",
    }
    if unknown:
        out["unknown_skills"] = unknown
    return out
