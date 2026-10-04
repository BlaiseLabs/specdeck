"""Mutants: small, plausible bugs on one line, most useful first.

Order follows what reviewers found actionable: comparison boundaries (where
off-by-one bugs live), negated comparisons and conditions, arithmetic,
boolean connectives, constants, then deleting the statement.
"""
from __future__ import annotations

import ast
from typing import Optional

from ._source import Source, cached
from .arid import is_arid
from .model import Mutant

_BOUNDARY = {ast.Gt: ">=", ast.GtE: ">", ast.Lt: "<=", ast.LtE: "<"}
_CMP_TEXT = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.Eq: "==", ast.NotEq: "!=",
             ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not"}
_NEGATE = {ast.Eq: "!=", ast.NotEq: "==", ast.In: "not in", ast.NotIn: "in", ast.Is: "is not", ast.IsNot: "is",
           ast.Gt: "<=", ast.GtE: "<", ast.Lt: ">=", ast.LtE: ">"}
_ARITH = {ast.Add: ("+", "-"), ast.Sub: ("-", "+"), ast.Mult: ("*", "/"), ast.Div: ("/", "*"),
          ast.FloorDiv: ("//", "/"), ast.Mod: ("%", "*"), ast.Pow: ("**", "*")}
_BOOL = {ast.And: ("and", "or"), ast.Or: ("or", "and")}
_ORDER = ["boundary", "negate", "arithmetic", "boolean", "constant", "statement"]


class _Line:
    def __init__(self, src: Source, lineno: int) -> None:
        self.src, self.lineno = src, lineno
        self.text = src.line(lineno)

    def on_line(self, node: ast.AST) -> bool:
        return getattr(node, "lineno", None) == self.lineno and getattr(node, "end_lineno", None) == self.lineno

    def c(self, byte_col: int) -> int:
        return self.src.col(self.lineno, byte_col)

    def swap_between(self, left: ast.AST, right: ast.AST, old: str, new: str) -> Optional[str]:
        """Replace operator `old` found between two operands on this line."""
        a, b = self.c(left.end_col_offset), self.c(right.col_offset)
        gap = self.text[a:b]
        i = gap.find(old)
        if i < 0:
            return None
        return self.text[:a] + gap[:i] + new + gap[i + len(old):] + self.text[b:]

    def replace_node(self, node: ast.AST, new: str) -> str:
        a, b = self.c(node.col_offset), self.c(node.end_col_offset)
        return self.text[:a] + new + self.text[b:]


def _constant_mutation(value) -> Optional[str]:
    if isinstance(value, bool):
        return repr(not value)
    if isinstance(value, int):
        return repr(value + 1)
    if isinstance(value, float):
        return repr(value + 1.0) if value else "1.0"
    if isinstance(value, str) and value:
        return '""'
    return None


def mutants_for_line(source: str, line: int, path: str = "<string>") -> list:
    """Candidate mutants for `line`, most useful first. Arid lines get none."""
    if is_arid(source, line):
        return []
    src = cached(source)
    try:
        src.tree
    except SyntaxError:
        return []
    L = _Line(src, line)
    found: dict = {k: [] for k in _ORDER}

    for node in src.nodes_on_line(line):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for i, op in enumerate(node.ops):
                old = _CMP_TEXT.get(type(op))
                if old is None:
                    continue
                left, right = operands[i], operands[i + 1]
                if type(op) in _BOUNDARY:
                    new = L.swap_between(left, right, old, _BOUNDARY[type(op)])
                    if new:
                        found["boundary"].append(new)
                if type(op) in (ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.Is, ast.IsNot):
                    new = L.swap_between(left, right, old, _NEGATE[type(op)])
                    if new:
                        found["negate"].append(new)
        elif isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            old, new_op = _ARITH[type(node.op)]
            new = L.swap_between(node.left, node.right, old, new_op)
            if new:
                found["arithmetic"].append(new)
        elif isinstance(node, ast.BoolOp) and type(node.op) in _BOOL:
            old, new_op = _BOOL[type(node.op)]
            new = L.swap_between(node.values[0], node.values[1], old, new_op)
            if new:
                found["boolean"].append(new)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            found["boolean"].append(L.replace_node(node, src.line(line)[L.c(node.operand.col_offset):L.c(node.operand.end_col_offset)]))
        elif isinstance(node, (ast.If, ast.While)) and L.on_line(node.test) and not isinstance(node.test, ast.UnaryOp):
            test_src = L.text[L.c(node.test.col_offset):L.c(node.test.end_col_offset)]
            found["negate"].append(L.replace_node(node.test, f"not ({test_src})"))
        elif isinstance(node, ast.Constant) and not isinstance(node.value, (bytes, type(None))):
            new_val = _constant_mutation(node.value)
            if new_val is not None and not _is_docstringish(src, node):
                found["constant"].append(L.replace_node(node, new_val))

    stmt = src.statement_at(line)
    if (stmt is not None and stmt.lineno == line and (stmt.end_lineno or line) == line
            and isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Return,
                                  ast.Raise, ast.Assert, ast.Delete))):
        indent = L.text[: len(L.text) - len(L.text.lstrip())]
        found["statement"].append(indent + "pass")

    out, seen = [], {L.text}
    for op in _ORDER:
        for mutated in found[op]:
            if mutated in seen:
                continue
            seen.add(mutated)
            new_source = "".join(src.lines[: line - 1]) + mutated + src.lines[line - 1][len(L.text):] + "".join(src.lines[line:])
            try:
                ast.parse(new_source)
            except SyntaxError:
                continue
            out.append(Mutant(path=path, line=line, operator=op, original=L.text, mutated=mutated))
    return out


def _is_docstringish(src: Source, node: ast.Constant) -> bool:
    """Strings nobody checks: bare expression strings and exception messages."""
    parents = src.parents()
    parent = parents.get(node)
    if isinstance(parent, ast.Expr):
        return True
    if isinstance(node.value, str):
        # strings are mutated only where they are values: returned, compared, assigned or concatenated.
        # Keys, attribute names, call arguments and messages are identifiers or prose; changing them
        # to "" is noise (and not in Google's operator set either).
        if not isinstance(parent, (ast.Return, ast.Compare, ast.Assign, ast.AnnAssign, ast.BinOp)):
            return True
        n = parent
        while n is not None and not isinstance(n, ast.stmt):
            n = parents.get(n)
        return isinstance(n, ast.Raise)
    return False


def apply(source: str, mutant: Mutant) -> str:
    """`source` with the mutant's line swapped in."""
    lines = source.splitlines(keepends=True)
    old = lines[mutant.line - 1]
    ending = old[len(old.rstrip("\r\n")):]
    lines[mutant.line - 1] = mutant.mutated + ending
    return "".join(lines)


def select(candidates: dict, cap_per_file: int = 7) -> list:
    """At most one mutant per line (its first, most useful candidate), at most `cap_per_file` per file.

    `candidates`: {path: {line: [Mutant, ...]}}. Lines are taken top to bottom.
    """
    chosen = []
    for path in candidates:
        taken = 0
        for line in sorted(candidates[path]):
            if taken >= cap_per_file:
                break
            if candidates[path][line]:
                chosen.append(candidates[path][line][0])
                taken += 1
    return chosen
