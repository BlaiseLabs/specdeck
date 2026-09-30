"""Requirements: what the code should do, written by people; tests link to them and take expected values from them."""
import json

import pytest
from specdeck import and_, given, scenario, then, when

from .conftest import PLUGIN_ARGS, scenario_by_id

REQS = """\
requirements:
  - id: pricing.bulk-tier
    says: Orders of 100 units or more get 15% off; 10 or more get 5%.
    why: Agreed with sales.
    examples:
      - given: {qty: 100}
        expect: 15
      - given: {qty: 99}
        expect: 5
      - given: {qty: 9}
        expect: 0
  - id: pricing.tax
    says: Totals include 10% tax.
    examples:
      - given: {net: 100}
        expect: 110.0
  - id: pricing.refunds
    says: A refund returns the full amount paid.
"""

SUITE = '''
"""Pricing."""
import pytest
from specdeck import scenario, then, example, examples


def bulk_discount(qty):
    if qty > 100:      # BUG: the requirement says 100 or more
        return 15
    if qty >= 10:
        return 5
    return 0


@scenario("tier-examples")
@pytest.mark.parametrize("given, expected", examples("pricing.bulk-tier"))
def test_every_tier_example(given, expected):
    """Every example of the bulk tiers holds."""
    assert bulk_discount(**given) == expected


@scenario("tax", covers=["pricing.tax"])
def test_tax():
    """Tax is added to the net amount."""
    with then("the total includes tax", expected=str(example("pricing.tax", net=100))):
        assert round(100 * 1.1, 2) == example("pricing.tax", net=100)


@scenario("typo", covers=["pricing.taxes"])
def test_typo():
    """Links to a requirement that doesn't exist."""
'''


def _report(pytester):
    return json.loads((pytester.path / ".specdeck" / "requirements.json").read_text())


def _by_id(rep):
    return {r["id"]: r for r in rep["requirements"]}


@scenario("requirements-joined-with-tests", tags=["requirements"])
def test_each_requirement_gets_a_status_from_the_tests_that_cover_it(pytester, run):
    """Each requirement is met, failing or untested according to the tests that cover it."""
    with given("three requirements, a buggy tier function, and tests linked by covers= and by examples()"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_pricing=SUITE)
    with when("the suite runs"):
        result, spec, _ = run()
        rep = _report(pytester)
        by = _by_id(rep)
    with then("the tier requirement fails (the 100-unit example exposes the bug), tax is met, refunds untested",
              expected="failing, met, untested"):
        assert (by["pricing.bulk-tier"]["status"], by["pricing.tax"]["status"], by["pricing.refunds"]["status"]) == \
            ("failing", "met", "untested")
        assert rep["counts"] == {"met": 1, "failing": 1, "blocked": 0, "not-run": 0, "untested": 1, "total": 3}
    with and_("the terminal says so, and warns about the link to a requirement that doesn't exist"):
        result.stdout.fnmatch_lines(["*specdeck: requirements: 1 of 3 met, 1 failing, 1 untested*",
                                     "*typo covers 'pricing.taxes', which isn't in the requirements file*"])
        assert rep["unknown"] == [{"id": "pricing.taxes", "scenarios": ["typo"]}]
    with and_("scenarios carry req: tags in the generated spec, which the viewer's validator accepts"):
        assert "req:pricing.tax" in scenario_by_id(spec, "tax")["tags"]
        assert "req:pricing.bulk-tier" in scenario_by_id(spec, "tier-examples-qty-100")["tags"]


@scenario("requirements-examples-as-expected-values", tags=["requirements"],
          depends_on=["requirements-joined-with-tests"])
def test_expected_values_come_from_the_requirement_and_are_tracked(pytester, run):
    """example() and examples() give tests their expected values from the file, and each use is recorded."""
    with given("the same suite"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_pricing=SUITE)
    with when("it runs"):
        _, spec, _ = run()
        by = _by_id(_report(pytester))
    with then("examples() made one test case per example", expected="3 cases"):
        cases = [s for s in spec["scenarios"] if s["id"].startswith("tier-examples")]
        assert len(cases) == 3
    with and_("examples used by passing tests are marked; the one the bug breaks is not",
              expected="qty=99 and qty=9 used, qty=100 not"):
        checked = {e["given"]["qty"]: e["checked"] for e in by["pricing.bulk-tier"]["examples"]}
        assert checked == {100: False, 99: True, 9: True}
        assert by["pricing.tax"]["examples_checked"] == 1
    with and_("the step's expected label shows the value taken from the requirement"):
        step = scenario_by_id(spec, "tax")["steps"][0]
        assert step["assertion"]["expected_label"] == "110.0"


