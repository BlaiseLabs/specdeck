"""Prepare the subject: a working repo with three implementations removed.

The task is Commit0-shaped (arXiv:2412.01769): the tests are the specification and the
agent must write the code that satisfies them. Published state of the art on that
benchmark is 6-29% of tests passing, so this is deliberately scaled down to three
modules rather than a whole library — enough that an agent can make real progress and
the arms can separate.

Two test files are held out and never shown to any arm. Visible-test pass rate is
gameable (iterating against visible tests raises overfitting by up to 35.9%); the
held-out rate is the honest number, and the gap between them is the gaming signal.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path("/workspaces/python/tapedeck")
# The whole library: 13 modules, 1,621 lines. Three modules was saturated — one call
# reimplemented them perfectly from the tests, with or without docstrings — so there was
# nothing for a distribution strategy to improve on.
STAGES = [
    ["tapedeck/engine/serialize.py", "tapedeck/engine/recorder.py"],
    ["tapedeck/engine/tape.py"],
]
TARGETS = [m for stage in STAGES for m in stage]
# Held out: three files whose tests all FAIL at the start, so they measure real progress
# rather than what already passed. 7 of the 32 earnable tests (22%).
HELD_OUT = ["tests/test_stream_roundtrip.py", "tests/test_telegram_shape_tap.py",
            "tests/test_registry.py"]
# These already pass with the stubs in place: the regression set. Breaking one is a
# failure even if everything else passes ("fixed it by deleting the feature").
REGRESSION = ["tests/test_drift.py", "tests/test_dictlike_responses.py"]
IGNORE = shutil.ignore_patterns("__pycache__", ".git", ".specdeck", "*.egg-info", ".pytest_cache", "bench")


DETAIL = "bare"   # "documented" keeps the docstrings, "bare" strips them


def stub_bodies(path: Path, detail: str = None) -> str:
    """Replace every function body with `raise NotImplementedError`, keeping the signature,
    imports and module-level code — so the module still imports cleanly.

    `detail` mirrors the manipulation in "The Specification Gap" (arXiv:2603.24284), which
    stripped spec detail from full docstrings (L0) to bare signatures (L3) and watched
    single-agent success fall 89% -> 56%. With docstrings kept, one call reimplemented all
    three of these modules perfectly in 32k tokens — no headroom to measure anything in.
    """
    detail = detail or DETAIL
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    def outermost(scope):
        """Functions directly in a module or class body. A function nested inside another
        disappears with its parent's body, so collecting it too makes overlapping edits."""
        found = []
        for node in scope.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append(node)
            elif isinstance(node, ast.ClassDef):
                found += outermost(node)
        return found

    def abstract(node) -> bool:
        """Already a declaration: body is just `...`, often on the signature's own line.
        Interface files (Protocols, stateless Abilities) are entirely this, and there is
        nothing in them to implement."""
        return (len(node.body) == 1 and isinstance(node.body[0], ast.Expr)
                and isinstance(getattr(node.body[0], "value", None), ast.Constant)
                and node.body[0].value.value is Ellipsis) or node.body[0].lineno == node.lineno

    edits = []
    for node in outermost(ast.parse(src)):
        if abstract(node):
            continue
        body = node.body
        has_doc = (len(body) > 1 and isinstance(body[0], ast.Expr)
                   and isinstance(getattr(body[0], "value", None), ast.Constant)
                   and isinstance(body[0].value.value, str))
        first = body[1] if (has_doc and detail == "documented") else body[0]
        edits.append((first.lineno - 1, body[-1].end_lineno, " " * first.col_offset + "raise NotImplementedError\n"))
    for start, end, text in sorted(edits, reverse=True):
        lines[start:end] = [text]
    return "".join(lines)


