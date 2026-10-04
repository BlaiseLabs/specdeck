"""Build the three copies of specdeck's own repo for the business-purpose experiment.

  plain         code + ordinary pytest tests: no @scenario decorators (so no covers=), no step
                blocks, no test docstrings, no requirements.yaml, no .specdeck/, no README
  separate_doc  plain, plus REQUIREMENTS.md: every requirement's "says" and "why" as prose
                paragraphs, with no ids and no test names — the information without the links
  specdeck      the repo as specdeck users write it: decorators with covers=, step blocks,
                docstrings, requirements.yaml, a freshly generated .specdeck/, SPEC.md from
                `specdeck context`, and a one-line AGENTS.md pointer to SPEC.md and
                requirements.yaml (the navigation calibration showed an unannounced spec is ignored)

The README is removed from all three: it explains much of the "why" in its own words, which
would make even the plain copy a documented one. Source code (specdeck/) is identical in all
three. Edits are by AST line position, never ast.unparse, so comments survive.
"""
from __future__ import annotations

import ast
import io
import os
import tokenize
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "navigate"))
from strip import STEP_FUNCS, strip_docstrings  # noqa: E402

SRC = Path(os.environ.get("PURPOSE_SRC", "/workspaces/python/specdeck"))   # v3: a snapshot, see run.py
IGNORE = shutil.ignore_patterns("__pycache__", ".git", "*.egg-info", ".pytest_cache", "bench", ".specdeck",
                                "README.md", ".gitignore")
SPECDECK_NAMES = {"scenario"} | STEP_FUNCS
POINTER = ("SPEC.md describes this test suite (every scenario, what it checks, what depends on what); "
           "requirements.yaml says what the project must do and why, and tests link to it with covers=.\n")


def strip_specdeck_format(src: str) -> str:
    """Remove @scenario decorators and the scenario/step imports, and unwrap step blocks.
    Unlike navigate/strip.py, other `from specdeck import x` lines stay: here specdeck is the
    code under test, not only the test framework."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    edits = []
    for node in tree.body:   # module level only; nested imports of steps live in pytester strings
        if isinstance(node, ast.ImportFrom) and node.module == "specdeck":
            names = [a.name for a in node.names]
            if all(n in SPECDECK_NAMES for n in names):
                edits.append((node.lineno - 1, node.end_lineno, []))
            elif any(n in SPECDECK_NAMES for n in names):
                kept = ", ".join(n for n in names if n not in SPECDECK_NAMES)
                edits.append((node.lineno - 1, node.end_lineno, [f"from specdeck import {kept}\n"]))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                f = d.func if isinstance(d, ast.Call) else d
                if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) == "scenario":
                    edits.append((d.lineno - 1, d.end_lineno, []))
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = repl
    src = "".join(lines)
    for _ in range(200):
        tree = ast.parse(src)
        target = next((n for n in ast.walk(tree) if isinstance(n, ast.With) and len(n.items) == 1
                       and isinstance(n.items[0].context_expr, ast.Call)
                       and getattr(n.items[0].context_expr.func, "id", "") in STEP_FUNCS), None)
        if target is None:
            return src
        lines = src.splitlines(keepends=True)
        hdr_last = target.body[0].lineno - 1
        for i in range(target.lineno - 1, hdr_last):
            if lines[i].rstrip().endswith(":"):
                hdr_last = i + 1
                break
        delta = target.body[0].col_offset - target.col_offset
        # continuation lines of a multi-line string are data, not code: never dedent them
        inside = {i for n in ast.walk(target) if isinstance(n, (ast.Constant, ast.JoinedStr))
                  and n.end_lineno > n.lineno for i in range(n.lineno, n.end_lineno)}  # 0-based
        lines[target.lineno - 1:target.end_lineno] = [
            l[delta:] if (i not in inside and l[:delta].strip() == "") else l
            for i, l in enumerate(lines[hdr_last:target.end_lineno], start=hdr_last)]
        src = "".join(lines)
    raise RuntimeError("step unwrapping did not converge")


def strip_pyproject(text: str) -> str:
    """Drop specdeck's self-configuration (feature name, description, self-gating comment)."""
    return "".join(l for l in text.splitlines(keepends=True)
                   if not l.startswith("specdeck_") and not l.startswith("# specdeck gates itself")
                   and not l.startswith("# invalid generated spec fails the run"))


