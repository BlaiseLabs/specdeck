"""Fork mode: one pytest start for the whole check, same answers as one process per mutant."""
import os
import subprocess
import sys
import textwrap
import time

import pytest
from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind, LineStatus, analyze, runner
from proofdeck._forkrun import _order
from proofdeck.mutate import mutants_for_line
from proofdeck.schemata import build

from .conftest import BASE_SRC, FEATURE_SRC, commit, git, line_of, new_repo

needs_fork = pytest.mark.skipif(not runner.fork_supported(), reason="needs os.fork")


def _summary(report):
    return (dict(report.lines), sorted((f.kind, f.path, f.line) for f in report.findings))


@scenario(tags=["speed"], depends_on=["accept-vacuous-branch", "accept-mockonly-branch"])
@needs_fork
@pytest.mark.parametrize("variant", ["vacuous", "mockonly", "tautological"])
def test_fork_mode_gives_the_same_answers_as_one_process_per_mutant(demo, variant):
    """Fork mode reports exactly what one-process-per-mutant reports: every line status and every finding."""
    with given(f"the {variant} demo branch"):
        repo = demo(variant)
    with when("it is checked in both modes"):
        fork = analyze(str(repo), mode="fork")
        sub = analyze(str(repo), mode="subprocess")
    with then("line statuses and findings are identical", expected="same report"):
        assert _summary(fork) == _summary(sub)
    with and_("fork mode started pytest once; the other mode once per mutant, plus the trace"):
        assert fork.stats["mode"] == "fork" and fork.stats["pytest_starts"] == 1
        assert sub.stats["pytest_starts"] == 1 + sub.stats["mutants"] > 1


@scenario("schemata-behave-like-mutants", tags=["speed"])
def test_switched_code_behaves_like_the_mutant_and_keeps_line_numbers():
    """With a mutant's switch on, the compiled code behaves like that mutant; with none on, like the original."""
    with given("the buggy pricing code and the first mutant of every line"):
        source = BASE_SRC + FEATURE_SRC.format(op=">")
        cands = {n: mutants_for_line(source, n) for n in range(1, len(source.splitlines()) + 1)}
        cands = {n: ms for n, ms in cands.items() if ms}
    with when("they are all compiled in behind switches"):
        compiled, chosen = build(source, cands)
    with then("every line keeps its number", expected="same line count, unchanged lines identical"):
        assert len(compiled.splitlines()) == len(source.splitlines())
        for n, (a, b) in enumerate(zip(source.splitlines(), compiled.splitlines()), start=1):
            assert a == b or n in chosen
    with and_("with no switch on it is the original; with the boundary switch on it is the mutant",
              expected="bulk_discount(100): 5, then 15"):
        import builtins
        active = {"key": 0}
        builtins._proofdeck_m = lambda k: k == active["key"]
        try:
            ns = {}
            exec(compile(compiled, "<schemata>", "exec"), ns)
            assert ns["bulk_discount"](100) == 5
            boundary = line_of_text(source, "if qty > 100")
            active["key"] = chosen[boundary][0]
            assert ns["bulk_discount"](100) == 15
            stmt_line = line_of_text(source, "pct = bulk_discount(qty)")
            active["key"] = chosen[stmt_line][0]
            with pytest.raises(NameError):  # the statement is gone, like the `pass` mutant
                ns["order_total"](10, 1)
        finally:
            del builtins._proofdeck_m


def line_of_text(source, needle):
    return next(i for i, line in enumerate(source.splitlines(), start=1) if needle in line)


def _record_coverage(repo, style):
    env = {**os.environ, "COVERAGE_FILE": str(repo / ".coverage")}
    if style == "pytest-cov":
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:specdeck", "-p", "no:cacheprovider",
               "--cov=src", "--cov-context=test", "--cov-report="]
    elif style == "test_function":
        (repo / ".coveragerc").write_text("[run]\ndynamic_context = test_function\nsource = src\n")
        cmd = [sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q", "-p", "no:specdeck",
               "-p", "no:cacheprovider", "-p", "no:cov"]
    else:  # no per-test contexts
        cmd = [sys.executable, "-m", "coverage", "run", "--source=src", "-m", "pytest", "-q", "-p", "no:specdeck",
               "-p", "no:cacheprovider", "-p", "no:cov"]
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True)
    assert (repo / ".coverage").exists(), proc.stdout + proc.stderr
    return repo / ".coverage"


