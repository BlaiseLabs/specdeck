"""Source positions: ast columns are UTF-8 byte offsets; strings are indexed by character."""
from __future__ import annotations

import ast
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=32)
def cached(text: str) -> "Source":
    """One parsed Source per file text, shared by every per-line question about it."""
    return Source(text)


class Source:
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.splitlines(keepends=True) or [""]
        self._tree: Optional[ast.AST] = None
        self._parents: Optional[dict] = None
        self._index: Optional[tuple] = None

    @property
    def tree(self) -> ast.AST:
        if self._tree is None:
            self._tree = ast.parse(self.text)
        return self._tree

    def col(self, lineno: int, byte_col: int) -> int:
        return len(self.lines[lineno - 1].encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))

    def line(self, lineno: int) -> str:
        return self.lines[lineno - 1].rstrip("\r\n") if 0 < lineno <= len(self.lines) else ""

    def parents(self) -> dict:
        if self._parents is None:
            self._parents = {}
            for node in ast.walk(self.tree):
                for child in ast.iter_child_nodes(node):
                    self._parents[child] = node
        return self._parents

    def _build_index(self) -> tuple:
        """One walk of the tree: innermost statement per line, single-line nodes per line (in
        ast.walk order), and the lines that belong to decorators."""
        if self._index is None:
            stmt_at: dict = {}
            on_line: dict = {}
            deco_lines: set = set()
            for node in ast.walk(self.tree):
                start = getattr(node, "lineno", None)
                end = getattr(node, "end_lineno", None) or start
                if start is None:
                    continue
                if isinstance(node, ast.stmt):
                    for n in range(start, end + 1):
                        best = stmt_at.get(n)
                        if best is None or (node.lineno, -end) >= (best.lineno, -(best.end_lineno or best.lineno)):
                            stmt_at[n] = node
                if start == end:
                    on_line.setdefault(start, []).append(node)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    for d in node.decorator_list:
                        deco_lines.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
            self._index = (stmt_at, on_line, deco_lines)
        return self._index

    def statement_at(self, lineno: int) -> Optional[ast.stmt]:
        """The innermost statement whose span covers `lineno`."""
        return self._build_index()[0].get(lineno)

    def nodes_on_line(self, lineno: int) -> list:
        """Nodes that start and end on `lineno`, in ast.walk order."""
        return self._build_index()[1].get(lineno, [])

    def in_decorator(self, lineno: int) -> bool:
        return lineno in self._build_index()[2]


def call_name(node: ast.AST) -> str:
    """'a.b.c' for a call's function, '' if it isn't a plain name/attribute chain."""
    f = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
        return ".".join(reversed(parts))
    return ""


def is_docstring(node: ast.AST, parents: dict) -> bool:
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return False
    parent = parents.get(node)
    return isinstance(parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and parent.body[0] is node
