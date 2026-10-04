"""(a) Where did the expected answer come from?

Mutation testing cannot tell a correct test from one whose expected values were
pasted from buggy output — both kill the same mutants. proofdeck doesn't claim
to either. It marks every expectation that was born on the branch alongside the
code it checks as *unconfirmed*, so a human confirms it against the requirement.
"""
from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind
from proofdeck.provenance import unconfirmed_expectations

from .conftest import commit, git, line_of, new_repo, src


@scenario("provenance-copied-output-flagged", tags=["provenance", "oracle"], depends_on=["diff-changed-source-lines"])
def test_expectation_born_with_the_code_is_unconfirmed(demo):
    """The pasted-from-buggy-output assertion is flagged as unconfirmed."""
    with given("the tautological variant: `assert bulk_discount(100) == 5` was copied from the buggy code's output"):
        repo = demo("tautological")
        pasted = line_of(repo, "tests/test_new.py", "bulk_discount(100) == 5")
    with when("unconfirmed expectations are found"):
        found = unconfirmed_expectations(str(repo))
    with then("that assertion is flagged unconfirmed", expected=FindingKind.UNCONFIRMED_EXPECTATION.value):
        assert any(f.path == "tests/test_new.py" and f.line == pasted for f in found)
    with and_("the message asks for the value to be checked against the requirement, not the code"):
        msg = next(f.message for f in found if f.line == pasted)
        assert "requirement" in msg.lower()


@scenario(
    "provenance-honest-about-its-limit",
    tags=["provenance", "oracle", "limitation"],
    depends_on=["provenance-copied-output-flagged"],
)
def test_correct_new_expectations_are_also_unconfirmed(demo):
    """A correct new test is ALSO unconfirmed: without an independent source the two look the same."""
    with given("the good variant: the new expectation 15 is correct, but was also written on this branch"):
        repo = demo("good")
        correct = line_of(repo, "tests/test_new.py", "bulk_discount(100) == 15")
    with when("unconfirmed expectations are found"):
        found = unconfirmed_expectations(str(repo))
    with then("it is flagged too — proofdeck does not pretend to know which is right", expected="flagged"):
        assert any(f.path == "tests/test_new.py" and f.line == correct for f in found)


@scenario("provenance-expectation-from-main-confirmed", tags=["provenance"], depends_on=["provenance-copied-output-flagged"])
def test_expectation_that_existed_on_main_is_confirmed(tmp_path):
    """An assertion that already existed on main is an independent check of changed code."""
    with given("main has a test for f; the branch rewrites f without touching the test"):
        repo = new_repo(tmp_path / "r", {
            "src/m.py": "def f(x):\n    return x * 2\n",
            "tests/test_m.py": "from m import f\n\ndef test_f():\n    assert f(3) == 6\n",
        })
        git(repo, "checkout", "-qb", "feature")
        commit(repo, {"src/m.py": "def f(x):\n    return x + x\n"}, "refactor")
    with when("unconfirmed expectations are found"):
        found = unconfirmed_expectations(str(repo))
    with then("nothing is flagged", expected="[]"):
        assert found == []


@scenario("provenance-fixture-from-main-confirmed", tags=["provenance"], depends_on=["provenance-expectation-from-main-confirmed"])
def test_expectation_loaded_from_a_preexisting_data_file_is_confirmed(tmp_path):
    """Expected values read from a data file that existed on main count as independent."""
    with given("a cases file committed on main, and a new test on the branch that reads its expectations from it"):
        repo = new_repo(tmp_path / "r", {
            "src/m.py": "def f(x):\n    return x\n",
            "tests/cases.json": '[[3, 6], [4, 8]]\n',
        })
        git(repo, "checkout", "-qb", "feature")
        commit(repo, {
            "src/m.py": "def f(x):\n    return x * 2\n",
            "tests/test_m.py": src("""
                import json, pathlib
                from m import f

                CASES = json.loads((pathlib.Path(__file__).parent / "cases.json").read_text())

                def test_f():
                    for given, expected in CASES:
                        assert f(given) == expected
            """),
        }, "feature")
    with when("unconfirmed expectations are found"):
        found = unconfirmed_expectations(str(repo))
    with then("nothing is flagged", expected="[]"):
        assert found == []


@scenario("provenance-branch-markers-dont-confirm", tags=["provenance", "tampering"], depends_on=["provenance-copied-output-flagged"])
def test_a_confirmation_comment_written_on_the_branch_does_not_count(demo):
    """Nothing written on the branch can confirm an expectation — an agent could write it."""
    with given("the tautological variant, with a 'confirmed' comment added beside the pasted value"):
        repo = demo("tautological")
        test = (repo / "tests/test_new.py").read_text().replace(
            "assert bulk_discount(100) == 5", "assert bulk_discount(100) == 5  # confirmed against spec"
        )
        commit(repo, {"tests/test_new.py": test}, "mark as confirmed")
        pasted = line_of(repo, "tests/test_new.py", "bulk_discount(100) == 5")
    with when("unconfirmed expectations are found"):
        found = unconfirmed_expectations(str(repo))
    with then("the assertion is still unconfirmed", expected="flagged"):
        assert any(f.line == pasted for f in found)