def strip_comments(src: str) -> str:
    """Remove every comment, found by tokenize (so `#` inside strings is untouched): a
    comment-only line goes, a trailing comment is cut with the whitespace before it."""
    lines = src.splitlines(keepends=True)
    drop, cut = set(), {}
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT:
            row, col = t.start
            if lines[row - 1][:col].strip() == "":
                drop.add(row - 1)
            else:
                cut[row - 1] = col
    for i, col in cut.items():
        lines[i] = lines[i][:col].rstrip() + ("\n" if lines[i].endswith("\n") else "")
    out = "".join(l for i, l in enumerate(lines) if i not in drop)
    assert ast.dump(ast.parse(out)) == ast.dump(ast.parse(src)), "comment strip changed the code"
    return out


def strip_everything(dest: Path) -> None:
    """v2: no comment or docstring in any Python file, no prose doc, no comment or description
    in pyproject.toml. Applied to every arm before its own material is added."""
    for p in dest.rglob("*.py"):
        p.write_text(strip_comments(strip_docstrings(p.read_text(encoding="utf-8"))), encoding="utf-8")
    for pat in ("*.md", "*.rst", "*.txt", "CHANGELOG*", "DIRECTION*"):
        for p in dest.rglob(pat):
            p.unlink()
    pp = dest / "pyproject.toml"
    pp.write_text("".join(l for l in pp.read_text().splitlines(keepends=True)
                          if not l.lstrip().startswith("#") and not l.startswith("description =")))


def requirements_prose() -> str:
    reqs = yaml.safe_load((SRC / "requirements.yaml").read_text())["requirements"]
    out = ["# What this project must do, and why\n",
           "Notes from the maintainers on what the tool has to guarantee and the reasons behind each "
           "guarantee.\n"]
    for r in reqs:
        why = " ".join((r.get("why") or "").split())
        out.append(r["says"] + (" " + why if why else "") + "\n")
    return "\n".join(out)


def build(arm: str, dest: Path, variant: str = "v1") -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SRC, dest, ignore=IGNORE)
    if arm in ("plain", "separate_doc"):
        (dest / "requirements.yaml").unlink()
        for p in (dest / "tests").rglob("*.py"):
            p.write_text(strip_docstrings(strip_specdeck_format(p.read_text(encoding="utf-8"))), encoding="utf-8")
        pp = dest / "pyproject.toml"
        pp.write_text(strip_pyproject(pp.read_text()))
    if variant == "v2":
        strip_everything(dest)
    if arm == "separate_doc":
        assert not (dest / "REQUIREMENTS.md").exists()
        (dest / "REQUIREMENTS.md").write_text(requirements_prose())
    if arm == "specdeck":
        env = {**os.environ, "PYTHONPATH": str(dest), "SPECDECK_HOME": str(dest.parent / f"{dest.name}_home")}
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                            "--specdeck-coverage"], cwd=dest, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError("specdeck copy's suite failed:\n" + r.stdout[-2000:])
        r = subprocess.run([sys.executable, "-m", "specdeck", "context", "--out", str(dest / "SPEC.md")],
                           cwd=dest, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        agents = dest / "AGENTS.md"
        with agents.open("a") as fh:   # append if present, create if absent
            fh.write(POINTER)
    return dest


def check(dest: Path) -> str:
    env = {**os.environ, "PYTHONPATH": str(dest), "SPECDECK_HOME": "/tmp/purpose_check_home"}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:specdeck",
                        "-p", "no:randomly"], cwd=dest, capture_output=True, text=True, env=env)
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-300:]


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "v1"
    for arm in ("plain", "separate_doc", "specdeck"):
        d = build(arm, Path(f"/tmp/purpose_{variant}_{arm}/specdeck"), variant)
        texts = [f.read_text() for f in d.joinpath("tests").rglob("*.py")]
        n_doc = sum(1 for t in texts for x in ast.walk(ast.parse(t))
                    if isinstance(x, (ast.FunctionDef, ast.ClassDef, ast.Module)) and ast.get_docstring(x))
        n_cov = sum(t.count("covers=") for t in texts)
        files = sorted(p.name for p in d.iterdir())
        print(f"{arm:12} docstrings {n_doc:>3} | covers= {n_cov:>2} | {files} | suite: {check(d)}", flush=True)
