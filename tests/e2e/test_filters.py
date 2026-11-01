"""The filter bar and the address: filters go into the URL and come back after a reload."""

import re

from playwright.sync_api import expect

from .conftest import pick, wait_for_data


def jobs(page):
    return int(re.sub(r"\D", "", page.locator("#f-count").text_content()))


def test_filters_go_into_the_address_and_come_back_after_a_reload(open_site):
    page = open_site()
    total = jobs(page)
    pick(page, "#f-field", "IT")
    pick(page, "#f-city", "Berlin")
    page.check("#f-nogerman")
    expect(page).to_have_url(re.compile(r"\?field=[^&]+&city=Berlin&german=no$"))
    filtered, field = jobs(page), page.input_value("#f-field")
    assert 0 < filtered < total

    page.reload()
    wait_for_data(page)
    assert jobs(page) == filtered
    assert page.input_value("#f-field") == field
    assert page.is_checked("#f-nogerman")


def test_reset_clears_the_filters_and_the_address(open_site):
    page = open_site("?city=Berlin&german=no")
    total_berlin = jobs(page)
    page.click("#f-reset")
    expect(page).not_to_have_url(re.compile(r"city=|german="))
    assert jobs(page) > total_berlin
    assert not page.is_checked("#f-nogerman")


def test_german_interface_from_the_address(open_site):
    page = open_site("?lang=de")
    assert page.evaluate("document.documentElement.lang") == "de"
    expect(page.locator("[data-lang=de]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#f-nogerman + span")).not_to_have_text("No German needed")
