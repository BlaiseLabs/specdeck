"""The viewer in a real browser: a developer sees where the suite is stuck, fixes it, re-runs, and edits a test.

Skipped when Playwright or its Chromium isn't installed.
"""
import os
import re

import pytest
from specdeck import and_, given, scenario, then, when

from .conftest import DAG_SUITE, http_json

playwright = pytest.importorskip("playwright.sync_api")

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]  # small /dev/shm in containers
# pytester moves HOME for each test; resolve the browser cache against the real one now
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser("~/.cache/ms-playwright")


def text(target, selector=None):
    """Visible text with whitespace collapsed (flex layouts put each span on its own line)."""
    raw = target.inner_text(selector) if selector else target.inner_text()
    return " ".join(raw.split())


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", BROWSERS)
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=LAUNCH_ARGS, timeout=30000)
        except Exception as exc:  # pragma: no cover - environment without a browser
            pytest.skip(f"no Chromium for Playwright: {exc}")
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.set_default_timeout(20000)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        yield pg
        browser.close()


@scenario(
    "ui-journey",
    tags=["ui", "acceptance"],
    depends_on=["server-runs-tests-and-streams", "server-edits-a-test", "analysis-frontier"],
    covers=["viewer.fix-loop"],
)
def test_developer_finds_the_blocker_fixes_it_and_watches_the_suite_go_green(pytester, record, viewer, page):
    """A developer opens the viewer, sees what's stuck and why, fixes the bug, re-runs from the browser, and edits a step.

    This is the whole loop the viewer exists for: what value is delivered, where
    it's stuck, what changed, all without leaving the page.
    """
    base, app = viewer
    with given("a suite where one bug fails a scenario and blocks the two built on it"):
        path = pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
        slug = http_json(f"{base}/api/projects")[1]["projects"][0]["slug"]
    with when("the developer opens the project's page"):
        page.goto(f"{base}/p/{slug}")
    with then("the summary shows 1 of 4 passing, with 1 failing and 2 blocked", expected="1 / 4"):
        page.wait_for_selector(".kpi-num")
        assert re.fullmatch(r"1\s*/\s*4", text(page, ".kpi-num").strip())
        assert "Failing 1" in text(page, ".legend") and "Blocked 2" in text(page, ".legend")
    with and_("'Stuck at' names the parse scenario as holding up the other two", expected="holds up 2"):
        stuck = page.locator(".side .card").first
        assert "Amounts are parsed from text." in text(stuck)
        assert "holds up 2" in text(stuck)
    with and_("the goal 'invoice' is marked stuck"):
        assert "Stuck" in text(page.locator(".side .card").nth(1))

    with when("the developer opens the stuck scenario"):
        stuck.locator(".item").first.click()
        drawer = page.locator("#drawer.open")
        drawer.wait_for()
    with then("the drawer shows the failing step with what was expected and what came back",
              expected="expected 41, got 42"):
        failed = drawer.locator(".step.failed")
        assert "the amount is 41" in text(failed)
        assert "41" in text(failed.locator(".step-lbl")) and "42" in text(failed.locator(".got"))

    with when("the bug is fixed in the code and the developer presses 'Run failing'"):
        path.write_text(path.read_text().replace("int(text) + 1  # BUG: off by one", "int(text)"))
        page.click("[data-act=run-failing]")
    with then("the page updates by itself to 4 of 4 passing", expected="4 / 4"):
        playwright.expect(page.locator(".kpi-num")).to_have_text(re.compile(r"^4\s*/\s*4$"), timeout=60000)
    with and_("the change is called out: three newly passing"):
        page.wait_for_selector(".chip.up")
        assert "3 newly passing" in text(page, ".chip.up")
    with and_("History lists both runs, the second from the viewer with ▲ 3"):
        page.click("[data-tab=history]")
        page.wait_for_selector(".runs-table tbody tr:nth-child(2)")
        first_row = text(page.locator(".runs-table tbody tr").first)
        assert "#2" in first_row and "Viewer" in first_row and "▲ 3" in first_row

    with when("the developer edits a step of the order-total scenario and saves it"):
        page.click("[data-tab=overview]")
        page.locator(".row", has_text="Order totals add parsed amounts.").click()
        page.click("#drawer [data-act=edit]")
        page.fill("#drawer input[data-step='0'][data-k='text']", "the amounts 1 and 2 are added")
        page.click("#drawer [data-act=edit-preview]")
        diff = page.locator("#drawer .diff").inner_text()  # keep indentation
        page.click("#drawer [data-act=edit-save]")
        page.wait_for_selector(".toast >> text=Saved")
    with then("the preview showed exactly that change, and the test file now says it",
              expected='with when("the amounts 1 and 2 are added"):'):
        assert '+    with when("the amounts 1 and 2 are added"):' in diff
        assert 'with when("the amounts 1 and 2 are added"):' in path.read_text()
    with and_("the drawer reminds the developer to run it"):
        assert "Run it to see the change here" in text(page, "#drawer")

    with when("the developer switches to the graph"):
        page.click("[data-tab=graph]")
        page.wait_for_selector(".gn")
    with then("the dependency chain is drawn, all green, and the scenario with no dependencies is left out",
              expected="3 nodes, 2 edges"):
        assert page.locator(".gn").count() == 3
        assert page.locator(".gn.st-passed").count() == 3
        assert page.locator(".ge").count() == 2
        assert "1 scenario with no dependencies isn't drawn" in text(page, "#gnote")
    with and_("asking for it adds it to the picture", expected="4 nodes"):
        page.click("#gnote [data-act=gindep]")
        playwright.expect(page.locator(".gn")).to_have_count(4)
    with and_("the page raised no script errors along the way"):
        assert page.errors == []
