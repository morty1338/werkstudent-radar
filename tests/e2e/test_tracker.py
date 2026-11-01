"""The application tracker (tracker.js): tracking a posting, editing it, the backup link, two tabs."""

from playwright.sync_api import expect

from .conftest import wait_for_data


def track_postings(page, n=1):
    """Open the job list for Python and track the first n postings; returns their buttons."""
    page.click("#match-summary [data-act=jobs]")
    buttons = page.locator("#job-list .track-btn")
    expect(buttons.first).to_be_visible()
    for i in range(n):
        buttons.nth(i).click()
        expect(buttons.nth(i)).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#trk-count")).to_have_text(str(n))
    return buttons


def open_panel(page):
    page.click("#trk-open")
    expect(page.locator("#trk")).to_be_visible()
    return page.locator("#trk-list [data-id]")


def test_a_tracked_posting_keeps_its_status_after_a_reload(open_site):
    page = open_site("?skills=python")
    track_postings(page)
    open_panel(page).first.locator("[data-act=status]").select_option("applied")

    page.reload()
    wait_for_data(page)
    expect(open_panel(page).first.locator("[data-act=status]")).to_have_value("applied")


def test_typing_a_note_then_picking_a_status_keeps_both(open_site):
    # Leaving the note fires its change event; a redraw there replaced the status select being
    # picked (in Chrome: "Failed to set the 'innerHTML' property" in the console).
    page = open_site("?skills=python")
    track_postings(page)
    card = open_panel(page).first
    card.locator("[data-act=note]").fill("Ask about remote days")
    card.locator("[data-act=status]").select_option("applied")
    expect(page.locator("#trk-list [data-act=status]").first).to_have_value("applied")

    page.reload()
    wait_for_data(page)
    card = open_panel(page).first
    expect(card.locator("[data-act=note]")).to_have_value("Ask about remote days")
    expect(card.locator("[data-act=status]")).to_have_value("applied")


def test_the_backup_link_brings_the_list_to_another_browser(open_site):
    page = open_site("?skills=python", permissions=["clipboard-read", "clipboard-write"])
    track_postings(page, 2)
    open_panel(page)
    page.click("#trk-link")
    expect(page.locator("#trk-msg")).to_contain_text("Link copied")
    link = page.evaluate("navigator.clipboard.readText()")
    assert "#trk=" in link

    other = open_site(link.split("/", 3)[3])               # a fresh browser: empty localStorage
    expect(other.locator("#trk")).to_be_visible()          # the import opens the panel
    expect(other.locator("#trk-list [data-id]")).to_have_count(2)
    assert "#trk=" not in other.url                         # the link isn't kept in the address


def test_typing_a_note_survives_a_change_in_another_tab(open_site):
    tab = open_site("?skills=python")
    track_postings(tab, 2)
    other = open_site("?skills=python", context=tab.context)   # same browser, same localStorage

    note = open_panel(tab).first.locator("[data-act=note]")
    note.click()
    tab.keyboard.type("Call on ")
    open_panel(other).nth(1).locator("[data-act=status]").select_option("applied")   # the other tab saves
    expect(tab.locator("#trk-list [data-act=status]").nth(1)).to_have_value("applied")   # this tab redrew
    tab.keyboard.type("Monday")
    expect(tab.locator("#trk-list [data-act=note]").first).to_have_value("Call on Monday")


def test_a_half_typed_new_application_survives_a_change_in_another_tab(open_site):
    tab = open_site("?skills=python")
    track_postings(tab)
    other = open_site("?skills=python", context=tab.context)

    open_panel(tab)
    tab.click("#trk-new")
    tab.fill("#trk-form [name=title]", "Werkstudent Data")
    open_panel(other).first.locator("[data-act=status]").select_option("interview")
    expect(tab.locator("#trk-list [data-act=status]").first).to_have_value("interview")   # the tab redrew
    expect(tab.locator("#trk-form [name=title]")).to_have_value("Werkstudent Data")
