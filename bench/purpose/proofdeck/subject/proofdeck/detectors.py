"""Static detectors: tests whose expected value can't disagree with the code, or can't fail at all.

(b) recomputed expectations: the expected side is the implementation's own
    formula, or another call to the same function.
(c) special-cased inputs: the implementation branches on exactly the literals a
    test passes and returns a constant.
    vacuous assertions: `assert True`, `is not None`, a bare isinstance, `x == x`.
"""
from __future__ import annotations

import ast
import copy
from typing import Optional

from .model import Finding, FindingKind

MSG_RECOMPUTED = ("The expected value is worked out the same way the code works it out, so this test "
                  "can't disagree with the code. Use a value taken from the requirement instead.")
MSG_SELF = ("The expected value comes from calling the code under test, so the test compares the code "
            "with itself. Use a value taken from the requirement instead.")
MSG_SPECIAL = ("This branch returns a fixed value for exactly the inputs a test uses. Check it against "
               "the requirement: it may exist only to make that test pass.")
MSG_VACUOUS = "This assertion passes for almost any result, so it can't catch a wrong answer."


def _snip(text: str, line: int) -> str:
    lines = text.splitlines()
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def _parse(text: str) -> Optional[ast.AST]:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _functions(tree: ast.AST) -> dict:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _callee(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return None


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _unwrap_approx(node: ast.AST) -> ast.AST:
    if isinstance(node, ast.Call) and _callee(node) == "approx" and node.args:
        return node.args[0]
    return node


def _params(fn: ast.AST) -> list:
    a = fn.args
    return [p.arg for p in [*a.posonlyargs, *a.args]]


class _Subst(ast.NodeTransformer):
    def __init__(self, env: dict) -> None:
        self.env = env

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id in self.env:
            return copy.deepcopy(self.env[node.id])
        return node


def _formulas(fn: ast.AST, call: ast.Call) -> list:
    """The function's return expressions with its parameters replaced by the call's arguments
    (and simple local assignments inlined), as they would appear written out in a test."""
    params = _params(fn)
    env = {}
    for name, arg in zip(params, call.args):
        env[name] = arg
    for kw in call.keywords:
        if kw.arg:
            env[kw.arg] = kw.value
    out = []
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            env[stmt.targets[0].id] = _Subst(dict(env)).visit(copy.deepcopy(stmt.value))
        elif isinstance(stmt, ast.Return) and stmt.value is not None:
            out.append(_Subst(dict(env)).visit(copy.deepcopy(stmt.value)))
    return out


def recomputed_expectations(test_source: str, impl_source: str, path: str = "<test>") -> list:
    test_tree, impl_tree = _parse(test_source), _parse(impl_source)
    if test_tree is None or impl_tree is None:
        return []
    impl = _functions(impl_tree)
    found = []
    for fn in _functions(test_tree).values():
        bound: dict = {}  # local name -> impl function whose result it holds
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                callee = _callee(node.value)
                if callee in impl:
                    bound[node.targets[0].id] = callee
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)
                    and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.Eq)):
                continue
            left, right = node.test.left, _unwrap_approx(node.test.comparators[0])
            msg = None
            for actual, expected in ((left, right), (right, left)):
                callee = _callee(actual)
                if callee not in impl:
                    continue
                if _callee(expected) == callee or (isinstance(expected, ast.Name) and bound.get(expected.id) == callee):
                    msg = MSG_SELF
                elif not isinstance(expected, (ast.Constant, ast.Name, ast.Call)):
                    if any(_dump(f) == _dump(expected) for f in _formulas(impl[callee], actual)):
                        msg = MSG_RECOMPUTED
                if msg:
                    break
            if msg:
                found.append(Finding(FindingKind.RECOMPUTED_EXPECTATION, path, node.lineno, msg,
                                     snippet=_snip(test_source, node.lineno)))
    return found


def _literal(node: ast.AST):
    """(True, value) for a literal, including negative numbers; (False, None) otherwise."""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return True, -node.operand.value
    return False, None


def _pinned(test: ast.AST, params: set) -> Optional[dict]:
    """{param: literal} if `test` is `p == lit` or an `and` of those; else None."""
    parts = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
    pins = {}
    for part in parts:
        if not (isinstance(part, ast.Compare) and len(part.ops) == 1 and isinstance(part.ops[0], ast.Eq)):
            return None
        a, b = part.left, part.comparators[0]
        if isinstance(b, ast.Name):
            a, b = b, a
        ok, value = _literal(b)
        if not (isinstance(a, ast.Name) and a.id in params and ok):
            return None
        pins[a.id] = value
    return pins


_NATURAL_EDGES = (0, 1, -1, "", None, True, False)


def special_cased_inputs(test_source: str, impl_source: str, path: str = "<impl>") -> list:
    test_tree, impl_tree = _parse(test_source), _parse(impl_source)
    if test_tree is None or impl_tree is None:
        return []
    calls: dict = {}
    for node in ast.walk(test_tree):
        name = _callee(node)
        if name:
            calls.setdefault(name, []).append(node)
    found = []
    for fn in _functions(impl_tree).values():
        params = _params(fn)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.If) and len(node.body) == 1 and isinstance(node.body[0], ast.Return)
                    and node.body[0].value is not None and _literal(node.body[0].value)[0]):
                continue
            pins = _pinned(node.test, set(params))
            if not pins:
                continue
            if len(pins) == 1 and next(iter(pins.values())) in _NATURAL_EDGES:
                continue  # `if n == 0: return 1` is an edge case, not a cheat
            for call in calls.get(fn.name, []):
                given = {}
                for name, arg in zip(params, call.args):
                    ok, value = _literal(arg)
                    if ok:
                        given[name] = value
                for kw in call.keywords:
                    ok, value = _literal(kw.value)
                    if kw.arg and ok:
                        given[kw.arg] = value
                if all(k in given and given[k] == v and type(given[k]) is type(v) for k, v in pins.items()):
                    found.append(Finding(FindingKind.SPECIAL_CASED_INPUT, path, node.lineno, MSG_SPECIAL,
                                         snippet=_snip(impl_source, node.lineno)))
                    break
    return found


def vacuous_assertions(test_source: str, path: str = "<test>") -> list:
    tree = _parse(test_source)
    if tree is None:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        t = node.test
        vacuous = (
            (isinstance(t, ast.Constant) and bool(t.value))
            or (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.IsNot)
                and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None)
            or (isinstance(t, ast.Call) and _callee(t) == "isinstance")
            or (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq)
                and _dump(t.left) == _dump(t.comparators[0]))
        )
        if vacuous:
            found.append(Finding(FindingKind.VACUOUS_ASSERTION, path, node.lineno, MSG_VACUOUS,
                                 snippet=_snip(test_source, node.lineno)))
    return found
