"""Copies of proofdeck for the business-purpose replication (built like v2).

The subject is `subject/`, a frozen snapshot of proofdeck's working tree. Every copy loses
every comment, docstring and prose doc (build.strip_everything). Then:

  plain            @scenario decorators and step blocks removed; no requirements, no docs
  separate_doc     plain + REQUIREMENTS.md: each requirement's says and why as prose
                   paragraphs, no ids, no test names
  specdeck         decorators kept with covers= added from links.yaml, step text kept,
                   requirements.yaml, .specdeck/ from a real run, SPEC.md from
                   `specdeck context` (default), AGENTS.md holding a one-line pointer
  specdeck_prompt  the specdeck copy with SPEC.md from `specdeck context --task why`
                   (that document is also pasted into the prompt, see run_pd.py)

Each arm is built once into a template and verified; sessions copy the template.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from build import POINTER, strip_everything, strip_pyproject, strip_specdeck_format  # noqa: E402

SUBJECT = HERE / "subject"
IGNORE = shutil.ignore_patterns("__pycache__", ".git", "*.egg-info", ".pytest_cache", ".specdeck", ".gitignore")
TEMPLATES = Path("/tmp/pd_templates")
ARMS = ("plain", "separate_doc", "specdeck", "specdeck_prompt")


def links() -> dict:
    return yaml.safe_load((HERE / "links.yaml").read_text())


def requirements() -> list:
    return yaml.safe_load((HERE / "requirements.yaml").read_text())["requirements"]


def add_covers(src: str, mapping: dict) -> str:
    """Add covers=[...] to the existing @scenario(...) decorator of each mapped test, by
    position (ast columns are UTF-8 byte offsets)."""
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    edits = []
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in mapping:
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "scenario":
                    assert not any(k.arg == "covers" for k in d.keywords), node.name
                    ids = ", ".join(f'"{i}"' for i in mapping[node.name])
                    edits.append((d.end_lineno - 1, d.end_col_offset - 1, ids, bool(d.args or d.keywords)))
                    found.add(node.name)
    missing = set(mapping) - found
    assert not missing, f"no @scenario(...) for {missing}"
    for row, col, ids, has_args in sorted(edits, reverse=True):
        b = lines[row].encode()
        assert b[col:col + 1] == b")", (row, lines[row])
        before = (b"".join(l.encode() for l in lines[:row]) + b[:col]).rstrip()
        sep = ", " if has_args and not before.endswith(b",") else ""   # a trailing comma is already there
        if b[:col].strip() == b"":      # `)` on its own line: add an argument line above it
            lines[row] = f"    covers=[{ids}],\n" + lines[row]
        else:
            lines[row] = (b[:col] + f"{sep}covers=[{ids}]".encode() + b[col:]).decode()
    return "".join(lines)


def requirements_prose() -> str:
    out = ["# What this project must do, and why\n",
           "Notes from the maintainers on what the tool has to guarantee and the reasons behind each "
           "guarantee.\n"]
    for r in requirements():
        out.append(r["says"] + " " + " ".join(r["why"].split()) + "\n")
    return "\n".join(out)


def _env(dest: Path) -> dict:
    return {**os.environ, "PYTHONPATH": str(dest), "SPECDECK_HOME": str(dest.parent / "sdhome")}


def build(arm: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SUBJECT, dest, ignore=IGNORE)
    if arm in ("specdeck", "specdeck_prompt"):
        for fname, mapping in links().items():
            p = dest / "tests" / fname
            p.write_text(add_covers(p.read_text(encoding="utf-8"), mapping), encoding="utf-8")
        text = (HERE / "requirements.yaml").read_text()
        (dest / "requirements.yaml").write_text("".join(l for l in text.splitlines(keepends=True)
                                                        if not l.startswith("#")))
    else:
        for p in (dest / "tests").rglob("*.py"):
            p.write_text(strip_specdeck_format(p.read_text(encoding="utf-8")), encoding="utf-8")
        pp = dest / "pyproject.toml"
        pp.write_text(strip_pyproject(pp.read_text()))
    strip_everything(dest)
    if arm == "separate_doc":
        (dest / "REQUIREMENTS.md").write_text(requirements_prose())
    if arm in ("specdeck", "specdeck_prompt"):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                            "--specdeck-coverage"], cwd=dest, capture_output=True, text=True, env=_env(dest))
        if r.returncode != 0:
            raise RuntimeError("specdeck copy's suite failed:\n" + r.stdout[-3000:])
        cmd = [sys.executable, "-m", "specdeck", "context", "--out", str(dest / "SPEC.md")]
        if arm == "specdeck_prompt":
            cmd[4:4] = ["--task", "why"]
        r = subprocess.run(cmd, cwd=dest, capture_output=True, text=True, env=_env(dest))
        if r.returncode != 0:
            raise RuntimeError(r.stderr)
        (dest / "AGENTS.md").write_text(POINTER)
    return dest


def check(dest: Path) -> str:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:specdeck",
                        "-p", "no:randomly"], cwd=dest, capture_output=True, text=True, env=_env(dest))
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-300:]


def template(arm: str) -> Path:
    return TEMPLATES / arm / "proofdeck"


def instance(arm: str, dest: Path) -> Path:
    """A session's copy: the verified template, copied (the .specdeck/ run included)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(template(arm), dest)
    return dest


if __name__ == "__main__":
    import io
    import tokenize
    arms = sys.argv[1:] or ARMS
    for arm in arms:
        d = build(arm, template(arm))
        texts = [f.read_text() for f in d.rglob("*.py")]
        n_com = sum(1 for t in texts for tok in tokenize.generate_tokens(io.StringIO(t).readline)
                    if tok.type == tokenize.COMMENT)
        n_doc = sum(1 for t in texts for x in ast.walk(ast.parse(t))
                    if isinstance(x, (ast.FunctionDef, ast.ClassDef, ast.Module, ast.AsyncFunctionDef))
                    and ast.get_docstring(x))
        n_cov = sum(t.count("covers=[") for t in texts)
        docs = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.suffix in (".md", ".rst", ".txt", ".yaml"))
        print(f"{arm:16} comments {n_com} docstrings {n_doc} covers=[ {n_cov:>3} | {docs} | suite: {check(d)}",
              flush=True)