@scenario(tags=["speed", "coverage"], depends_on=["test_fork_mode_gives_the_same_answers_as_one_process_per_mutant"])
@needs_fork
@pytest.mark.parametrize("style", ["pytest-cov", "test_function"])
def test_per_test_coverage_from_ci_replaces_the_traced_run(demo, style):
    """A coverage.py file with per-test contexts from CI replaces proofdeck's own traced run, with the same answers."""
    pytest.importorskip("coverage")
    if style == "pytest-cov":
        pytest.importorskip("pytest_cov")
    with given(f"the vacuous demo branch and a coverage file recorded the {style} way"):
        repo = demo("vacuous")
        cov = _record_coverage(repo, style)
    with when("it is checked with the coverage file"):
        reused = analyze(str(repo), coverage=str(cov))
        traced = analyze(str(repo))
    with then("the suite wasn't traced again", expected="traced: False"):
        assert reused.stats["traced"] is False and traced.stats["traced"] is True
        assert any("used per-test coverage" in n for n in reused.notes)
    with and_("the answers are the same as tracing"):
        assert _summary(reused) == _summary(traced)


@scenario("coverage-file-without-contexts", tags=["speed", "coverage"], depends_on=["test_per_test_coverage_from_ci_replaces_the_traced_run"])
@needs_fork
def test_a_coverage_file_without_per_test_contexts_falls_back_to_tracing(demo):
    """A coverage file that doesn't say which test ran each line can't be used; proofdeck says so and traces."""
    pytest.importorskip("coverage")
    with given("a coverage file recorded without contexts"):
        repo = demo("vacuous")
        cov = _record_coverage(repo, "none")
    with when("it is checked with that file"):
        report = analyze(str(repo), coverage=str(cov))
    with then("it traced the tests instead and explained why", expected="note mentions contexts"):
        assert report.stats["traced"] is True
        assert any("no per-test contexts" in n for n in report.notes)
    with and_("the check still found the surviving mutant"):
        assert report.findings_of(FindingKind.SURVIVING_MUTANT)


@scenario("hanging-mutant-is-noticed", tags=["speed", "robustness"], depends_on=["test_fork_mode_gives_the_same_answers_as_one_process_per_mutant"])
@needs_fork
def test_a_mutant_that_makes_the_tests_hang_is_stopped_and_counts_as_noticed(tmp_path):
    """A mutant that turns a loop infinite is killed at its time limit, and the line counts as checked."""
    with given("a loop whose first mutant (`i += 2`) never reaches an odd target"):
        repo = new_repo(tmp_path / "r", {"pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
                                         "src/count.py": "def placeholder():\n    return 0\n"})
        git(repo, "checkout", "-qb", "feature")
        commit(repo, {
            "src/count.py": textwrap.dedent("""\
                def count_to(n):
                    i = 0
                    while i != n:
                        i += 1
                    return i
            """),
            "tests/test_count.py": "from count import count_to\n\ndef test_count():\n    assert count_to(3) == 3\n",
        }, "feature")
        step = line_of(repo, "src/count.py", "i += 1")
    with when("the branch is checked"):
        start = time.time()
        report = analyze(str(repo), mode="fork")
        took = time.time() - start
    with then("the hanging mutant was stopped and its line counts as checked", expected=LineStatus.CHECKED.value):
        assert report.status("src/count.py", step) == LineStatus.CHECKED
    with and_("the whole check still finished promptly", expected="under 30 s"):
        assert took < 30


@scenario("tests-ordered-fastest-first", tags=["speed"])
def test_tests_that_fake_nothing_run_first_fastest_first():
    """Each mutant tries tests that fake nothing before tests that do, fastest first, and stops at the first failure."""
    with given("four covering tests: two fake a collaborator, and they take different times"):
        durations = {"slow_real": 2.0, "fast_real": 0.1, "fast_fake": 0.05, "slow_fake": 1.0}
        faked = {"fast_fake": ["api"], "slow_fake": ["api"]}
    with then("real tests come first, each group fastest first",
              expected="fast_real, slow_real, fast_fake, slow_fake"):
        assert _order(list(durations), durations, faked) == ["fast_real", "slow_real", "fast_fake", "slow_fake"]


@scenario("no-fork-falls-back", tags=["speed"], depends_on=["test_fork_mode_gives_the_same_answers_as_one_process_per_mutant"])
def test_without_fork_it_runs_one_process_per_mutant(demo, monkeypatch):
    """On a system without fork (Windows), the check runs one pytest process per mutant instead."""
    with given("a system without os.fork"):
        monkeypatch.setattr(runner, "fork_supported", lambda: False)
        repo = demo("vacuous")
    with when("the branch is checked"):
        report = analyze(str(repo))
    with then("it used one process per mutant and still found the surviving mutant", expected="subprocess"):
        assert report.stats["mode"] == "subprocess"
        assert report.findings_of(FindingKind.SURVIVING_MUTANT)
