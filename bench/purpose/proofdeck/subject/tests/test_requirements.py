"""Requirements as the independent answer: approved entries confirm or contradict expected values.

An entry is approved when it's on the base branch (a person reviewed it when it
was merged). Entries added on the branch are drafts.
"""
from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind, analyze
from proofdeck.ledger import load
from proofdeck.provenance import against_requirements, unconfirmed_expectations
from proofdeck.report import exit_code

from .conftest import BASE_SRC, FEATURE_SRC, PYPROJECT, commit, git, line_of, new_repo

REQS = """\
requirements:
  - id: pricing.bulk-tier
    says: Orders of 100 units or more get 15% off; 10 or more get 5%.
    examples:
      - given: {qty: 100}
        expect: 15
      - given: {qty: 99}
        expect: 5
"""


def _repo(tmp_path, *, reqs_on_main=REQS, reqs_on_branch=None, test_body, op=">", extra=None):
    files = {"pyproject.toml": PYPROJECT, "src/pricing.py": BASE_SRC, "tests/test_base.py": "def test_ok():\n    pass\n"}
    if reqs_on_main is not None:
        files["requirements.yaml"] = reqs_on_main
    repo = new_repo(tmp_path / "r", files)
    git(repo, "checkout", "-qb", "feature")
    changes = {"src/pricing.py": BASE_SRC + FEATURE_SRC.format(op=op),
               "tests/test_new.py": "from specdeck import scenario, example\nfrom pricing import bulk_discount\n\n"
                                    + test_body}
    if reqs_on_branch is not None:
        changes["requirements.yaml"] = reqs_on_branch
    changes.update(extra or {})
    commit(repo, changes, "feature")
    return repo


def _kinds(findings, line=None):
    return {f.kind for f in findings if line is None or f.line == line}


@scenario("req-contradiction", tags=["requirements"], depends_on=["provenance-copied-output-flagged"])
def test_a_linked_test_that_disagrees_with_an_approved_example_fails(tmp_path):
    """A test that claims a requirement but expects something else than its approved example fails the gate."""
    with given("an approved requirement (100 units -> 15) and a linked test pasted from buggy output (== 5)"):
        repo = _repo(tmp_path, test_body='@scenario(covers=["pricing.bulk-tier"])\n'
                                         'def test_tier():\n    assert bulk_discount(100) == 5\n')
        pasted = line_of(repo, "tests/test_new.py", "== 5")
    with when("the branch is checked"):
        found = against_requirements(str(repo))
        report = analyze(str(repo))
    with then("the assertion is reported as contradicting the requirement", expected="contradicts-requirement"):
        assert [(f.kind, f.line) for f in found] == [(FindingKind.CONTRADICTS_REQUIREMENT, pasted)]
        assert "15" in found[0].message and "pricing.bulk-tier" in found[0].message
    with and_("the gate fails, even though every mutant on the boundary line is killed"):
        assert exit_code(report) == 1
        assert FindingKind.CONTRADICTS_REQUIREMENT in _kinds(report.findings, pasted)


@scenario("req-confirms", tags=["requirements"], depends_on=["req-contradiction"])
def test_an_expected_value_that_agrees_with_an_approved_example_is_confirmed(tmp_path):
    """An expected value taken from, or equal to, an approved example counts as confirmed."""
    with given("correct code, and two linked assertions: a literal 15 and example(...)"):
        repo = _repo(tmp_path, op=">=", test_body=(
            '@scenario(covers=["pricing.bulk-tier"])\ndef test_literal():\n    assert bulk_discount(100) == 15\n\n'
            'def test_from_requirement():\n    assert bulk_discount(99) == example("pricing.bulk-tier", qty=99)\n'))
    with when("unconfirmed expectations are listed"):
        found = unconfirmed_expectations(str(repo))
    with then("neither assertion is listed", expected="[]"):
        assert found == []
    with and_("nothing contradicts the requirement"):
        assert against_requirements(str(repo)) == []


@scenario("req-only-linked-tests", tags=["requirements"], depends_on=["req-contradiction"])
def test_a_test_that_doesnt_claim_the_requirement_is_not_judged_by_it(tmp_path):
    """Only tests that claim a requirement are compared with it, so matching argument values elsewhere don't count."""
    with given("the same pasted assertion, but in a test with no covers= and no example()"):
        repo = _repo(tmp_path, test_body="def test_tier():\n    assert bulk_discount(100) == 5\n")
        pasted = line_of(repo, "tests/test_new.py", "== 5")
    with then("it's not called a contradiction; it stays an unconfirmed expectation", expected="unconfirmed only"):
        assert against_requirements(str(repo)) == []
        assert _kinds(unconfirmed_expectations(str(repo)), pasted) == {FindingKind.UNCONFIRMED_EXPECTATION}


