"""(a) Where did the expected answer come from?

Mutation testing can't tell a correct test from one whose expected values were
pasted from buggy output; both kill the same mutants. proofdeck doesn't claim
to either. An expected value counts as confirmed only when it comes from
somewhere independent of this branch: the same assertion already existed on
the base branch, or the value is read from data (not written as a literal in
the new test). Nothing written on the branch can confirm it, not even a
comment saying so, because the agent that wrote the code could write that too.
The honest cost: correct new tests are flagged exactly like copied ones.
"""
from __future__ import annotations

import ast

from typing import Optional

from . import ledger as _ledger
from .diff import added_lines, is_test_path
from .git import base_files, base_text, branch_text, merge_base
from .model import Finding, FindingKind

MESSAGE = ("This expected value was written on this branch, together with the code it checks. "
           "Confirm it against the requirement: a value copied from what the code printed passes "
           "even when the code is wrong.")
MESSAGE_DRAFT = ("This expected value comes from requirement {rid}, which isn't approved yet: {why}. It counts "
                 "as confirmed once the requirement is merged; until then, check it against what was asked for.")


def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "approx" and node.args:
        node = node.args[0]
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def _assert_key(node: ast.Assert) -> str:
    return ast.unparse(node.test)


def _base_assertions(repo, rev: str) -> set:
    keys = set()
    for path in base_files(repo, rev):
        if not (path.endswith(".py") and is_test_path(path)):
            continue
        try:
            tree = ast.parse(base_text(repo, rev, path) or "")
        except SyntaxError:
            continue
        keys.update(_assert_key(n) for n in ast.walk(tree) if isinstance(n, ast.Assert))
    return keys


def _literal_names(fn: ast.AST) -> set:
    """Local names assigned a literal inside `fn` (expected = 15)."""
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and _is_literal(n.value):
            names.add(n.targets[0].id)
    return names


def unconfirmed_expectations(repo, base: str = "main", ledger: Optional["_ledger.Ledger"] = None) -> list:
    """Expected values born on this branch that nothing independent confirms."""
    return _scan(repo, base, ledger)[0]


def against_requirements(repo, base: str = "main", ledger: Optional["_ledger.Ledger"] = None) -> list:
    """Linked tests whose literal expected value contradicts an approved example."""
    return _scan(repo, base, ledger)[1]


def _scan(repo, base: str, ledger) -> tuple:
    rev = merge_base(repo, base)
    ledger = ledger if ledger is not None else _ledger.load(repo, base)
    confirmed = _base_assertions(repo, rev)
    unconfirmed, contradictions = [], []
    for path, lines in sorted(added_lines(repo, base, tests=True, sources=False).items()):
        text = branch_text(repo, path) or ""
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        src_lines = text.splitlines()
        scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [tree]
        seen = set()
        for scope in scopes:
            literal_names = _literal_names(scope)
            claims = _ledger.linked(scope) if scope is not tree else set()
            for node in ast.walk(scope):
                if not isinstance(node, ast.Assert) or node.lineno in seen:
                    continue
                if not any(n in lines for n in range(node.lineno, (node.end_lineno or node.lineno) + 1)):
                    continue
                t = node.test
                if not (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Eq, ast.NotEq))):
                    continue
                snippet = src_lines[node.lineno - 1].strip()
                sides = [t.left, t.comparators[0]]
                ex = next((c for c in map(_ledger.example_call, sides) if c), None)
                if ex is not None:  # expected value taken from a requirement
                    rid, given = ex
                    ok, _ = ledger.approved_expect(rid, given)
                    if ok and ledger.branch.get(rid) is not None and \
                            ledger.branch[rid].examples == ledger.approved[rid].examples:
                        seen.add(node.lineno)
                        continue  # approved, and unchanged on this branch: confirmed
                    why = ("it was added on this branch" if rid not in ledger.approved
                           else "its examples were changed on this branch" if ok
                           else "the approved version has no such example")
                    seen.add(node.lineno)
                    unconfirmed.append(Finding(FindingKind.UNCONFIRMED_EXPECTATION, path, node.lineno,
                                               MESSAGE_DRAFT.format(rid=rid, why=why), snippet=snippet))
                    continue
                lit = [(_is_literal(s) or (isinstance(s, ast.Name) and s.id in literal_names)) for s in sides]
                if lit.count(True) != 1:
                    continue  # nothing literal, or literal == literal
                actual, expected = (sides[1], sides[0]) if lit[0] else (sides[0], sides[1])
                verdict = _against(ledger, claims, actual, expected, isinstance(t.ops[0], ast.Eq))
                if verdict == "confirmed":
                    seen.add(node.lineno)
                    continue
                if verdict is not None:
                    seen.add(node.lineno)
                    contradictions.append(Finding(FindingKind.CONTRADICTS_REQUIREMENT, path, node.lineno,
                                                  verdict, snippet=snippet))
                    continue
                if _assert_key(node) in confirmed:
                    continue
                seen.add(node.lineno)
                unconfirmed.append(Finding(FindingKind.UNCONFIRMED_EXPECTATION, path, node.lineno, MESSAGE,
                                           snippet=snippet))
    return unconfirmed, contradictions


def _against(ledger, claims: set, actual: ast.AST, expected: ast.AST, is_eq: bool):
    """'confirmed', a contradiction message, or None when no approved example applies.

    Only requirements the test itself claims are consulted, so a coincidence of
    argument values in an unrelated test never counts."""
    if not isinstance(actual, ast.Call) or not is_eq:
        return None
    try:
        value = ast.literal_eval(expected.args[0] if isinstance(expected, ast.Call) and expected.args else expected)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    for rid in sorted(claims):
        entry = ledger.approved.get(rid)
        if entry is None:
            continue
        for given, expect in entry.examples:
            if _ledger.call_matches(actual, given):
                if value == expect:
                    return "confirmed"
                shown = ", ".join(f"{k}={v!r}" for k, v in given.items())
                return _ledger.MSG_CONTRADICTS.format(actual=repr(value), rid=rid, given=shown, expect=repr(expect))
    return None
