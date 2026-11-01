"""Browser tests of the site (docs/) in the installed Google Chrome, through pytest-playwright.

The site is served from the checkout by a local static server on a free port, with the
repository's data. Every request to another host is blocked (pdf.js from cdnjs is never
needed here), and a console error or an uncaught exception fails the test. Each test gets a
fresh browser context, so localStorage (the tracker) starts empty.
Without Chrome or pytest-playwright the tests are skipped.
"""

import functools
import re
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("pytest_playwright")

DOCS = Path(__file__).resolve().parents[2] / "docs"
DESKTOP = {"viewport": {"width": 1366, "height": 860}}
PHONE = {"viewport": {"width": 375, "height": 812}, "is_mobile": True, "has_touch": True}
CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def site():
    """The base URL of docs/ served on a free local port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(DOCS)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    if not any(Path(c).exists() or shutil.which(c) for c in CHROME):
        pytest.skip("Google Chrome is not installed")
    return {**browser_type_launch_args, "channel": "chrome"}


@pytest.fixture
def open_site(browser, site):
    """open_site(path="", device=DESKTOP, context=None) -> a page that has loaded the data.

    Pass `context` to open a second page in the same browser context (shared localStorage)."""
    contexts, errors = [], []

    def open_(path="", device=DESKTOP, context=None, **context_args):
        if context is None:
            context = browser.new_context(reduced_motion="reduce", **device, **context_args)
            context.route(re.compile(r"^https?://(?!127\.0\.0\.1)"), lambda route: route.abort())
            contexts.append(context)
        page = context.new_page()
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(site + path)
        wait_for_data(page)
        return page

    yield open_
    for context in contexts:
        context.close()
    assert not errors, f"errors in the browser console: {errors}"


def wait_for_data(page):
    """The filter count is filled once summary.json and checker.json are loaded and drawn."""
    page.wait_for_function("document.getElementById('f-count')?.textContent.trim().length > 0")


def pick(page, select, starts_with=None):
    """Choose the first option of a <select> whose label starts with `starts_with` (or the first
    real option) and fire `change`; returns the option's label."""
    return page.evaluate(
        """([sel, start]) => {
            const s = document.querySelector(sel);
            const o = [...s.options].find((o) => o.value !== "" && (!start || o.textContent.trim().startsWith(start)));
            s.value = o.value;
            s.dispatchEvent(new Event("change", { bubbles: true }));
            return o.textContent.trim();
        }""",
        [select, starts_with],
    )
