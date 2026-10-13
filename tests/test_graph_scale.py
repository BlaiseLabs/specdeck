"""The graph on a large suite: it opens on a feature map, and every view narrows to something readable.

Skipped when Playwright or its Chromium isn't installed.
"""
import random
import re

import pytest
from specdeck import and_, given, scenario, then, when

from .conftest import http_json
from .test_ui_journey import page, text  # noqa: F401  (the browser fixture)

playwright = pytest.importorskip("playwright.sync_api")


def big_suite(pytester, modules=36, per_module=10, seed=11):
    """Modules of ten tests; most depend on an earlier test in their module, a few on another
    module's first test (a hub); some are parametrized x3; about one in twenty fails."""
    rng = random.Random(seed)
    hubs = []
    files = {}
    for m in range(1, modules + 1):
        lines = [f'"""Area {m}: a feature of the synthetic product."""', "import pytest",
                 "from specdeck import scenario", ""]
        local = []
        for i in range(per_module):
            sid = f"a{m}-t{i}"
            deps = []
            if local and rng.random() < 0.7:
                deps.append(rng.choice(local[-3:]))
            if hubs and rng.random() < 0.1:
                deps.append(rng.choice(hubs))
            param = i > 0 and rng.random() < 0.15  # parametrized tests get derived ids, one per case
            kw = [] if param else [f'"{sid}"']
            if deps:
                kw.append(f"depends_on={deps!r}")
            lines.append(f"@scenario({', '.join(kw)})")
            if param:
                lines.append('@pytest.mark.parametrize("case", ["x", "y", "z"])')
            lines.append(f"def test_{i}({'case' if param else ''}):")
            lines.append(f'    """Behaviour {i} of area {m} holds."""')
            lines.append(f"    assert {'False' if rng.random() < 0.05 else 'True'}")
            lines.append("")
            if not param:
                local.append(sid)
        hubs.append(local[0])
        files[f"test_area{m:02d}"] = "\n".join(lines)
    pytester.makepyfile(**files)


@scenario("graph-large-suite", tags=["ui", "graph"], depends_on=["ui-journey"], covers=["viewer.readable-at-scale"])
def test_a_large_suite_opens_on_a_feature_map_and_narrows_down(pytester, record, viewer, page):  # noqa: F811
    """A big suite opens on one node per feature; opening a feature, 'Problems' and 'Selected branch'
    each draw a small, readable picture, and zooming out turns nodes into status tiles.
    """
    base, _ = viewer
    with given("a suite of 36 features, most scenarios chained inside their feature and some across"):
        big_suite(pytester)
        record()
        slug = http_json(f"{base}/api/projects")[1]["projects"][0]["slug"]
        board = http_json(f"{base}/api/p/{slug}/board")[1]
    with when("the developer opens the graph"):
        page.goto(f"{base}/p/{slug}#tab=graph")
        page.wait_for_selector(".gf")
    with then("it shows the feature map: one node per feature, not hundreds of scenarios", expected="36 feature nodes"):
        assert page.locator(".gf").count() == 36 and page.locator(".gn").count() == 0
        assert "36 features" in text(page, "#gnote")
        assert page.locator("[data-glevel=features].on").count() == 1
    with and_("arrows between features carry how many dependencies they stand for"):
        assert page.locator(".gel").count() > 0

    with when("the developer opens one feature from the map"):
        gid = next(g["id"] for g in board["groups"] if g["name"].startswith("Area 7:"))
        page.locator(f".gf[data-gid='{gid}']").click()
        page.wait_for_selector(".gn")
    with then("only that feature's scenarios are drawn, plus the ones elsewhere they connect to (marked)",
              expected="area 7 + neighbours"):
        unit = lambda s: f"fam:{s['file']}::{s['function']}" if s["parametrized"] else s["id"]  # noqa: E731
        own = {unit(s) for s in board["scenarios"] if s["group"] == gid}
        assert "Area 7" in text(page, "#gpick-label")
        drawn = [(el.get_attribute("data-uid"), el.get_attribute("class")) for el in page.locator(".gn").all()]
        assert {u for u, c in drawn if " ext" not in f" {c}"} == own
        assert any(" ext" in f" {c}" for _u, c in drawn)
    with and_("'Show all features' goes back to the map"):
        page.click("#gnote [data-act=gall]")
        page.wait_for_selector(".gf")

    with when("the developer asks for problems only"):
        page.click("[data-gfocus=problems]")
        page.wait_for_selector(".gn")
    with then("every node drawn is failing or blocked", expected="only st-failed / st-blocked"):
        classes = [el.get_attribute("class") for el in page.locator(".gn").all()]
        assert classes and all(re.search(r"st-(failed|error|blocked)", c) for c in classes)

    with when("the developer finds a hub scenario and asks for its branch"):
        page.fill("#gq", "Behaviour 0 of area 1 holds")
        page.press("#gq", "Enter")
        page.click("[data-gfocus=branch]")
        page.wait_for_selector("#drawer.open")
    with then("only what it needs and what needs it is drawn", expected="the hub's branch"):
        by = {s["id"]: s for s in board["scenarios"]}
        branch, stack = {"a1-t0"}, ["a1-t0"]
        while stack:  # a1-t0 has no prerequisites, so its branch is everything downstream
            for d in by[stack.pop()]["dependents"]:
                if d not in branch:
                    branch.add(d)
                    stack.append(d)
        drawn_families = {(by[i]["file"], by[i]["function"]) for i in branch}
        assert page.locator(".gn").count() == len(drawn_families)
        assert "Behaviour 0 of area 1 holds." in text(page, "#gnote")

    with when("the developer switches to every scenario and zooms out to fit"):
        page.fill("#gq", "")
        page.click("[data-gfocus=all]")
        page.click("[data-glevel=scenarios]")
        page.wait_for_selector(".gn")
        page.click("[data-act=fit]")
    with then("parametrized cases are one node each, marked with their case count", expected="'3 cases'"):
        assert page.locator(".gn .gsub-t", has_text="3 cases").count() > 0
        params = {(s["file"], s["function"]) for s in board["scenarios"] if s["parametrized"]}
        assert page.locator(".gn[data-uid^='fam:']").count() <= len(params)
    with and_("zoomed that far out, nodes become status tiles"):
        playwright.expect(page.locator("#gsvg")).to_have_class(re.compile(r"\bfar\b"))
    with and_("scenarios with no dependencies are left out, and the page says how many"):
        assert re.search(r"\d+ scenarios? with no dependencies (isn't|aren't) drawn", text(page, "#gnote"))
    with and_("the minimap appears when the picture is bigger than the view, and clicking it moves the view"):
        page.click("[data-act=zoom-in]")
        page.click("[data-act=zoom-in]")
        page.click("[data-act=zoom-in]")
        playwright.expect(page.locator("#gminibox")).to_be_visible()
        before = page.locator("#gview").get_attribute("transform")
        box = page.locator("#gmini").bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.9)
        assert page.locator("#gview").get_attribute("transform") != before
    with and_("the feature picker finds a feature by name"):
        page.click("[data-act=gpick]")
        page.fill("#gpick-q", "area 12")
        page.press("#gpick-q", "Enter")
        playwright.expect(page.locator("#gpick-label")).to_contain_text("Area 12")
    with and_("the page raised no script errors"):
        assert page.errors == []
