"""Finding the lines a branch changed — the only lines proofdeck ever mutates."""
from specdeck import and_, given, scenario, then, when

from proofdeck.diff import changed_lines

from .conftest import commit, git, line_of, new_repo


@scenario("diff-changed-source-lines", tags=["diff"])
def test_reports_added_and_modified_source_lines(tmp_path):
    """Lines added or modified on the branch are reported per file; blank lines are left out."""
    with given("a repo whose feature branch edits one line and adds two"):
        repo = new_repo(tmp_path / "r", {"src/calc.py": "def f(x):\n    return x + 1\n"})
        git(repo, "checkout", "-qb", "feature")
        commit(repo, {"src/calc.py": "def f(x):\n    return x + 2\n\ndef g(y):\n    return y * 3\n"}, "edit")
    with when("changed lines are computed against main"):
        changed = changed_lines(str(repo), base="main")
    with then("the edited line and the new non-blank lines are reported", expected="src/calc.py: {2, 4, 5}"):
        assert changed == {"src/calc.py": {2, 4, 5}}


@scenario("diff-excludes-tests", tags=["diff"], depends_on=["diff-changed-source-lines"])
def test_test_files_are_not_mutation_targets(demo):
    """Test files are never mutation targets, even though the branch changed them."""
    with given("the demo branch, which adds both source and a test file"):
        repo = demo("good")
    with when("changed lines are computed"):
        changed = changed_lines(str(repo), base="main")
    with then("only the source file appears", expected="src/pricing.py only"):
        assert set(changed) == {"src/pricing.py"}
    with and_("the new bulk_discount boundary line is among the changes"):
        assert line_of(repo, "src/pricing.py", "if qty >= 100") in changed["src/pricing.py"]


@scenario("diff-unchanged-lines-ignored", tags=["diff"], depends_on=["diff-changed-source-lines"])
def test_untouched_code_is_ignored(demo):
    """Code that already existed on main is not re-checked."""
    with given("the demo branch, which leaves apply_discount untouched"):
        repo = demo("good")
    with when("changed lines are computed"):
        changed = changed_lines(str(repo), base="main")["src/pricing.py"]
    with then("no line of apply_discount is included", expected="lines 1-5 absent"):
        assert not changed & {1, 2, 3, 4, 5}
