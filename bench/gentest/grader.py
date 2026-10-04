"""Grade a generated test suite the only way that means anything: break the code and see
if the tests notice.

The published field cannot do this — "the tests pass" is the usual metric, and an audit
found 7.8% of test-passing patches simply wrong. proofdeck's mutation engine answers it
directly, so here it is used as the measuring instrument rather than the product.

Three numbers per suite:
  valid     does it pass against the CORRECT implementation (a suite that fails on good
            code is worse than no suite)
  caught    fraction of injected bugs that make at least one test fail  <- the headline
  vacuous   assertions that cannot fail, which inflate `caught` if left uncounted
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/workspaces/python/proofdeck")
from proofdeck.mutate import apply as apply_mutant  # noqa: E402
from proofdeck.mutate import mutants_for_line  # noqa: E402

REPO = Path("/workspaces/python/tapedeck")
IGNORE = shutil.ignore_patterns("__pycache__", ".git", ".specdeck", "*.egg-info", ".pytest_cache", "bench")


def sandbox() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gentest-"))
    dest = tmp / "repo"
    shutil.copytree(REPO, dest, ignore=IGNORE)
    return dest


def _pytest(tree: Path, target: str, timeout: int = 180, extra: list = ()) -> tuple:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:specdeck",
         "-p", "no:randomly", "--tb=no", "-rN", "--no-header", target, *extra],
        cwd=tree, capture_output=True, text=True, timeout=timeout,
        env={"PYTHONPATH": str(tree), "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")})
    import re
    passed = failed = errors = 0
    for n, what in re.findall(r"(\d+) (passed|failed|error)", p.stdout):
        if what == "passed":
            passed = int(n)
        elif what == "failed":
            failed = int(n)
        else:
            errors = int(n)
    return passed, failed, errors, p.stdout[-1500:]


def mutable_lines(module: str, cap: int = 30) -> list:
    """The lines proofdeck would mutate, with its first-choice mutant for each."""
    text = (REPO / module).read_text(encoding="utf-8")
    out = []
    for n in range(1, text.count("\n") + 2):
        ms = mutants_for_line(text, n, module)
        if ms:
            out.append((n, ms[0]))
    return out[:cap]


def _outcomes(tree: Path, target: str) -> dict:
    """Pass/fail per test node id."""
    import re

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:specdeck",
         "-p", "no:randomly", "--tb=no", "-rA", "--no-header", "-q", target],
        cwd=tree, capture_output=True, text=True, timeout=300,
        env={"PYTHONPATH": str(tree), "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")})
    out = {}
    for line in p.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR)\s+(\S+)", line)
        if m:
            out[m.group(2)] = m.group(1)
    return out


def _outcomes_with(tree: Path, target: str, extra: list) -> dict:
    import re

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:specdeck",
         "-p", "no:randomly", "--tb=no", "-rA", "--no-header", "-q", target, *extra],
        cwd=tree, capture_output=True, text=True, timeout=300,
        env={"PYTHONPATH": str(tree), "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")})
    out = {}
    for line in p.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR)\s+(\S+)", line)
        if m:
            out[m.group(2)] = m.group(1)
    return out


def grade(test_code: str, module: str, rel_test: str = "tests/test_generated.py", cap: int = 30) -> dict:
    """Write the suite into a clean copy, keep the tests that are actually correct, and
    measure how many injected bugs those catch.

    Dropping the wrong tests is what a developer would do, and refusing to grade a suite
    where 8 of 9 tests are right throws away the measurement. Both numbers are reported:
    how much of what it wrote was correct, and what the correct part is worth.
    """
    tree = sandbox()
    try:
        # the generated suite is the ONLY test file, so nothing else can carry it
        for p in list((tree / "tests").glob("test_*.py")):
            p.unlink()
        path = tree / rel_test
        path.write_text(test_code, encoding="utf-8")
        passed, failed, errors, tail = _pytest(tree, rel_test)
        collected = passed + failed + errors
        outcomes = _outcomes(tree, rel_test)
        wrong = [n for n, st in outcomes.items() if st != "PASSED"]
        deselect = [a for n in wrong for a in ("--deselect", n)]
        result = {"tests": collected, "passed": passed, "wrong": len(wrong),
                  "validity": round(passed / collected, 3) if collected else 0.0, "tail": tail}
        if not passed:
            result.update({"caught": 0.0, "killed": 0, "mutants": 0,
                           "note": "nothing to grade: no generated test passes on correct code"})
            return result
        # Prune until the kept subset is actually green. One pass is not enough: a module
        # that patches global state (netguard patches sockets) makes failures order-dependent,
        # so removing three tests can change which others fail.
        for _ in range(4):
            kp, kf, ke, _ = _pytest(tree, rel_test, extra=deselect)
            if not (kf or ke):
                break
            still = [n for n, st in _outcomes_with(tree, rel_test, deselect).items() if st != "PASSED"]
            if not still:
                break
            wrong += still
            deselect = [a for n in dict.fromkeys(wrong) for a in ("--deselect", n)]
        else:
            result.update({"caught": 0.0, "killed": 0, "mutants": 0,
                           "note": "could not reach a green subset in four passes"})
            return result
        result["wrong"] = len(dict.fromkeys(wrong))
        result["kept"] = kp
        original = (tree / module).read_text(encoding="utf-8")
        killed, kids = 0, set()
        muts = mutable_lines(module, cap)
        for line_no, m in muts:
            (tree / module).write_text(apply_mutant(original, m), encoding="utf-8")
            p2, f2, e2, _ = _pytest(tree, rel_test, extra=deselect)
            if f2 or e2:
                killed += 1
                kids.add(line_no)          # which mutants, so equivalent ones can be found
            (tree / module).write_text(original, encoding="utf-8")
        result.update({"killed": killed, "mutants": len(muts),
                       "caught": round(killed / len(muts), 3) if muts else 0.0,
                       "killed_ids": sorted(kids)})
        return result
    finally:
        shutil.rmtree(tree.parent, ignore_errors=True)
