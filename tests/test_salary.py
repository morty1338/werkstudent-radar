"""Salary per year for Jobs mode (radar/salary.py): the Bundesagentur's fields first, then
clear statements in the text; caps, floors, hourly rates and amounts without a period are ignored."""

import pytest

from radar import salary as S


@pytest.mark.parametrize("text, expected", [
    ("Verdienstmöglichkeiten zwischen 42.000 und 54.000 EUR jährlich", (42000, 54000)),
    ("eine jährliche Vergütung von derzeit ca. 57.000 – 79.000 Euro brutto.", (57000, 79000)),
    ("Vergütung: Jahresbruttogehalt 40.000 € bis 43.000 €, je nach Erfahrung", (40000, 43000)),
    ("36.000 € - 48.000 € pro Jahr - Vollzeit", (36000, 48000)),
    ("Gehaltsrahmen: 45.000 € - 55.000 € Brutto/Jahr", (45000, 55000)),
    ("Gehalt: 50k-60k p.a.", (50000, 60000)),
    ("Gehalt: 48.000 € p.a.", (48000, 48000)),
    ("3.500 - 4.500 EUR pro Monat", (42000, 54000)),                   # monthly × 12
    ("ca. **3.500 € bis 4.000 € brutto pro Monat**", (42000, 48000)),
    ("Monatsgehalt 2.900 € brutto", (34800, 34800)),
    ("Salary: €55,000 per year", None),                                  # English thousands comma: not parsed, unclear
])
def test_clear_salaries(text, expected):
    got = S.from_text(text)
    assert got == (tuple(float(x) for x in expected) if expected else None)


@pytest.mark.parametrize("text", [
    "Ein attraktives Gehalt von bis zu 65.000 EUR brutto pro Jahr.",      # a cap
    "**Gehalt:** Bis zu 55.000 € p.a.",
    "ab 45.000 € jährlich",                                              # a floor
    "Stundenlohn 15,00 € pro Stunde",                                    # hourly, not converted
    "Wir sind 2.000 Mitarbeitende im Jahr 2025",                         # not money
    "Gehalt 45.000 €",                                                   # no period
    "Minijob 538 € im Monat",                                            # below 12,000 a year
    "Gehalt 3.000 € im Monat oder 36.000 € im Jahr",                     # both periods: unclear for the first
])
def test_unclear_statements_are_ignored(text):
    got = S.from_text(text)
    assert got is None or got == (36000.0, 36000.0)                      # the last one: the yearly figure only


def test_bundesagentur_fields_first():
    detail = {"verguetungsangabe": "JAHRESGEHALT", "gehaltsspanneVon": 42000.0, "gehaltsspanneBis": 52000.0}
    assert S.annual(detail, "3.000 € pro Monat") == (42000.0, 52000.0, "ba")
    assert S.annual({"verguetungsangabe": "JAHRESGEHALT", "festgehalt": 58752.0}, "") == (58752.0, 58752.0, "ba")


@pytest.mark.parametrize("detail", [
    {"verguetungsangabe": "STUNDENLOHN", "festgehalt": 15.0},           # hourly: Werkstudent rules, not here
    {"verguetungsangabe": "JAHRESGEHALT", "festgehalt": 900.0},          # implausible
    {"verguetungsangabe": "KEINE_ANGABEN"},
    None,
])
def test_bundesagentur_fields_that_dont_count(detail):
    assert S.structured(detail) is None
    assert S.annual(detail, "45.000 € pro Jahr") == (45000.0, 45000.0, "text")


def test_mangled_euro_sign_in_a_range():
    assert S.from_text("Attraktives Gehalt von 47.000 \x80 bis 51.200 \x80 p.a.") == (47000.0, 51200.0)
