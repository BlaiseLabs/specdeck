"""Finer slicing than whole test files: the individual test functions that touch a module,
and the dependency edges that say which already-written modules a worker needs to see.

Measured on this subject: the test functions that execute one module are 41,773 tokens
summed over the 11 modules, against 78,941 for the whole files containing them — 47%
cheaper, for strictly more relevant content.
"""
from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

SRC = Path("/workspaces/python/tapedeck")


@lru_cache(maxsize=1)
def _maps() -> tuple:
    import yaml

    cov = json.loads((SRC / ".specdeck" / "coverage.json").read_text(encoding="utf-8"))
    spec = yaml.safe_load((SRC / ".specdeck" / "spec.yaml").read_text(encoding="utf-8"))
    node = {s["id"]: s["logic"][0][len("pytest "):] for s in spec["scenarios"]}
    return cov, node


def nodeids_for(module: str) -> list:
    """Test node ids whose scenario executes `module`, from specdeck's coverage map."""
    cov, node = _maps()
    out = [node[sid] for sid, places in cov.items()
           if sid in node and any(p.split(":")[0] == module for p in places)]
    return sorted(set(out))


def test_source_for(tree: Path, module: str) -> str:
    """Just those test functions, with their decorators, grouped by file."""
    blocks: dict = {}
    for nid in nodeids_for(module):
        rel, _, fn = nid.partition("::")
        fn = re.sub(r"\[.*\]$", "", fn)
        path = tree / rel
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        try:
            parsed = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines(keepends=True)
        for node in parsed.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn:
                start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                blocks.setdefault(rel, []).append("".join(lines[start:node.end_lineno]))
    out = []
    for rel, fns in sorted(blocks.items()):
        out.append(f"### from {rel}\n\n```python\n" + "\n\n".join(f.rstrip() for f in fns) + "\n```")
    return "\n\n".join(out)


def depends_on(module: str, among: list) -> list:
    """Which of `among` this module imports — what a worker actually needs to have seen."""
    src = (SRC / module).read_text(encoding="utf-8")
    out = []
    for other in among:
        if other == module:
            continue
        stem = Path(other).stem
        # Only real imports. Matching bare attribute access (`app.foo`) invented edges and
        # put a cycle in the graph.
        if re.search(rf"^\s*from [.\w]*\.{stem} import|^\s*from \.{stem} import|^\s*import [\w.]*\b{stem}\b",
                     src, re.M):
            out.append(other)
    return out


def chunks(targets: list) -> list:
    """One chunk per module, in an order where a module's dependencies come first."""
    deps = {m: [d for d in depends_on(m, targets)] for m in targets}
    order, placed = [], set()
    while len(placed) < len(targets):
        ready = [m for m in targets if m not in placed and set(deps[m]) <= placed]
        if not ready:
            ready = [m for m in targets if m not in placed]
        for m in ready:
            order.append((m, list(deps[m])))
            placed.add(m)
    return order
