"""Phones at 375 px: no sideways scroll, the bottom tab bar, the filter sheet, 16 px form fields."""

import pytest
from playwright.sync_api import expect

from .conftest import PHONE

# Elements wider than the viewport, the page's own scroll width, and form fields under 16 px (iOS zooms in).
LAYOUT = """() => {
    const w = document.documentElement.clientWidth;
    const trk = document.getElementById("trk").getBoundingClientRect();
    const closed = (e) => e.closest("#trk") && trk.left >= w;   // the tracker waits off screen
    const wide = [...document.querySelectorAll("body *")]
        .filter((e) => e.getClientRects().length && !e.closest("[hidden], .sr-only") && !closed(e))
        .filter((e) => { const r = e.getBoundingClientRect(); return r.right > w + 1 || r.left < -1; })
        .filter((e) => getComputedStyle(e).position !== "fixed" || e.getBoundingClientRect().width > w + 1)
        .map((e) => e.id ? "#" + e.id : `${e.tagName.toLowerCase()}.${[...e.classList].join(".")} in .${e.parentElement.className} "${e.textContent.trim().slice(0, 30)}"`);
    const small = [...document.querySelectorAll("input:not([type=checkbox], [type=radio], [type=file]), select, textarea")]
        .filter((e) => e.getClientRects().length && parseFloat(getComputedStyle(e).fontSize) < 16)
        .map((e) => e.id || e.name || e.dataset.act);
    return { scroll: document.documentElement.scrollWidth - w, wide: [...new Set(wide)].slice(0, 10), small };
}"""


def assert_fits(page):
    layout = page.evaluate(LAYOUT)
    assert layout == {"scroll": 0, "wide": [], "small": []}, layout


@pytest.mark.parametrize("path", ["", "?lang=de", "?mode=jobs"])
def test_the_page_fits_375_px(open_site, path):
    page = open_site(path, device=PHONE)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    assert_fits(page)
    tabs = page.locator(".nav a:visible")
    assert 1 <= tabs.count() <= 6


def test_the_filter_sheet_and_the_tracker_fit_375_px(open_site):
    page = open_site("?skills=python", device=PHONE)
    page.click("#f-open")
    expect(page.locator("#f-sheet")).to_be_visible()
    assert_fits(page)
    page.click("#f-done")

    page.click("#match-summary [data-act=jobs]")
    page.locator("#job-list .track-btn").first.click()
    page.click("#trk-open")
    expect(page.locator("#trk")).to_be_visible()
    assert_fits(page)
