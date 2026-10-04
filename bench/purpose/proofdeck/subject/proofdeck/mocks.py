"""Which tests fake their collaborators, and which ones (patch, patch.object, monkeypatch, mocker)."""
from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_PATCHERS = {"patch", "patch.object", "mock.patch", "mock.patch.object", "unittest.mock.patch",
             "unittest.mock.patch.object", "mocker.patch", "mocker.patch.object", "monkeypatch.setattr"}
# Swapping the process's own streams or arguments feeds the code input; it fakes no collaborator.
_PROCESS_IO = {"sys.stdin", "sys.stdout", "sys.stderr", "sys.argv"}


def _name(node: ast.AST) -> str:
    f = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _dotted(call: ast.Call) -> str:
    """The patch target as written: 'pricing.bulk_discount', or 'sys.stdin' for patch.object(sys, 'stdin')."""
    name = _name(call)
    args = call.args
    if name.endswith("patch.object") or (name == "monkeypatch.setattr" and len(args) >= 3):
        if len(args) >= 2 and isinstance(args[1], ast.Constant):
            return f"{_name(args[0])}.{args[1].value}"
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        return args[0].value
    return ""


def _target(call: ast.Call) -> Optional[str]:
    """The thing being faked, as a short name: 'pricing.bulk_discount' -> 'bulk_discount'.
    None when the call only redirects the process's own streams."""
    dotted = _dotted(call)
    if dotted in _PROCESS_IO:
        return None
    return dotted.rsplit(".", 1)[-1] if dotted else "a collaborator"


@lru_cache(maxsize=512)
def _functions(path: str, mtime: float) -> dict:
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}::{sub.name}"] = sub
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def faked(repo, nodeid: str) -> list:
    """Names this test replaces with fakes; [] if it uses none."""
    file, _, rest = nodeid.partition("::")
    func = re.sub(r"\[.*\]$", "", rest)
    path = Path(repo) / file
    try:
        fns = _functions(str(path), path.stat().st_mtime)
    except OSError:
        return []
    fn = fns.get(func) or fns.get(func.rsplit("::", 1)[-1])
    if fn is None:
        return []
    names = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _name(node) in _PATCHERS:
            names.append(_target(node))
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and _name(d) in _PATCHERS:
            names.append(_target(d))
    names = [n for n in names if n is not None]
    if not names and any(a.arg == "mocker" for a in fn.args.args):
        names.append("a collaborator")
    return list(dict.fromkeys(names))
