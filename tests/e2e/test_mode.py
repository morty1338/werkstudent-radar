"""The Werkstudent | Jobs switch (mode.js): another data set, the city kept, the field dropped."""

import re

from playwright.sync_api import expect

from .conftest import pick, wait_for_data


def test_switching_to_jobs_keeps_the_city_and_drops_the_field(open_site):
    page = open_site()
    pick(page, "#f-field")
    pick(page, "#f-city", "Berlin")
    expect(page.locator("#f-academy")).to_be_hidden()

    page.click("#mode-switch [data-mode=jobs]")
    page.wait_for_url(re.compile(r"mode=jobs"))
    wait_for_data(page)
    assert "city=Berlin" in page.url and "field=" not in page.url
    expect(page.locator("#mode-switch [data-mode=jobs]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#f-academy")).to_be_visible()

    page.click("#mode-switch [data-mode=werkstudent]")
    page.wait_for_url(lambda url: "mode=jobs" not in url)
    wait_for_data(page)
    assert "city=Berlin" in page.url
    expect(page.locator("#f-academy")).to_be_hidden()


def test_the_old_all_jobs_page_leads_to_jobs_mode(open_site):
    page = open_site("kurs.html")
    assert "index.html?mode=jobs" in page.url
    expect(page.locator("#mode-switch [data-mode=jobs]")).to_have_attribute("aria-pressed", "true")


def test_old_all_jobs_links_keep_their_academy_title_and_language(open_site):
    page = open_site("kurs.html?a=hr&t=personalreferent&lang=de")
    assert "mode=jobs" in page.url and "academy=hr" in page.url and "field=personalreferent" in page.url
    assert page.evaluate("document.documentElement.lang") == "de"
    assert page.evaluate("document.querySelector('#f-field').selectedOptions[0].textContent").startswith("Personalreferent")
    assert page.input_value("#f-academy") != ""
