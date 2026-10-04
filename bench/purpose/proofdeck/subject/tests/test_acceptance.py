"""End to end: a reviewer runs `proofdeck check` on each demo pull request.

Five branches carry the same feature. Four ship the same off-by-one bug
(`qty > 100`, spec says 100+ units get 15%) behind a different kind of test.
Existing mutation tools score the tautological branch exactly like the correct
one. proofdeck must tell the reviewer something true about every one of them.
"""
import json

from specdeck import and_, given, scenario, then, when

from .conftest import line_of, run_cli

DEPS = [
    "status-mock-only",
    "report-agent-safe-guidance",
    "report-unconfirmed-is-advisory",
    "tamper-config-read-from-main",
    "detect-vacuous-assertions",
    "detect-self-comparison",
]


def _check(repo):
    proc = run_cli(repo, "check", "--base", "main", "--format", "json")
    return proc.returncode, json.loads(proc.stdout or "{}")


def _kinds_at(data, path, line):
    return {f["kind"] for f in data.get("findings", []) if f["path"] == path and f["line"] == line}


def _status(data, path, line):
    return next((r["status"] for r in data.get("lines", []) if r["path"] == path and r["line"] == line), None)


@scenario("accept-good-branch", tags=["acceptance"], depends_on=DEPS)
def test_correct_branch_passes_with_its_new_expectations_marked_unconfirmed(demo):
    """Correct code with spec-correct tests passes; its new expectations are listed for confirmation."""
    with given("the good branch"):
        repo = demo("good")
        boundary = line_of(repo, "src/pricing.py", "if qty >= 100")
    with when("the reviewer runs `proofdeck check --base main`"):
        code, data = _check(repo)
    with then("the gate passes", expected="exit 0"):
        assert code == 0
    with and_("the boundary line is checked", expected="checked"):
        assert _status(data, "src/pricing.py", boundary) == "checked"
    with and_("the new expectations are listed as unconfirmed for a human to verify"):
        assert any(f["kind"] == "unconfirmed-expectation" for f in data["findings"])


@scenario("accept-vacuous-branch", tags=["acceptance"], depends_on=DEPS)
def test_vacuous_tests_fail_the_gate_at_the_buggy_line(demo):
    """Tests that assert nothing useful fail the gate, pointing at the buggy boundary."""
    with given("the vacuous branch"):
        repo = demo("vacuous")
        boundary = line_of(repo, "src/pricing.py", "if qty > 100")
    with when("the reviewer runs the check"):
        code, data = _check(repo)
    with then("the gate fails", expected="exit 1"):
        assert code == 1
    with and_("the buggy boundary ran but was not checked", expected="ran-only + surviving-mutant"):
        assert _status(data, "src/pricing.py", boundary) == "ran-only"
        assert "surviving-mutant" in _kinds_at(data, "src/pricing.py", boundary)
    with and_("the vacuous assertions themselves are named"):
        assert any(f["kind"] == "vacuous-assertion" for f in data["findings"])


@scenario("accept-tautological-branch", tags=["acceptance", "oracle"], depends_on=DEPS)
def test_pasted_expectations_are_surfaced_even_though_every_mutant_dies(demo):
    """The case every existing tool misses: mutants die, but the expectations are unconfirmed copies.

    The pasted test kills the `>` -> `>=` mutant — which is the bug fix — so the
    boundary is honestly 'checked'. proofdeck must not stop there: it points the
    reviewer at the exact assertion that pins the bug.
    """
    with given("the tautological branch: `assert bulk_discount(100) == 5` pasted from buggy output"):
        repo = demo("tautological")
        boundary = line_of(repo, "src/pricing.py", "if qty > 100")
        pasted = line_of(repo, "tests/test_new.py", "bulk_discount(100) == 5")
    with when("the reviewer runs the check"):
        code, data = _check(repo)
    with then("the boundary counts as checked — mutation alone cannot see this bug", expected="checked"):
        assert _status(data, "src/pricing.py", boundary) == "checked"
    with and_("the pasted assertion is flagged unconfirmed", expected=f"tests/test_new.py:{pasted}"):
        assert "unconfirmed-expectation" in _kinds_at(data, "tests/test_new.py", pasted)


@scenario("accept-mockonly-branch", tags=["acceptance", "mocks"], depends_on=DEPS)
def test_mocked_away_code_is_not_reported_as_tested(demo):
    """Mock-only tests fail the gate: the real code never ran or was only checked through fakes."""
    with given("the mock-only branch"):
        repo = demo("mockonly")
        boundary = line_of(repo, "src/pricing.py", "if qty > 100")
        subtotal = line_of(repo, "src/pricing.py", "subtotal = apply_discount")
    with when("the reviewer runs the check"):
        code, data = _check(repo)
    with then("the gate fails", expected="exit 1"):
        assert code == 1
    with and_("bulk_discount never ran and order_total was only checked via mocks", expected="never-ran / mock-only"):
        assert _status(data, "src/pricing.py", boundary) == "never-ran"
        assert _status(data, "src/pricing.py", subtotal) == "mock-only"


@scenario("accept-selfref-branch", tags=["acceptance", "oracle"], depends_on=DEPS)
def test_code_checked_against_itself_fails_the_gate(demo):
    """Tests that compare the function with itself fail the gate and are named as recomputed."""
    with given("the self-referencing branch"):
        repo = demo("selfref")
        selfcmp = line_of(repo, "tests/test_new.py", "bulk_discount(100) == bulk_discount(100)")
    with when("the reviewer runs the check"):
        code, data = _check(repo)
    with then("the gate fails", expected="exit 1"):
        assert code == 1
    with and_("the self-comparison is flagged as a recomputed expectation", expected=f"tests/test_new.py:{selfcmp}"):
        assert "recomputed-expectation" in _kinds_at(data, "tests/test_new.py", selfcmp)
