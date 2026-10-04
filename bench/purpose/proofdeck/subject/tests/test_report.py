"""What the reviewer and the agent see: findings on lines, never a score."""
import json

from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind, analyze
from proofdeck.report import exit_code, render_json, render_markdown

from .conftest import line_of


@scenario("report-findings-anchored-to-lines", tags=["report"], depends_on=["status-ran-only-when-a-mutant-survives"])
def test_markdown_lists_each_finding_at_its_line_with_the_mutant(demo):
    """Each finding appears as path:line with the before/after of the surviving mutant."""
    with given("an analysis of the vacuous variant"):
        repo = demo("vacuous")
        boundary = line_of(repo, "src/pricing.py", "if qty > 100")
        report = analyze(str(repo))
    with when("it is rendered as a PR comment"):
        md = render_markdown(report)
    with then("the boundary finding is anchored to its line", expected=f"src/pricing.py:{boundary}"):
        assert f"src/pricing.py:{boundary}" in md
    with and_("the mutant diff is shown", expected="- if qty > 100: / + if qty >= 100:"):
        assert "if qty > 100:" in md and "if qty >= 100:" in md


@scenario("report-no-score", tags=["report"], depends_on=["report-findings-anchored-to-lines"])
def test_no_percentage_or_score_is_ever_reported(demo):
    """No mutation score: Google found scores 'neither concrete nor actionable'."""
    with given("an analysis of the vacuous variant"):
        report = analyze(str(demo("vacuous")))
    with when("both renderings are produced"):
        md = render_markdown(report)
        data = render_json(report)
    with then("the markdown contains no percentage", expected="no '%'"):
        assert "%" not in md
    with and_("the JSON has no score field", expected="keys: lines, tests, findings"):
        assert set(data) == {"lines", "tests", "findings"}
        assert "score" not in json.dumps(data).lower()


@scenario("report-agent-safe-guidance", tags=["report", "agents"], depends_on=["report-findings-anchored-to-lines"])
def test_guidance_never_tells_the_author_to_assert_current_output(demo):
    """Surviving-mutant guidance asks for the value the REQUIREMENT gives — never the code's current output.

    A gate that says "add an assertion that kills this mutant" teaches an agent to
    paste current behaviour, which locks the bug in (demonstrated with teeth).
    """
    with given("an analysis of the vacuous variant, whose boundary mutant survives"):
        report = analyze(str(demo("vacuous")))
        survivors = report.findings_of(FindingKind.SURVIVING_MUTANT)
    with then("the guidance points to the requirement", expected="mentions 'requirement'"):
        assert survivors and all("requirement" in f.message.lower() for f in survivors)
    with and_("it never suggests asserting the current output"):
        banned = ("current output", "current behaviour", "current behavior", "what the code returns")
        assert not any(b in f.message.lower() for f in survivors for b in banned)


@scenario("report-gate-fails-on-survivors", tags=["report", "gate"], depends_on=["status-ran-only-when-a-mutant-survives"])
def test_gate_fails_when_a_mutant_survives(demo):
    """With gating on, a surviving mutant fails the check."""
    with given("an analysis of the vacuous variant"):
        report = analyze(str(demo("vacuous")))
    with then("the exit code is 1", expected="1"):
        assert exit_code(report, gate=True) == 1
    with and_("without gating it is advisory only", expected="0"):
        assert exit_code(report, gate=False) == 0


@scenario(
    "report-unconfirmed-is-advisory",
    tags=["report", "gate", "provenance"],
    depends_on=["report-gate-fails-on-survivors", "provenance-honest-about-its-limit"],
)
def test_unconfirmed_expectations_do_not_fail_the_gate(demo):
    """Unconfirmed expectations are shown to the reviewer but never fail the gate on their own."""
    with given("the good variant: every line checked, new expectations unconfirmed"):
        report = analyze(str(demo("good")))
    with then("it has unconfirmed-expectation findings", expected="> 0"):
        assert report.findings_of(FindingKind.UNCONFIRMED_EXPECTATION)
    with and_("the gate still passes", expected="0"):
        assert exit_code(report, gate=True) == 0


@scenario("report-json-names-the-tests", tags=["report", "agents", "mocks"], depends_on=["status-mock-only", "report-no-score"])
def test_json_says_which_tests_ran_and_noticed_each_line_and_what_they_fake(demo):
    """The JSON carries the evidence per test, so a tool that knows what a test is FOR
    (specdeck's requirements) can tell a requirement met by a real check from one met
    only through fakes."""
    with given("an analysis of the mock-only variant"):
        repo = demo("mockonly")
        subtotal = line_of(repo, "src/pricing.py", "subtotal = apply_discount")
        data = render_json(analyze(str(repo)))
        row = next(r for r in data["lines"] if r["line"] == subtotal)
        faker = "tests/test_new.py::test_order_total_uses_bulk_discount"
    with then("the mock-only line names the test that ran it and the one that noticed", expected=faker):
        assert row["status"] == "mock-only"
        assert faker in row["ran_by"] and row["killed_by"] == [faker]
    with and_("that test is listed with what it fakes", expected="bulk_discount, apply_discount"):
        entry = next(t for t in data["tests"] if t["nodeid"] == faker)
        assert set(entry["fakes"]) >= {"bulk_discount", "apply_discount"}
    with and_("in the good variant the noticing test fakes nothing", expected="fakes: []"):
        good = demo("good")
        boundary = line_of(good, "src/pricing.py", "if qty >= 100")
        gdata = render_json(analyze(str(good)))
        grow = next(r for r in gdata["lines"] if r["line"] == boundary)
        assert grow["status"] == "checked" and grow["killed_by"]
        assert all(t["fakes"] == [] for t in gdata["tests"] if t["nodeid"] in grow["killed_by"])
