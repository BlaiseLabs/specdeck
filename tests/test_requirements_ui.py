"""The Requirements tab in a real browser: what should happen, and which tests show that it does."""
import re

import pytest
from specdeck import and_, given, scenario, then, when

from .conftest import http_json
from .test_requirements import REQS, SUITE
from .test_ui_journey import page, text  # noqa: F401  (the browser fixture)

playwright = pytest.importorskip("playwright.sync_api")


@scenario("requirements-tab", tags=["ui", "requirements"], depends_on=["requirements-joined-with-tests", "ui-journey"])
def test_a_developer_sees_which_requirements_are_met_and_why(pytester, record, viewer, page):  # noqa: F811
    """The headline counts requirements met; the tab shows each one's status, its examples and the tests behind it."""
    base, _ = viewer
    with given("three requirements: one broken by a bug, one met, one with no test"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_pricing=SUITE)
        record()
        slug = http_json(f"{base}/api/projects")[1]["projects"][0]["slug"]
    with when("the developer opens the project"):
        page.goto(f"{base}/p/{slug}")
        page.wait_for_selector(".req-kpi")
    with then("the headline is requirements met, before scenarios passing", expected="1 / 3 requirements met"):
        assert re.fullmatch(r"1\s*/\s*3", page.inner_text(".req-kpi .kpi-num").strip())
        assert "requirements met" in text(page, ".req-kpi")
    with when("they open the Requirements tab from the headline"):
        page.click(".req-kpi")
        page.wait_for_selector(".req")
    with then("problems come first: the failing requirement, then the one with no test, then the met one",
              expected="failing, untested, met"):
        ids = [el.inner_text() for el in page.locator(".req .req-id").all()]
        assert ids == ["pricing.bulk-tier", "pricing.refunds", "pricing.tax"]
        assert "Failing" in text(page.locator(".req").first) and "No test" in text(page.locator(".req").nth(1))
    with and_("each example shows whether a passing test takes its expected value from it"):
        rows = [text(r) for r in page.locator(".req").first.locator("table.ex tbody tr").all()]
        assert "✓" not in rows[0] and "✓" in rows[1] and "✓" in rows[2]
    with and_("the link to a requirement that doesn't exist is called out"):
        assert "pricing.taxes" in text(page, ".req-warn")
    with when("they open a test behind the failing requirement, then follow its Covers link back"):
        page.locator(".req").first.locator(".rel-chip").first.click()
        page.wait_for_selector("#drawer.open")
        assert "pricing.bulk-tier" in text(page, "#drawer")
        page.click("#drawer [data-req-link='pricing.bulk-tier']")
    with then("the Requirements tab shows that requirement highlighted", expected=".req.focus = pricing.bulk-tier"):
        playwright.expect(page.locator(".req.focus .req-id")).to_have_text("pricing.bulk-tier")
    with and_("filtering to 'No test' leaves only the refunds requirement"):
        page.click("[data-reqfilter=untested]")
        assert [el.inner_text() for el in page.locator(".req .req-id").all()] == ["pricing.refunds"]
    with and_("the page raised no script errors"):
        assert page.errors == []