def build(dest: Path) -> dict:
    """A fresh copy with the targets stubbed. Returns the pristine text of everything
    the grader must be able to restore."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SOURCE, dest, ignore=IGNORE)
    pristine = {"tests": {}, "reference": {}, "stubs": {}}
    for p in sorted((dest / "tests").rglob("*.py")):
        pristine["tests"][str(p.relative_to(dest))] = p.read_text(encoding="utf-8")
    for rel in TARGETS:
        reference = (SOURCE / rel).read_text(encoding="utf-8")
        stub = stub_bodies(dest / rel)
        if stub == reference:      # an interface file: nothing to implement, leave it alone
            continue
        pristine["reference"][rel] = reference
        if DETAIL == "bare":                   # also drop the module docstring
            tree = ast.parse(stub)
            if (tree.body and isinstance(tree.body[0], ast.Expr)
                    and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
                    and isinstance(tree.body[0].value.value, str)):
                ls = stub.splitlines(keepends=True)
                del ls[tree.body[0].lineno - 1:tree.body[0].end_lineno]
                stub = "".join(ls)
        pristine["stubs"][rel] = stub
        (dest / rel).write_text(stub, encoding="utf-8")
    for rel in HELD_OUT:                      # the agent must not see these at all
        (dest / rel).unlink()
    return pristine


def restore_tests(tree: Path, pristine: dict) -> None:
    """Put every test file back exactly as it was, including the held-out ones.

    This is the guard that neutralises test edits, added skips and conftest hijacks in
    one move — a ten-line conftest can otherwise report every test as passing.
    """
    for p in list((tree / "tests").rglob("*.py")):
        p.unlink()
    for rel, text in pristine["tests"].items():
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for extra in ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg"):
        p = tree / extra
        if p.exists() and extra not in pristine["tests"]:
            p.unlink()


def run_tests(tree: Path, only: list = None) -> dict:
    """Outcomes per test, from a run that cannot be influenced by anything the agent wrote
    into the test tree (it has just been restored)."""
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:specdeck",
            "-p", "no:randomly", "--tb=no", "-rN", "--no-header"]
    if only:
        args += only
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600,
                       env={"PYTHONPATH": str(tree), "PATH": __import__("os").environ["PATH"],
                            "HOME": __import__("os").environ.get("HOME", "/tmp")})
    passed = failed = errors = 0
    for line in p.stdout.splitlines():
        if " passed" in line or " failed" in line or " error" in line:
            import re
            for n, what in re.findall(r"(\d+) (passed|failed|error|errors)", line):
                if what == "passed":
                    passed = int(n)
                elif what == "failed":
                    failed = int(n)
                else:
                    errors = int(n)
    return {"passed": passed, "failed": failed, "errors": errors,
            "total": passed + failed + errors, "rc": p.returncode,
            "tail": "\n".join(p.stdout.strip().splitlines()[-3:])}


def failures(tree: Path, limit: int = 60) -> str:
    """What to show the agent between attempts: the failing tests and their assertions,
    truncated. Practitioners truncate this for a reason — an untruncated dump floods context."""
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:specdeck",
            "-p", "no:randomly", "--tb=short", "-x" if False else "--maxfail=12", "--no-header"]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600,
                       env={"PYTHONPATH": str(tree), "PATH": __import__("os").environ["PATH"],
                            "HOME": __import__("os").environ.get("HOME", "/tmp")})
    out = p.stdout.strip().splitlines()
    return "\n".join(out[:limit] if len(out) <= limit else out[:limit // 2] + ["...", ""] + out[-limit // 2:])


def failures_of(tree: Path, nodeids: list, limit: int = 50) -> str:
    """Failures of just these tests — what a worker on one module should be shown."""
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:specdeck",
            "-p", "no:randomly", "--tb=short", "--no-header", "--maxfail=10", *nodeids]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600,
                       env={"PYTHONPATH": str(tree), "PATH": os.environ["PATH"],
                            "HOME": os.environ.get("HOME", "/tmp")})
    out = p.stdout.strip().splitlines()
    return "\n".join(out if len(out) <= limit else out[:limit // 2] + ["..."] + out[-limit // 2:])


def per_test(tree: Path) -> dict:
    """Outcome per test node id, so a post-mortem can name which tests never pass."""
    args = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:specdeck",
            "-p", "no:randomly", "--tb=line", "-rA", "--no-header", "-q"]
    p = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=600,
                       env={"PYTHONPATH": str(tree), "PATH": os.environ["PATH"],
                            "HOME": os.environ.get("HOME", "/tmp")})
    out = {}
    for line in p.stdout.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR)\s+(\S+)", line)
        if m:
            out[m.group(2)] = m.group(1)
    return {"outcomes": out, "report": p.stdout[-14000:]}