@scenario("req-draft", tags=["requirements"], depends_on=["req-confirms"])
def test_a_requirement_added_on_the_branch_is_a_draft(tmp_path):
    """A requirement written on the branch is listed for review; expected values from it stay unconfirmed."""
    with given("no requirements on main; the branch adds one and a test takes its expected value from it"):
        repo = _repo(tmp_path, op=">=", reqs_on_main=None, reqs_on_branch=REQS, test_body=(
            'def test_tier():\n    assert bulk_discount(100) == example("pricing.bulk-tier", qty=100)\n'))
        line = line_of(repo, "tests/test_new.py", "example(")
    with when("the branch is checked"):
        report = analyze(str(repo))
    with then("the new requirement is listed for review", expected="draft-requirement"):
        drafts = report.findings_of(FindingKind.DRAFT_REQUIREMENT)
        assert [f.path for f in drafts] == ["requirements.yaml"] and "Orders of 100 units" in drafts[0].message
    with and_("the expected value taken from it is unconfirmed until the requirement is merged"):
        f = [f for f in report.findings_of(FindingKind.UNCONFIRMED_EXPECTATION) if f.line == line]
        assert f and "added on this branch" in f[0].message
    with and_("drafts are for the reviewer: on their own they don't fail the gate", expected="exit 0"):
        from proofdeck import Report
        advisory = drafts + report.findings_of(FindingKind.UNCONFIRMED_EXPECTATION)
        assert exit_code(Report(findings=advisory)) == 0


@scenario("req-changed", tags=["requirements", "tampering"], depends_on=["req-contradiction"])
def test_changing_an_approved_requirement_on_the_branch_fails(tmp_path):
    """Editing an approved example on the same branch as the code fails the gate, and doesn't confirm anything."""
    with given("the branch changes the approved example (100 -> 5) to match its buggy code"):
        repo = _repo(tmp_path, reqs_on_branch=REQS.replace("expect: 15", "expect: 5"), test_body=(
            'def test_tier():\n    assert bulk_discount(100) == example("pricing.bulk-tier", qty=100)\n'))
        line = line_of(repo, "tests/test_new.py", "example(")
    with when("the branch is checked"):
        report = analyze(str(repo))
    with then("the change is reported and the gate fails", expected="requirement-changed, exit 1"):
        changed = report.findings_of(FindingKind.REQUIREMENT_CHANGED)
        assert changed and "given different examples" in changed[0].message
        assert exit_code(report) == 1
    with and_("the test's expected value, read from the edited file, is not treated as confirmed"):
        f = [f for f in report.findings_of(FindingKind.UNCONFIRMED_EXPECTATION) if f.line == line]
        assert f and "examples were changed" in f[0].message


@scenario("req-deleted", tags=["requirements", "tampering"], depends_on=["req-changed"])
def test_deleting_an_approved_requirement_is_reported(tmp_path):
    """Deleting an approved requirement on the branch is a change like any other."""
    with given("the branch deletes the requirements file's only entry"):
        repo = _repo(tmp_path, op=">=", reqs_on_branch="requirements: []\n",
                     test_body="def test_tier():\n    assert bulk_discount(100) == 15\n")
    with then("it's reported as deleted", expected="requirement-changed: deleted"):
        from proofdeck.ledger import entry_findings
        found = entry_findings(load(str(repo)))
        assert [f.kind for f in found] == [FindingKind.REQUIREMENT_CHANGED] and "deleted" in found[0].message


@scenario("req-file-from-base-config", tags=["requirements"], depends_on=["tamper-config-read-from-main"])
def test_the_requirements_file_location_comes_from_the_base_branch(tmp_path):
    """specdeck_requirements in the base branch's pytest config says where the file is; the branch can't move it."""
    with given("main keeps requirements in docs/requirements.yaml; the branch points the setting elsewhere"):
        repo = new_repo(tmp_path / "r", {
            "pyproject.toml": PYPROJECT + 'specdeck_requirements = "docs/requirements.yaml"\n',
            "docs/requirements.yaml": REQS, "src/pricing.py": BASE_SRC})
        git(repo, "checkout", "-qb", "feature")
        commit(repo, {"pyproject.toml": PYPROJECT + 'specdeck_requirements = "other.yaml"\n',
                      "other.yaml": "requirements: []\n"}, "move it")
    with when("the requirements are loaded"):
        ledger = load(str(repo))
    with then("they come from docs/requirements.yaml, as configured on main", expected="docs/requirements.yaml"):
        assert ledger.path == "docs/requirements.yaml" and "pricing.bulk-tier" in ledger.approved
