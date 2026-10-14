"""Annual salary of a regular job (Jobs mode): € per year from the Bundesagentur's salary
fields or the posting's text. Werkstudent jobs use hourly pay instead (radar/extract.py).

Rules (unclear statements are ignored, never guessed):
- The Bundesagentur's fields count when they say JAHRESGEHALT: a fixed amount or a range.
- In the text, an amount or a range of amounts counts only with a period next to it:
  per year ("pro Jahr", "jährlich", "p.a.", "Jahresgehalt", …) as it is, per month
  ("pro Monat", "monatlich", "Monatsgehalt", …) times 12. Hourly rates are not converted.
- "bis zu 65.000" and "ab 45.000" are a cap and a floor, not a salary: ignored.
- A range gives (low, high); the statistics use its midpoint.
- Plausible range: 12,000–250,000 € a year.
"""

import re

YEAR_MIN, YEAR_MAX = 12_000, 250_000

_NUM = r"(\d{1,3}(?:[.\s ]\d{3})+(?:,\d{1,2})?|\d{4,6}(?:,\d{1,2})?|\d{2,3}(?:[.,]\d)?\s?[kK](?![a-zäöü]))"
_CUR = r"(?:[€\x80]|eur(?:o)?\b|euro)"        # \x80: a € mangled in some Bundesagentur texts
_SEP = r"\s*(?:-|–|—|bis(?! zu)|und|to)\s*"
# A single amount or a range, with an optional currency sign around each number.
AMOUNT = re.compile(rf"(?:{_CUR}\s*)?{_NUM}\s*(?:{_CUR})?(?:{_SEP}(?:{_CUR}\s*)?{_NUM}\s*(?:{_CUR})?)?", re.I)
YEAR = re.compile(r"pro\s+jahr|im\s+jahr|jährlich|\bp\.\s?a\.?|per\s+annum|/\s*jahr|brutto[\s/-]*jahr|jahres(?:brutto)?(?:gehalt|vergütung|einkommen|bruttogehalt)"
                  r"|per\s+year|a\s+year|annual|yearly|/\s*year", re.I)
MONTH = re.compile(r"pro\s+monat|im\s+monat|monatlich|\bmtl\.?|/\s*monat|brutto[\s/-]*monat|monats(?:brutto)?(?:gehalt|lohn|vergütung)"
                   r"|per\s+month|a\s+month|monthly|/\s*month", re.I)
HOUR = re.compile(r"pro\s+stunde|/\s*std|/\s*h\b|stundenlohn|per\s+hour|hourly", re.I)
CAP_OR_FLOOR = re.compile(r"(?:bis\s+zu|bis|max(?:imal)?\.?|up\s+to|ab|mindestens|min\.|from|starting\s+at)\s*$", re.I)
# A money context: a currency next to the amount, or a salary word close before it.
MONEY_WORD = re.compile(r"gehalt|vergütung|verdienst|einkommen|salary|lohn|brutto|€|eur", re.I)


def _value(s):
    s = s.strip().replace(" ", "").replace(" ", "")
    if s[-1] in "kK":
        return float(s[:-1].replace(",", ".")) * 1000
    s = s.split(",")[0]
    return float(s.replace(".", "").replace(" ", ""))


def structured(detail):
    """(low, high) € per year from the Bundesagentur's fields, or None."""
    if not detail or detail.get("verguetungsangabe") != "JAHRESGEHALT":
        return None
    if detail.get("festgehalt"):
        v = float(detail["festgehalt"])
        return (v, v) if YEAR_MIN <= v <= YEAR_MAX else None
    lo, hi = detail.get("gehaltsspanneVon"), detail.get("gehaltsspanneBis")
    if lo and hi and YEAR_MIN <= float(lo) <= float(hi) <= YEAR_MAX:
        return float(lo), float(hi)
    return None


def from_text(text):
    """(low, high) € per year from the first clear salary statement in the text, or None."""
    text = text or ""
    for m in AMOUNT.finditer(text):
        before, after = text[max(0, m.start() - 60):m.start()], text[m.end():m.end() + 45]
        raw = m.group(0)
        has_cur = re.search(_CUR, raw, re.I) or re.match(rf"\s*{_CUR}", after, re.I)
        if not (has_cur or MONEY_WORD.search(before[-40:])):
            continue
        if CAP_OR_FLOOR.search(before[-14:]):
            continue
        near = before[-40:] + " " + after
        if HOUR.search(after[:25]):
            continue
        per_year, per_month = YEAR.search(near), MONTH.search(near)
        if bool(per_year) == bool(per_month):
            continue                                   # no period, or both: unclear
        try:
            lo = _value(m.group(1))
            hi = _value(m.group(2)) if m.group(2) else lo
        except (ValueError, IndexError):
            continue
        factor = 12 if per_month else 1
        lo, hi = sorted((lo * factor, hi * factor))
        if YEAR_MIN <= lo <= hi <= YEAR_MAX and hi <= lo * 2.5:
            return lo, hi
    return None


def annual(detail, text):
    """(low, high, source) with source "ba" or "text", or None."""
    s = structured(detail)
    if s:
        return (*s, "ba")
    t = from_text(text)
    return (*t, "text") if t else None
