"""(e) Weakening the tests or the checker.

weakened-test: a skip or xfail added, an assertion that existed on the base
branch removed, a test or test file deleted.
checker-tampering: `pragma: no mutate` added, or the proofdeck, coverage or
pytest configuration changed in a way that hides code or tests. (proofdeck reads
its own configuration from the base branch anyway, so such a change can't take
effect in the same pull request; it is still reported.)
"""
from __future__ import annotations

import ast
import difflib
import re
import tomllib
from collections import Counter

from .diff import added_lines, is_test_path
from .git import base_files, base_text, branch_files, branch_text, merge_base
from .model import Finding, FindingKind

_SKIP_DECOS = re.compile(r"(^|\.)(skip|skipif|xfail|skipIf|skipUnless|expectedFailure)$")
_PRAGMA = re.compile(r"#.*\bpragma:\s*no\s*mutate\b|#\s*proofdeck:\s*(ignore|skip)", re.I)
_CFG_KEYS = re.compile(r"\b(omit|exclude_lines|exclude_also|addopts|testpaths|norecursedirs|python_files|"
                       r"python_functions|deselect|proofdeck)\b", re.I)


def _tests(text):
    """{qualname: FunctionDef} for test functions (and methods of Test classes)."""
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return {}, None
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out[f"{node.name}.{sub.name}"] = sub
    return out, tree


def _deco_name(d: ast.AST) -> str:
    d = d.func if isinstance(d, ast.Call) else d
    parts = []
    while isinstance(d, ast.Attribute):
        parts.append(d.attr)
        d = d.value
    if isinstance(d, ast.Name):
        parts.append(d.id)
    return ".".join(reversed(parts))


def _skips(fn: ast.AST) -> dict:
    """{marker: line} for skip/xfail decorators and pytest.skip()/xfail() calls in the body."""
    out = {}
    for d in fn.decorator_list:
        name = _deco_name(d)
        if _SKIP_DECOS.search(name):
            out.setdefault(name.rsplit(".", 1)[-1], d.lineno)
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _deco_name(n) in ("pytest.skip", "pytest.xfail", "skip", "xfail", "self.skipTest"):
            out.setdefault(_deco_name(n), n.lineno)
    return out


def _asserts(fn: ast.AST) -> Counter:
    return Counter(ast.unparse(n.test) for n in ast.walk(fn) if isinstance(n, ast.Assert))


def _toml_sections(text: str) -> dict:
    try:
        data = tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return {}
    tool = data.get("tool", {})
    return {
        "[tool.proofdeck]": tool.get("proofdeck"),
        "[tool.coverage]": tool.get("coverage"),
        "[tool.pytest.ini_options]": {k: v for k, v in (tool.get("pytest", {}).get("ini_options", {}) or {}).items()
                                       if _CFG_KEYS.search(k)},
    }


def _line_of(text: str, needle: str) -> int:
    for i, line in enumerate((text or "").splitlines(), start=1):
        if line.strip().startswith(needle):
            return i
    return 1


def scan(repo, base: str = "main") -> list:
    rev = merge_base(repo, base)
    found = []
    W, T = FindingKind.WEAKENED_TEST, FindingKind.CHECKER_TAMPERING
    base_py = {p for p in base_files(repo, rev) if p.endswith(".py") and is_test_path(p)}
    branch_py = {p for p in branch_files(repo) if p.endswith(".py") and is_test_path(p)}

    for path in sorted(base_py | branch_py):
        old_fns, _ = _tests(base_text(repo, rev, path)) if path in base_py else ({}, None)
        if path in base_py and path not in branch_py:
            if old_fns:
                found.append(Finding(W, path, 1, f"This test file existed on the base branch and was deleted "
                                                  f"({len(old_fns)} tests)."))
            continue
        new_fns, _ = _tests(branch_text(repo, path))
        for name, old in old_fns.items():
            new = new_fns.get(name)
            if new is None:
                found.append(Finding(W, path, 1, f"The test {name} existed on the base branch and was deleted."))
                continue
            removed = _asserts(old) - _asserts(new)
            if removed:
                gone = next(iter(removed))
                found.append(Finding(W, path, new.lineno,
                                     f"{name} lost {sum(removed.values())} assertion(s) it had on the base branch, "
                                     f"e.g. `assert {gone}`."))
        for name, new in new_fns.items():
            old = old_fns.get(name)
            before = _skips(old) if old is not None else {}
            for marker, line in _skips(new).items():
                if marker not in before:
                    found.append(Finding(W, path, line, f"{name} is now skipped or expected to fail ({marker}), "
                                                        f"so it no longer checks anything."))

    for path, lines in added_lines(repo, base, tests=True, sources=True).items():
        text = (branch_text(repo, path) or "").splitlines()
        for n in sorted(lines):
            if _PRAGMA.search(text[n - 1]):
                found.append(Finding(T, path, n, "A `pragma: no mutate` was added, which hides this line from "
                                                 "mutation testing.", snippet=text[n - 1].strip()))

    old_toml, new_toml = base_text(repo, rev, "pyproject.toml"), branch_text(repo, "pyproject.toml")
    if new_toml is not None and old_toml != new_toml:
        before, after = _toml_sections(old_toml or ""), _toml_sections(new_toml)
        for section, value in after.items():
            if value != before.get(section) and (value or before.get(section)):
                found.append(Finding(T, "pyproject.toml", _line_of(new_toml, section),
                                     f"{section} changed on this branch. It decides what gets checked; proofdeck "
                                     f"keeps using the base branch's version."))
    for cfg in (".coveragerc", "setup.cfg", "pytest.ini", "tox.ini"):
        old, new = base_text(repo, rev, cfg), branch_text(repo, cfg)
        if new is None or old == new:
            continue
        changed = [ln for ln in difflib.ndiff((old or "").splitlines(), new.splitlines())
                   if ln[:2] in ("+ ", "- ") and _CFG_KEYS.search(ln)]
        if changed:
            found.append(Finding(T, cfg, 1, f"{cfg} changed what tests run or what counts as covered."))
    return found
