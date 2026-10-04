"""The 283-token artifact neither the tests nor the spec contains.

Measured failure mode, on two models three orders of magnitude apart: the model builds a
component whose interface the surrounding code disagrees with. `qwen3:8b` omitted
`Recorder.schemas`, which `proxies.py:99` calls; terra's `Recorder` omitted five members.
Both had perfect signatures for what they did write — the gap is not the signature, it is
knowing what the *rest of the codebase* calls on you.

"The Specification Gap" (arXiv:2603.24284) found that what recovers distributed-work
performance is each slice carrying the full interface contract, and that conflict detection
added nothing. This is that contract, derived statically.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path("/workspaces/python/tapedeck")


def _exported(path: Path) -> set:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _local_names(tree: ast.AST, module_stem: str, exported: set) -> set:
    """Names in this file that refer to something the target module exports.

    Three ways they appear, and the third is the one that matters most in practice:
    a direct import; a parameter annotated with an exported class; and an attribute the
    class stashed it on (`self._rec = recorder`), which is how `self._rec.schemas` — the
    call that actually broke — is reached.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and module_stem in node.module.split("."):
            for a in node.names:
                if a.name in exported:
                    names.add(a.asname or a.name)
        if isinstance(node, ast.arg) and node.annotation is not None:
            ann = ast.unparse(node.annotation).strip('"\'')
            if ann.split("[")[0].split(".")[-1] in exported:
                names.add(node.arg)
        if isinstance(node, (ast.AnnAssign,)) and node.annotation is not None:
            ann = ast.unparse(node.annotation).strip('"\'')
            if ann.split("[")[0].split(".")[-1] in exported and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    # second pass: attributes assigned from one of those names, e.g. `self._rec = recorder`
    for _ in range(3):                       # chains like self._a = self._b resolve in a few passes
        before = len(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                src_name = None
                v = node.value
                if isinstance(v, ast.Name):
                    src_name = v.id
                elif isinstance(v, ast.Attribute):
                    src_name = v.attr
                if src_name in names:
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Attribute):
                            names.add(tgt.attr)
                        elif isinstance(tgt, ast.Name):
                            names.add(tgt.id)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "__setattr__":
                if len(node.args) >= 3 and isinstance(node.args[1], ast.Constant) \
                        and isinstance(node.args[2], ast.Name) and node.args[2].id in names:
                    names.add(str(node.args[1].value))
        if len(names) == before:
            break
    return names


def for_module(module: str, others: list) -> list:
    """`[(member, 'file:line')]` — what code outside this module calls on it."""
    exported = _exported(SRC / module)
    stem = Path(module).stem
    found: dict = {}
    for other in others:
        path = SRC / other
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        names = _local_names(tree, stem, exported)
        if not names:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                base = node.value
                root = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
                if root in names and node.attr not in found:
                    found[node.attr] = f"{other}:{node.lineno}"
    return sorted(found.items())


def render(targets: list, others: list) -> str:
    """The block to paste into a prompt. Empty when nothing outside calls these modules."""
    blocks = []
    for module in targets:
        uses = for_module(module, others)
        if not uses:
            continue
        lines = [f"`{module}` — code you are NOT writing calls these on it, so they must exist "
                 f"with these names:"]
        for member, where in uses:
            lines.append(f"  .{member}    (used at {where})")
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return ("## What the rest of the codebase requires of your modules\n\n"
            "Derived by static analysis of the files you are not writing. Missing one of these "
            "breaks code you cannot see.\n\n" + "\n\n".join(blocks))
