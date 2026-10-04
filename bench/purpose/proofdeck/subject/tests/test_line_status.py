"""Per changed line: checked, ran-only, never-ran or mock-only."""
from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind, LineStatus, analyze

from .conftest import line_of

PRICING = "src/pricing.py"


@scenario(
    "status-checked-when-a-test-notices",
    tags=["line-status", "mutation"],
    depends_on=["diff-changed-source-lines", "mutants-one-per-line-capped"],
)
def test_line_is_checked_when_its_mutant_is_killed(demo):
    """A changed line is CHECKED when breaking it makes a test fail."""
    with given("the good variant: correct code and spec-correct tests"):
        repo = demo("good")
        boundary = line_of(repo, PRICING, "if qty >= 100")
    with when("the branch is analysed"):
        report = analyze(str(repo))
    with then("the boundary line is checked", expected=LineStatus.CHECKED.value):
        assert report.status(PRICING, boundary) == LineStatus.CHECKED
    with and_("no mutant survives on any changed line"):
        assert report.findings_of(FindingKind.SURVIVING_MUTANT) == []


@scenario(
    "status-ran-only-when-a-mutant-survives",
    tags=["line-status", "mutation"],
    depends_on=["status-checked-when-a-test-notices"],
)
def test_line_is_ran_only_when_its_mutant_survives(demo):
    """A line the tests execute but never check is RAN_ONLY, with the surviving mutant attached."""
    with given("the vacuous variant: tests only assert the result is not None"):
        repo = demo("vacuous")
        boundary = line_of(repo, PRICING, "if qty > 100")
    with when("the branch is analysed"):
        report = analyze(str(repo))
    with then("the boundary line ran but was not checked", expected=LineStatus.RAN_ONLY.value):
        assert report.status(PRICING, boundary) == LineStatus.RAN_ONLY
    with and_("a surviving-mutant finding points at that line with the mutant diff", expected="if qty >= 100:"):
        found = [f for f in report.findings_of(FindingKind.SURVIVING_MUTANT) if f.line == boundary]
        assert found and found[0].mutant.mutated.strip() == "if qty >= 100:"


@scenario(
    "status-never-ran",
    tags=["line-status"],
    depends_on=["status-checked-when-a-test-notices"],
)
def test_line_is_never_ran_when_no_test_executes_it(demo):
    """Code whose function is mocked away in every test is NEVER_RAN — not reported as tested."""
    with given("the mock-only variant: bulk_discount is patched in every test that mentions it"):
        repo = demo("mockonly")
        boundary = line_of(repo, PRICING, "if qty > 100")
    with when("the branch is analysed"):
        report = analyze(str(repo))
    with then("bulk_discount's boundary line never ran", expected=LineStatus.NEVER_RAN.value):
        assert report.status(PRICING, boundary) == LineStatus.NEVER_RAN


@scenario(
    "status-mock-only",
    tags=["line-status", "mocks"],
    depends_on=["status-never-ran"],
)
def test_line_is_mock_only_when_checked_only_through_mocks(demo):
    """A line whose mutants are only killed by tests that mock its collaborators is MOCK_ONLY."""
    with given("the mock-only variant: order_total runs, but with bulk_discount and apply_discount faked"):
        repo = demo("mockonly")
        subtotal = line_of(repo, PRICING, "subtotal = apply_discount")
    with when("the branch is analysed"):
        report = analyze(str(repo))
    with then("the line is marked mock-only, not checked", expected=LineStatus.MOCK_ONLY.value):
        assert report.status(PRICING, subtotal) == LineStatus.MOCK_ONLY
    with and_("a mock-only finding explains which collaborators were faked"):
        found = [f for f in report.findings_of(FindingKind.MOCK_ONLY) if f.line == subtotal]
        assert found and "apply_discount" in found[0].message


