"""Arid lines: places where a mutant would only produce noise.

After Google's "arid nodes" (Petrović et al.): logging, printing, sleeps and
timeouts, imports, docstrings, `if __name__ == "__main__"` blocks, bare type
annotations, and signatures (whose only mutable parts are default values, the
most-rejected Python mutant at Google). Reviewers ignore findings there, so
they are never mutated.
"""
from __future__ import annotations

import ast

from ._source import cached, call_name, is_docstring

_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "fatal"}
_LOGGERS = {"log", "logger", "logging", "_log", "_logger", "LOG", "LOGGER", "log_", "self.log", "self.logger"}


def _is_noise_call(call: ast.Call) -> bool:
    name = call_name(call)
    if not name:
        return False
    head, _, last = name.rpartition(".")
    if name in ("print", "pprint", "pprint.pprint", "breakpoint"):
        return True
    if last in _LOG_METHODS and (head in _LOGGERS or head.endswith(("log", "logger", "LOG"))):
        return True
    if last == "sleep":  # time.sleep, asyncio.sleep, anything.sleep
        return True
    return False


def _in_main_guard(stmt: ast.AST, parents: dict) -> bool:
    node = stmt
    while node is not None:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            t = node.test
            names = [t.left, *t.comparators]
            if any(isinstance(n, ast.Name) and n.id == "__name__" for n in names) and any(
                isinstance(n, ast.Constant) and n.value == "__main__" for n in names
            ):
                return True
        node = parents.get(node)
    return False


def is_arid(source: str, line: int) -> bool:
    """True when `line` of `source` should never be mutated."""
    src = cached(source)
    text = src.line(line).strip()
    if not text or text.startswith("#"):
        return True
    try:
        stmt = src.statement_at(line)
    except SyntaxError:
        return True
    if stmt is None:
        return True
    parents = src.parents()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True  # a def/class line (its body lines are separate statements)
    if src.in_decorator(line):
        return True
    if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass, ast.Global, ast.Nonlocal)):
        return True
    if is_docstring(stmt, parents):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return True
    if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
        return True
    call = stmt.value if isinstance(stmt, ast.Expr) else None
    if isinstance(call, ast.Await):
        call = call.value
    if isinstance(call, ast.Call) and _is_noise_call(call):
        return True
    if _in_main_guard(stmt, parents):
        return True
    return False
