"""Is the set of requirements consistent with itself and with its tests? ISO/IEC/IEEE 29148:2018 §5.2.6.

A set is *consistent* when its requirements do not contradict one another. With examples, two
ways of contradicting can be checked by reading files rather than running code:

- one requirement gives two different expected values for the same inputs;
- a test linked to a requirement (``covers=`` or ``example()``) asserts a literal value for a
  call whose arguments are exactly one of the requirement's examples, and the value is not the
  example's ``expect``. The test and the requirement cannot both be right.

A test that takes its expected value from ``example()`` is consistent by construction; this
catches the ones that wrote the number down instead. Only requirements the test itself claims
are consulted, so a coincidence of argument values in an unrelated test never counts.

proofdeck runs the same comparison on a branch, against the requirements as they were on the
base branch (approved), to stop a change from editing the requirement and the test together.
This is the standing check of the whole set as it is now.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Iterable, Optional

CLAUSE = "§5.2.6"


def _diag(code: str, message: str, rid: Optional[str], line=None):
    from .quality import _diag as q
    return q("warning", code, message, CLAUSE, rid, line)


def _callee(node: ast.AST) -> str:
    f = node.func if isinstance(node, ast.Call) else node
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _const(node: ast.AST):
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def _key(given: dict) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in given.items()))


def conflicting_examples(reqs) -> list:
    """Requirements that give two expected values for the same inputs."""
    out = []
    for req in reqs.items.values():
        seen: dict = {}
        for ex in req.examples:
            k = _key(ex.given)
            if k in seen and seen[k] != ex.expect:
                shown = ", ".join(f"{a}={b!r}" for a, b in ex.given.items()) or "no inputs"
                out.append(_diag("CONFLICTING_EXAMPLES", f"two examples with {shown} expect {seen[k]!r} and "
                                 f"{ex.expect!r}; the requirement contradicts itself", req.id, req.line))
            seen.setdefault(k, ex.expect)
    return out


def _claims(fn: ast.AST) -> set:
    """Requirement ids a test claims: @scenario(covers=[...]) and example()/examples() calls."""
    ids = set()
    for d in getattr(fn, "decorator_list", []):
        if isinstance(d, ast.Call) and _callee(d) == "scenario":
            for kw in d.keywords:
                ok, value = _const(kw.value) if kw.arg == "covers" else (False, None)
                if ok and isinstance(value, (list, tuple)):
                    ids.update(str(v) for v in value)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _callee(node) in ("example", "examples") and node.args:
            ok, rid = _const(node.args[0])
            if ok and isinstance(rid, str):
                ids.add(rid)
    return ids


def _matches(call: ast.Call, given: dict) -> bool:
    """Does the call pass exactly the example's inputs, positionally in order or by name?"""
    pos = []
    for a in call.args:
        ok, v = _const(a)
        if not ok:
            return False
        pos.append(v)
    kw = {}
    for k in call.keywords:
        ok, v = _const(k.value)
        if not k.arg or not ok:
            return False
        kw[k.arg] = v
    keys = list(given)
    if len(pos) + len(kw) != len(keys):
        return False
    if any(given[keys[i]] != v or type(given[keys[i]]) is not type(v) for i, v in enumerate(pos)):
        return False
    rest = keys[len(pos):]
    return set(kw) == set(rest) and all(given[k] == kw[k] for k in rest)


def _expected(node: ast.AST, literals: dict):
    """(True, value, approx) when ``node`` is a literal, pytest.approx(literal) or a local
    name assigned a literal; else (False, None, False)."""
    approx = isinstance(node, ast.Call) and _callee(node) == "approx" and bool(node.args)
    inner = node.args[0] if approx else node
    if isinstance(inner, ast.Name) and inner.id in literals:
        return True, literals[inner.id], approx
    ok, value = _const(inner)
    return ok, value, approx


def _same(value, expect, approx: bool) -> bool:
    if approx and isinstance(value, (int, float)) and isinstance(expect, (int, float)):
        return math.isclose(value, expect, rel_tol=1e-6, abs_tol=1e-12)
    return value == expect


def contradicting_tests(reqs, files: Iterable) -> list:
    """Linked tests whose literal expected value contradicts one of the requirement's examples.

    ``files``: paths, or (path, name to show) pairs as ``test_files`` returns them."""
    out = []
    for item in files:
        path, shown_path = item if isinstance(item, tuple) else (item, item)
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            claims = [rid for rid in sorted(_claims(fn)) if rid in reqs.items and reqs.items[rid].examples]
            if not claims:
                continue
            literals = {}
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    ok, v = _const(n.value)
                    if ok:
                        literals[n.targets[0].id] = v
            for node in ast.walk(fn):
                t = node.test if isinstance(node, ast.Assert) else None
                if not (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq)):
                    continue
                for actual, expected in ((t.left, t.comparators[0]), (t.comparators[0], t.left)):
                    if not isinstance(actual, ast.Call) or _callee(actual) in ("example", "approx"):
                        continue
                    ok, value, approx = _expected(expected, literals)
                    if not ok:
                        continue
                    hit = _against(reqs, claims, actual, value, approx)
                    if hit:
                        rid, given, expect = hit
                        out.append(_diag("CONTRADICTS_EXAMPLE",
                                         f"{shown_path}:{node.lineno} in {fn.name} expects {value!r} where {rid} says "
                                         f"{given} gives {expect!r}; the test and the requirement cannot both be "
                                         f"right", rid, reqs.items[rid].line))
                    break
    return out


def _against(reqs, claims: list, call: ast.Call, value, approx: bool):
    for rid in claims:
        for ex in reqs.items[rid].examples:
            if _matches(call, ex.given):
                if _same(value, ex.expect, approx):
                    return None
                return rid, ", ".join(f"{k}={v!r}" for k, v in ex.given.items()), ex.expect
    return None


def test_files(scenarios: list, roots: Iterable[Path]) -> list:
    """(path, node-id path) for the test files behind the scenarios, from the node id in each
    one's `logic`, resolved against the first root that has them (pytest's rootdir is not
    recorded in the spec)."""
    rels: list = []
    for sc in scenarios:
        for cmd in sc.get("logic") or []:
            if isinstance(cmd, str) and cmd.startswith("pytest "):
                rel = cmd[len("pytest "):].split("::", 1)[0].strip()
                if rel.endswith(".py") and rel not in rels:
                    rels.append(rel)
    found = []
    for rel in rels:
        for root in roots:
            p = Path(root) / rel
            if p.exists():
                found.append((p, rel))
                break
    return found


def check(reqs, scenarios: Optional[list], roots: Iterable[Path] = ()) -> list:
    """§5.2.6 consistency: conflicting examples, and, with the spec's scenarios, contradicting tests."""
    out = conflicting_examples(reqs)
    roots = [Path(r) for r in roots]
    if scenarios:
        out += contradicting_tests(reqs, test_files(scenarios, roots))
    return out
