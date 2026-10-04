"""Mutant schemata: every chosen mutant compiled into the source at once, each behind a switch.

    if qty > 100:          ->   if (qty >= 100 if _proofdeck_m(7) else qty > 100):
    return 15              ->   return (16 if _proofdeck_m(8) else 15)
    total = a + b          ->   if not _proofdeck_m(9): total = a + b

Every rewrite stays on its original line, so line numbers (and the tracer's
view of the code) don't change. `_proofdeck_m` is installed in builtins by the
test session, so no import line is needed; with no switch on, the code behaves
exactly as written. One process can then import the code once and fork a child
per mutant with that mutant's switch turned on.
"""
from __future__ import annotations

import ast
from typing import Optional

from ._source import Source, cached
from .model import Mutant

SWITCH = "_proofdeck_m"  # single underscore: no name mangling inside class bodies


def _region(orig: str, mut: str) -> tuple:
    """(start, end) in `orig` of the part that differs from `mut`."""
    p = 0
    while p < min(len(orig), len(mut)) and orig[p] == mut[p]:
        p += 1
    s = 0
    while s < min(len(orig), len(mut)) - p and orig[-1 - s] == mut[-1 - s]:
        s += 1
    return p, len(orig) - s


def _parses_as_expr(text: str) -> bool:
    try:
        ast.parse(text, mode="eval")
        return True
    except SyntaxError:
        return False


def switch_line(src: Source, mutant: Mutant, key: int) -> Optional[str]:
    """The rewritten text of the mutant's line, or None if this mutant can't be switched."""
    orig, mut = src.line(mutant.line), mutant.mutated
    if mutant.operator == "statement":
        stmt = src.statement_at(mutant.line)
        indent = orig[: len(orig) - len(orig.lstrip())]
        if stmt is None or stmt.lineno != mutant.line or stmt.end_lineno != mutant.line:
            return None
        if src.col(mutant.line, stmt.col_offset) != len(indent):
            return None  # shares its line with another statement
        return f"{indent}if not {SWITCH}({key}): {orig.lstrip()}"
    a0, b0 = _region(orig, mut)
    delta = len(mut) - len(orig)
    nodes = []
    for node in src.nodes_on_line(mutant.line):
        if not isinstance(node, ast.expr):
            continue
        a, b = src.col(node.lineno, node.col_offset), src.col(node.lineno, node.end_col_offset)
        inside = (a <= a0 and b0 <= b) if a0 < b0 else (a < a0 < b)
        if inside:
            nodes.append((b - a, a, b))
    for _, a, b in sorted(nodes):
        orig_expr, mut_expr = orig[a:b], mut[a:b + delta]
        if orig[:a] + mut_expr + orig[b:] != mut:
            continue
        if _parses_as_expr(orig_expr) and _parses_as_expr(mut_expr):
            return f"{orig[:a]}({mut_expr} if {SWITCH}({key}) else {orig_expr}){orig[b:]}"
    return None


def build(source: str, candidates: dict, first_key: int = 1) -> tuple:
    """Compile one mutant per line into `source`.

    `candidates`: {line: [Mutant, ...]} in preference order; the first candidate
    that can be switched is used. Returns (new_source, {line: (key, Mutant)}).
    """
    src = cached(source)
    lines = source.splitlines(keepends=True)
    chosen: dict = {}
    key = first_key
    for line in sorted(candidates):
        for m in candidates[line]:
            new = switch_line(src, m, key)
            if new is None:
                continue
            ending = lines[line - 1][len(lines[line - 1].rstrip("\r\n")):]
            trial = lines[:line - 1] + [new + ending] + lines[line:]
            try:
                ast.parse("".join(trial))
            except SyntaxError:
                continue
            lines = trial
            chosen[line] = (key, m)
            key += 1
            break
    return "".join(lines), chosen
