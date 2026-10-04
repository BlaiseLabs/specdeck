"""(e) Weakening the tests or the checker, and why config is read from main."""
from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind, analyze
from proofdeck.tampering import scan

from .conftest import commit, line_of


def _kinds(found):
    return {f.kind for f in found}


@scenario("tamper-skip-added", tags=["tampering"], depends_on=["diff-changed-source-lines"])
def test_added_skip_marker_is_a_weakened_test(demo):
    """Skipping a previously running test on the branch is flagged."""
    with given("the good variant, then a commit that skips the tier test"):
        repo = demo("good")
        text = (repo / "tests/test_new.py").read_text().replace(
            "def test_bulk_discount_tiers", "import pytest\n\n@pytest.mark.skip\ndef test_bulk_discount_tiers"
        )
        commit(repo, {"tests/test_new.py": text}, "skip flaky test")
    with when("the branch is scanned for tampering"):
        found = scan(str(repo))
    with then("a weakened-test finding is raised", expected=FindingKind.WEAKENED_TEST.value):
        assert FindingKind.WEAKENED_TEST in _kinds(found)


@scenario("tamper-assertion-removed", tags=["tampering"], depends_on=["diff-changed-source-lines"])
def test_removed_assertion_on_main_is_a_weakened_test(demo):
    """Deleting an assertion that existed on main is flagged."""
    with given("a branch that deletes one of main's apply_discount assertions"):
        repo = demo("good")
        text = (repo / "tests/test_base.py").read_text().replace("    assert apply_discount(50, 100) == 0.0\n", "")
        commit(repo, {"tests/test_base.py": text}, "drop assertion")
    with when("the branch is scanned"):
        found = scan(str(repo))
    with then("a weakened-test finding points at test_base.py", expected="tests/test_base.py"):
        assert any(f.kind == FindingKind.WEAKENED_TEST and f.path == "tests/test_base.py" for f in found)


@scenario("tamper-pragma-no-mutate", tags=["tampering"], depends_on=["diff-changed-source-lines"])
def test_new_pragma_no_mutate_is_checker_tampering(demo):
    """A `pragma: no mutate` added on the branch is flagged — it hides a line from the checker."""
    with given("the vacuous variant with `# pragma: no mutate` added to the buggy boundary"):
        repo = demo("vacuous")
        src_text = (repo / "src/pricing.py").read_text().replace(
            "if qty > 100:", "if qty > 100:  # pragma: no mutate"
        )
        commit(repo, {"src/pricing.py": src_text}, "silence mutant")
        boundary = line_of(repo, "src/pricing.py", "if qty > 100")
    with when("the branch is scanned"):
        found = scan(str(repo))
    with then("checker tampering is flagged on that line", expected=FindingKind.CHECKER_TAMPERING.value):
        assert any(f.kind == FindingKind.CHECKER_TAMPERING and f.line == boundary for f in found)


@scenario("tamper-config-edit", tags=["tampering"], depends_on=["diff-changed-source-lines"])
def test_editing_proofdeck_config_is_checker_tampering(demo):
    """Excluding files via `[tool.proofdeck]` on the branch is flagged."""
    with given("a branch that adds an exclude for the file under test"):
        repo = demo("vacuous")
        py = (repo / "pyproject.toml").read_text() + '\n[tool.proofdeck]\nexclude = ["src/pricing.py"]\n'
        commit(repo, {"pyproject.toml": py}, "exclude pricing")
    with when("the branch is scanned"):
        found = scan(str(repo))
    with then("checker tampering is flagged on pyproject.toml", expected="pyproject.toml"):
        assert any(f.kind == FindingKind.CHECKER_TAMPERING and f.path == "pyproject.toml" for f in found)


@scenario(
    "tamper-config-read-from-main",
    tags=["tampering"],
    depends_on=["tamper-config-edit", "status-ran-only-when-a-mutant-survives"],
)
def test_branch_config_cannot_exclude_its_own_code(demo):
    """Config is read from main, so a branch that excludes its own file is still analysed."""
    with given("the vacuous variant whose branch excludes src/pricing.py in [tool.proofdeck]"):
        repo = demo("vacuous")
        py = (repo / "pyproject.toml").read_text() + '\n[tool.proofdeck]\nexclude = ["src/pricing.py"]\n'
        commit(repo, {"pyproject.toml": py}, "exclude pricing")
        boundary = line_of(repo, "src/pricing.py", "if qty > 100")
    with when("the branch is analysed"):
        report = analyze(str(repo))
    with then("the buggy boundary is still reported", expected="surviving mutant on the boundary line"):
        assert any(f.line == boundary for f in report.findings_of(FindingKind.SURVIVING_MUTANT))
    with and_("the exclusion itself is reported as tampering"):
        assert report.findings_of(FindingKind.CHECKER_TAMPERING)


@scenario("tamper-clean-branch", tags=["tampering"], depends_on=["tamper-skip-added"])
def test_ordinary_branch_has_no_tampering_findings(demo):
    """A branch that only adds code and tests produces no tampering findings."""
    with given("the good variant, unchanged"):
        repo = demo("good")
    with then("scan finds nothing", expected="[]"):
        assert scan(str(repo)) == []