@scenario("requirements-unknown-example", tags=["requirements"])
def test_asking_for_an_example_that_isnt_there_explains_what_is(pytester, run):
    """example() with inputs the requirement has no example for fails with the examples it does have."""
    with given("a test asking for an example that doesn't exist"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_x='''
from specdeck import example

def test_missing():
    example("pricing.bulk-tier", qty=50)
''')
    with when("it runs"):
        result, _, _ = run()
    with then("it fails naming the examples that exist", expected="qty=100; qty=99; qty=9"):
        result.assert_outcomes(failed=1)
        result.stdout.fnmatch_lines(["*RequirementError: pricing.bulk-tier has no example with {'qty': 50}. "
                                     "It has: qty=100; qty=99; qty=9.*"])


@scenario("requirements-file-problems", tags=["requirements"])
def test_problems_in_the_file_are_reported_not_fatal(pytester, run):
    """A malformed entry is skipped with a warning; the rest of the file still works."""
    with given("a file with a duplicate id and an entry without `says:`"):
        pytester.makefile(".yaml", requirements="""\
requirements:
  - id: a.one
    says: The first one.
  - id: a.one
    says: A duplicate.
  - id: a.two
""")
        pytester.makepyfile(test_x='from specdeck import scenario\n\n@scenario(covers=["a.one"])\ndef test_x():\n    pass\n')
    with when("the suite runs"):
        result, _, _ = run()
        rep = _report(pytester)
    with then("both problems are reported and a.one still counts", expected="1 of 1 met, 2 warnings"):
        result.stdout.fnmatch_lines(["*a.one is defined twice*", "*a.two needs `says:`*"])
        assert rep["counts"]["met"] == 1 and rep["counts"]["total"] == 1


@scenario("requirements-in-history", tags=["requirements", "history"], depends_on=["requirements-joined-with-tests"])
def test_the_viewer_shows_requirements_as_they_were_in_each_run(pytester, record):
    """Each recorded run keeps the requirements file as it was, so the board and old runs agree with their tests."""
    with given("a recorded run, then the refunds requirement is removed and the suite runs again"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_pricing=SUITE)
        record()
        pytester.makefile(".yaml", requirements=REQS.split("  - id: pricing.refunds")[0])
        _, hist = record()
        project = hist.projects()[0]
        latest = hist.board(project["id"])["requirements"]
        first = hist.board(project["id"], run_id=hist.runs(project["id"])[-1]["id"])["requirements"]
    with then("the latest board has two requirements; the first run still shows three", expected="2, then 3"):
        assert latest["counts"]["total"] == 2 and first["counts"]["total"] == 3
    with and_("the board knows which examples each scenario used"):
        tax = next(r for r in latest["requirements"] if r["id"] == "pricing.tax")
        assert tax["status"] == "met" and tax["examples_checked"] == 1


@scenario("requirements-parametrized-ids", tags=["requirements"])
def test_a_parametrized_scenario_gets_one_id_per_case(pytester, run):
    """A scenario id on a parametrized test becomes one id per case; depending on the id means every case."""
    with given("examples() on a test with an explicit id, and another test depending on that id"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_p='''
import pytest
from specdeck import scenario, examples

@scenario("tiers")
@pytest.mark.parametrize("given, expected", examples("pricing.bulk-tier"))
def test_tiers(given, expected):
    pass

@scenario("after", depends_on=["tiers"])
def test_after():
    pass
''')
    with when("the suite runs"):
        result, spec, _ = run()
    with then("each case has its own id, with no duplicate-id warnings", expected="tiers-qty-100, tiers-qty-99, tiers-qty-9"):
        ids = [s["id"] for s in spec["scenarios"]]
        assert ids[:3] == ["tiers-qty-100", "tiers-qty-99", "tiers-qty-9"]
        assert "duplicate scenario id" not in result.stdout.str()
    with and_("depending on the id depends on all three cases"):
        assert scenario_by_id(spec, "after")["depends_on"] == ["tiers-qty-100", "tiers-qty-99", "tiers-qty-9"]
