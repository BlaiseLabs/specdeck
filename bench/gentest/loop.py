"""proofdeck in the loop: generate tests, inject bugs, tell the model which ones survived,
repeat.

The literature points here rather than at better prompts. EvalPlus expanded suites 80x with
mutation + differential testing and knocked up to 23 points off reported pass rates; UTBoost
flipped 28.4% of a leaderboard's "correct" patches by strengthening tests; TDFlow concluded
the bottleneck in tests-given work *is* test quality. A surviving mutant is the ideal signal:
an executable statement that no test distinguishes this code from correct code.

The guard that makes the result trustworthy: mutants are split in two. The FEEDBACK set is
shown to the model between rounds. The HELD-OUT set is never shown and is the only thing the
headline number is computed on — otherwise the model can write a test against the exact bug
it was handed and learn nothing general.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import grader
from proofdeck.mutate import apply as apply_mutant


def split_mutants(module: str, cap: int = 30) -> tuple:
    """Deterministic split over the mutants that are PROVABLY KILLABLE.

    Two reasons to exclude the rest. A mutant nobody has ever killed is probably semantically
    identical to the original, so counting it deflates every score — the denominator mistake
    this study already made once. And showing an unkillable mutant as "your tests missed this"
    sends the model after a test that cannot exist.

    Line numbers are the ids, so scores already recorded for earlier arms stay comparable.
    """
    import json

    killable = set(json.loads(Path(__file__).with_name("baseline.json").read_text())["killable"][module])
    muts = [(line, m) for line, m in grader.mutable_lines(module, cap) if line in killable]
    feedback, held = [], []
    for i, pair in enumerate(muts):
        (feedback if (i % 5) < 3 else held).append(pair)   # 3 of every 5 -> feedback
    return feedback, held


def survivors(tree: Path, module: str, rel_test: str, deselect: list, muts: list) -> list:
    """Which of these mutants the current suite fails to notice."""
    original = (tree / module).read_text(encoding="utf-8")
    out = []
    for line_no, m in muts:
        (tree / module).write_text(apply_mutant(original, m), encoding="utf-8")
        p, f, e, _ = grader._pytest(tree, rel_test, extra=deselect)
        if not (f or e):
            out.append((line_no, m))
        (tree / module).write_text(original, encoding="utf-8")
    return out


def feedback_text(module: str, surv: list) -> str:
    lines = [f"Your tests do not notice these {len(surv)} changes to `{module}`. Each one is a "
             f"real bug: with it applied, your whole suite still passes.", ""]
    for line_no, m in surv:
        lines.append(f"  line {line_no}:")
        lines.append(f"    - {m.original.strip()}")
        lines.append(f"    + {m.mutated.strip()}")
    lines += ["", "Add or strengthen tests so that each of these changes makes a test fail. Do not "
              "write a test that merely restates the change — test the behaviour the correct code "
              "has and the changed code does not, so that other bugs in the same place are caught "
              "too. Return the complete test file."]
    return "\n".join(lines)


def grade_on(test_code: str, module: str, muts: list, rel_test: str = "tests/test_generated.py") -> dict:
    """Validity, then how many of `muts` the correct-and-passing subset kills."""
    tree = grader.sandbox()
    try:
        for p in list((tree / "tests").glob("test_*.py")):
            p.unlink()
        (tree / rel_test).write_text(test_code, encoding="utf-8")
        passed, failed, errors, tail = grader._pytest(tree, rel_test)
        collected = passed + failed + errors
        wrong = [n for n, st in grader._outcomes(tree, rel_test).items() if st != "PASSED"]
        deselect = [a for n in wrong for a in ("--deselect", n)]
        for _ in range(4):
            kp, kf, ke, _ = grader._pytest(tree, rel_test, extra=deselect)
            if not (kf or ke):
                break
            still = [n for n, st in grader._outcomes_with(tree, rel_test, deselect).items() if st != "PASSED"]
            if not still:
                break
            wrong += still
            deselect = [a for n in dict.fromkeys(wrong) for a in ("--deselect", n)]
        else:
            return {"tests": collected, "passed": passed, "wrong": len(wrong), "killed": 0,
                    "mutants": len(muts), "caught": 0.0, "note": "no green subset"}
        original = (tree / module).read_text(encoding="utf-8")
        kids = []
        for line_no, m in muts:
            (tree / module).write_text(apply_mutant(original, m), encoding="utf-8")
            p2, f2, e2, _ = grader._pytest(tree, rel_test, extra=deselect)
            if f2 or e2:
                kids.append(line_no)
            (tree / module).write_text(original, encoding="utf-8")
        return {"tests": collected, "passed": passed, "wrong": len(dict.fromkeys(wrong)),
                "killed": len(kids), "mutants": len(muts), "killed_ids": sorted(kids),
                "caught": round(len(kids) / len(muts), 3) if muts else 0.0,
                "validity": round(passed / collected, 3) if collected else 0.0}
    finally:
        shutil.rmtree(tree.parent, ignore_errors=True)


def survivors_for(test_code: str, module: str, muts: list,
                  rel_test: str = "tests/test_generated.py") -> list:
    """The feedback-set mutants the current suite misses, for showing to the model."""
    tree = grader.sandbox()
    try:
        for p in list((tree / "tests").glob("test_*.py")):
            p.unlink()
        (tree / rel_test).write_text(test_code, encoding="utf-8")
        wrong = [n for n, st in grader._outcomes(tree, rel_test).items() if st != "PASSED"]
        deselect = [a for n in wrong for a in ("--deselect", n)]
        for _ in range(4):
            kp, kf, ke, _ = grader._pytest(tree, rel_test, extra=deselect)
            if not (kf or ke):
                break
            still = [n for n, st in grader._outcomes_with(tree, rel_test, deselect).items() if st != "PASSED"]
            if not still:
                break
            wrong += still
            deselect = [a for n in dict.fromkeys(wrong) for a in ("--deselect", n)]
        return survivors(tree, module, rel_test, deselect, muts)
    finally:
        shutil.rmtree(tree.parent, ignore_errors=True)


def append_tests(existing: str, added: str) -> str:
    """Glue new tests onto the file without letting them clobber what already passes.

    Duplicate test names are renamed rather than dropped: a collision would silently
    shadow a working test, which is the exact damage appending is meant to avoid.
    """
    import ast as _ast
    import re as _re

    try:
        have = {n.name for n in _ast.walk(_ast.parse(existing))
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
    except SyntaxError:
        have = set()
    out = added
    try:
        for node in _ast.walk(_ast.parse(added)):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name in have:
                out = _re.sub(rf"\b{node.name}\b", node.name + "_added", out)
    except SyntaxError:
        pass
    return existing.rstrip() + "\n\n\n# --- added after mutation feedback ---\n" + out.strip() + "\n"