@scenario(
    "status-arid-lines-not-reported",
    tags=["line-status", "noise"],
    depends_on=["status-checked-when-a-test-notices", "test_noise_lines_are_arid"],
)
def test_docstrings_on_changed_lines_get_no_status(demo):
    """Arid changed lines (e.g. the new docstrings) are left out rather than reported as unchecked."""
    with given("the good variant, whose new functions have docstrings"):
        repo = demo("good")
        doc = line_of(repo, PRICING, '"""Percent discount')
    with when("the branch is analysed"):
        report = analyze(str(repo))
    with then("the docstring line has no status", expected="None"):
        assert report.status(PRICING, doc) is None


@scenario("status-import-time", tags=["line-status", "fork"], depends_on=["status-checked-when-a-test-notices"])
def test_a_changed_import_time_line_is_not_reported_as_survived_by_fork_mode(tmp_path):
    """A module-level line has already run when the child forks, so its mutant's switch is never
    reached. Fork mode must say 'not checked', not 'survived'; subprocess mode checks it for real."""
    from .conftest import commit, new_repo

    with given("a branch that changes a module-level constant and the function using it"):
        repo = new_repo(tmp_path / "r", {
            "pyproject.toml": "[tool.proofdeck]\nworkers = 1\n",
            "src/limits.py": "LIMIT = 50\n\n\ndef over(x):\n    return x > 50\n",
            "tests/test_limits.py": "import sys; sys.path.insert(0, 'src')\nfrom limits import over\n\n"
                                    "def test_over():\n    assert over(101) and not over(100)\n",  # from the requirement
        })
        import subprocess
        subprocess.run(["git", "checkout", "-qb", "feature"], cwd=repo, check=True)
        commit(repo, {"src/limits.py": "LIMIT = 100\n\n\ndef over(x):\n    return x > LIMIT\n"}, "use the constant")
        const, fn = line_of(repo, "src/limits.py", "LIMIT = 100"), line_of(repo, "src/limits.py", "return x > LIMIT")
    with when("fork mode analyses it"):
        fork = analyze(str(repo), mode="fork")
    with then("the constant is import-time, not ran-only, and the function line is checked", expected="import-time / checked"):
        assert fork.status("src/limits.py", const) == LineStatus.IMPORT_TIME
        assert fork.status("src/limits.py", fn) == LineStatus.CHECKED
        assert not [f for f in fork.findings_of(FindingKind.SURVIVING_MUTANT) if f.line == const]
    with and_("the JSON flags the line as run at import, by every test"):
        from proofdeck.report import render_json
        row = next(r for r in render_json(fork)["lines"] if r["line"] == const)
        assert row["at_import"] and row["status"] == "import-time"
    with and_("subprocess mode, which starts a fresh process per mutant, checks it", expected="checked"):
        sub = analyze(str(repo), mode="subprocess")
        assert sub.status("src/limits.py", const) == LineStatus.CHECKED


@scenario("mocks-process-io-is-not-a-fake", tags=["mocks"], depends_on=["status-mock-only"])
def test_redirecting_stdin_or_argv_does_not_make_a_test_mock_only(tmp_path):
    """Feeding the code input through sys.stdin or sys.argv fakes no collaborator, so a test that
    does only that is trusted like any other; patching a real collaborator still counts."""
    from proofdeck.mocks import faked

    with given("three tests: one swaps stdin, one swaps argv via patch.object, one patches a function"):
        (tmp_path / "test_io.py").write_text(
            "import io, sys\nfrom unittest.mock import patch\n\n"
            "def test_stdin(monkeypatch):\n    monkeypatch.setattr('sys.stdin', io.StringIO('x'))\n\n"
            "def test_argv():\n    with patch.object(sys, 'argv', ['prog']):\n        pass\n\n"
            "def test_real(monkeypatch):\n    monkeypatch.setattr('sys.stdin', io.StringIO('x'))\n"
            "    monkeypatch.setattr('pricing.bulk_discount', lambda q: 5)\n")
    with then("the stream swaps fake nothing", expected="[] and []"):
        assert faked(tmp_path, "test_io.py::test_stdin") == [] and faked(tmp_path, "test_io.py::test_argv") == []
    with and_("the function patch is still a fake", expected="['bulk_discount']"):
        assert faked(tmp_path, "test_io.py::test_real") == ["bulk_discount"]
