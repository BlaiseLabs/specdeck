"""Build the three copies of tapedeck for the navigation experiment.

  bare   no docstrings anywhere, no specdeck decorators or steps, no spec
  docs   docstrings kept,       no specdeck decorators or steps, no spec
  spec   everything kept, plus the spec, and specdeck's agent document as SPEC.md

Edits are made by line position from the AST, never by regenerating source: `ast.unparse`
would also delete every comment and reformat every line, so the stripped copies would
differ from the spec copy in far more than the spec. The README is kept in all three — it
is the "all you have is the readme" baseline. tapedeck's own hand-written AGENTS.md (a usage
guide, unrelated to the spec) is also kept in all three; the spec copy's document goes in a
separate SPEC.md, found by `ls` like any other file — no pointer to it is planted anywhere.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path("/workspaces/python/tapedeck")
IGNORE = shutil.ignore_patterns("__pycache__", ".git", "*.egg-info", ".pytest_cache", "bench")
STEP_FUNCS = {"given", "when", "then", "and_"}


def _is_doc(node) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)
            and isinstance(node.value.value, str))


def strip_docstrings(src: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    cuts = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and _is_doc(body[0]):
                d = body[0]
                indent = " " * d.col_offset
                # keep the block syntactically valid if the docstring was its only statement
                repl = [f"{indent}pass\n"] if len(body) == 1 else []
                cuts.append((d.lineno - 1, d.end_lineno, repl))
    for start, end, repl in sorted(cuts, reverse=True):
        lines[start:end] = repl
    return "".join(lines)


def strip_specdeck(src: str) -> str:
    """Remove `from specdeck import ...`, @scenario(...) decorators, and unwrap
    `with given(...):` blocks — header removed, body dedented, comments kept."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    edits = []   # (start, end, replacement lines)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "specdeck":
            edits.append((node.lineno - 1, node.end_lineno, []))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                f = d.func if isinstance(d, ast.Call) else d
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name == "scenario":
                    # decorator lines run from the line above `d` (the @) to d.end_lineno
                    edits.append((d.lineno - 1, d.end_lineno, []))
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = repl
    src = "".join(lines)
    # unwrap step blocks repeatedly, innermost-last, re-parsing after each pass
    for _ in range(50):
        tree = ast.parse(src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and len(node.items) == 1:
                call = node.items[0].context_expr
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") in STEP_FUNCS:
                    target = node
                    break
        if target is None:
            break
        lines = src.splitlines(keepends=True)
        header_end = target.body[0].lineno - 1
        # include any comment lines between the header and the first statement
        body_start = target.lineno           # first line after `with ...:` (0-based index)
        while body_start < len(lines) and lines[body_start - 1].rstrip().endswith(("(", ",")):
            body_start += 1
        hdr_last = header_end
        for i in range(target.lineno - 1, header_end):
            if lines[i].rstrip().endswith(":"):
                hdr_last = i + 1
                break
        delta = target.body[0].col_offset - target.col_offset
        body = lines[hdr_last:target.end_lineno]
        dedented = [l[delta:] if l[:delta].strip() == "" else l for l in body]
        lines[target.lineno - 1:target.end_lineno] = dedented
        src = "".join(lines)
    return src


def build(arm: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SRC, dest, ignore=IGNORE)
    if arm in ("bare", "docs"):
        shutil.rmtree(dest / ".specdeck", ignore_errors=True)
        for p in (dest / "tests").rglob("*.py"):
            p.write_text(strip_specdeck(p.read_text(encoding="utf-8")), encoding="utf-8")
    if arm == "bare":
        for p in list((dest / "tapedeck").rglob("*.py")) + list((dest / "tests").rglob("*.py")):
            p.write_text(strip_docstrings(p.read_text(encoding="utf-8")), encoding="utf-8")
    if arm == "spec":
        r = subprocess.run([sys.executable, "-m", "specdeck", "context", "--out", str(dest / "SPEC.md")],
                           cwd=SRC, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
    return dest


if __name__ == "__main__":
    for arm in ("bare", "docs", "spec"):
        d = build(arm, Path(f"/tmp/nav_{arm}"))
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "-p", "no:specdeck", "-p", "no:randomly"],
                           cwd=d, capture_output=True, text=True,
                           env={"PYTHONPATH": str(d), "PATH": __import__("os").environ["PATH"],
                                "HOME": __import__("os").environ.get("HOME", "/tmp")})
        tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
        n_doc = sum(1 for f in list(d.joinpath("tapedeck").rglob("*.py")) + list(d.joinpath("tests").rglob("*.py"))
                    for x in ast.walk(ast.parse(f.read_text())) if isinstance(x, (ast.FunctionDef, ast.ClassDef, ast.Module, ast.AsyncFunctionDef)) and ast.get_docstring(x))
        n_scen = sum(f.read_text().count("@scenario") for f in d.joinpath("tests").rglob("*.py"))
        n_step = sum(f.read_text().count("with given(") + f.read_text().count("with then(") for f in d.joinpath("tests").rglob("*.py"))
        print(f"{arm:5} docstrings {n_doc:>3} | @scenario {n_scen:>2} | step blocks {n_step:>3} | "
              f".specdeck {'yes' if (d/'.specdeck').exists() else 'no '} | SPEC.md {'yes' if (d/'SPEC.md').exists() else 'no '} | AGENTS.md identical: {(d/'AGENTS.md').read_text() == (SRC/'AGENTS.md').read_text()} | suite: {tail}")
