"""Cheap check: can a small model write one function from its signature, purpose and direct tests?

The swarm idea: a strong model writes the skeleton (signatures, a purpose sentence each, a test
that calls each function directly) and small models write the bodies. This measures the second
half on real code: 24 functions from tapedeck, proofdeck and specdeck that some test calls
directly. Each worker sees the module with only its function's body removed, the purpose (the
docstring, or one sentence terra wrote from the original when there was none) and the direct
tests. One retry with the direct tests' failure output. Graded by the direct tests and then by
the repository's whole suite, which the worker never sees.

    python3 swarm.py calibrate          # stubbed function must fail its direct tests
    python3 swarm.py purpose            # terra writes purposes for undocumented functions
    SWARM_MODEL=gpt-4o-mini python3 swarm.py run
    SWARM_MODEL=qwen3:8b python3 swarm.py run
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/workspaces/python")
FUNCS = json.loads((HERE / "functions.json").read_text())
PURPOSES = HERE / "purposes.json"
MODEL = os.environ.get("SWARM_MODEL", "gpt-4o-mini")
CAP = int(os.environ.get("SWARM_CAP", "450000"))     # OpenAI tokens across all arms
LEDGER = HERE / "ledger.jsonl"
IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".specdeck", "*.egg-info", ".pytest_cache", "bench")
MAX_TESTS = 3


def fn_id(f) -> str:
    return f"{f['repo']}:{f['file']}:{f['name']}:{f['line']}"


def find(tree, f):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == f["name"] and node.lineno == f["line"]:
            return node
    raise LookupError(fn_id(f))


def body_span(node):
    """(first, last) line of the body after the docstring."""
    body = node.body[1:] if ast.get_docstring(node) else node.body
    return body[0].lineno, node.end_lineno


def stubbed(f, purpose: str = "") -> str:
    """The module with the target's body replaced by a TODO, purpose added as a docstring."""
    text = (ROOT / f["repo"] / f["file"]).read_text()
    lines = text.splitlines(keepends=True)
    node = find(ast.parse(text), f)
    a, b = body_span(node)
    ind = " " * node.body[0].col_offset
    stub = [f"{ind}...  # TODO: implement {f['name']}\n"]
    if purpose and not ast.get_docstring(node):
        stub = [f'{ind}"""{purpose}"""\n'] + stub
    return "".join(lines[:a - 1] + stub + lines[b:])


def test_context(f) -> str:
    """The direct tests, with the imports and top-level helpers they mention."""
    out = []
    tests = sorted(f["tests"], key=lambda n: len(n))[:MAX_TESTS]
    by_file = {}
    for nid in tests:
        path, *parts = nid.split("::")
        by_file.setdefault(path, []).append([p.split("[")[0] for p in parts])
    for path, wanted in by_file.items():
        src = (ROOT / f["repo"] / path).read_text()
        tree = ast.parse(src)
        seg = lambda n: ast.get_source_segment(src, n, padded=True)
        picked = []
        for parts in wanted:
            scope = tree.body
            for p in parts[:-1]:
                scope = next(n.body for n in scope if isinstance(n, ast.ClassDef) and n.name == p)
            node = next(n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == parts[-1])
            picked.append(node)
        used = " ".join(seg(n) for n in picked)
        keep = []
        for n in tree.body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                keep.append(seg(n))
            elif n in picked:
                continue
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
                names = [n.name] if hasattr(n, "name") else [t.id for t in getattr(n, "targets", [getattr(n, "target", None)]) if isinstance(t, ast.Name)]
                decorated = isinstance(n, ast.FunctionDef) and any("fixture" in ast.unparse(d) for d in n.decorator_list)
                if any(re.search(rf"\b{re.escape(x)}\b", used) for x in names) or decorated:
                    keep.append(seg(n))
        body = "\n".join(keep) + "\n\n" + "\n\n".join(seg(n) for n in picked)
        out.append(f"# {path}\n{body}")
    conftest = ROOT / f["repo"] / "tests" / "conftest.py"
    if conftest.exists() and "conftest" in "".join(out) and len(conftest.read_text()) < 12000:
        out.append(f"# tests/conftest.py\n{conftest.read_text()}")
    return "\n\n".join(out), tests


def prompt(f, purpose: str) -> str:
    module = stubbed(f, purpose)
    tests, _ = test_context(f)
    return f"""Implement one function in an existing Python codebase.

File `{f['file']}` is shown below with the body of `{f['name']}` removed (marked TODO). Everything
else in the file is final. Write the body so the tests below pass. Do not change the tests.

Return ONLY the complete function `{f['name']}` (its `def` line, docstring and body) in one
```python block. If you need a new import, put it inside the function body.

=== {f['file']} ===
{module}
=== tests that call {f['name']} directly ===
{tests}
"""


# ---- models ---------------------------------------------------------------------------------

def spent() -> int:
    if not LEDGER.exists():
        return 0
    return sum(json.loads(l)["total"] for l in LEDGER.read_text().splitlines()
               if json.loads(l)["model"] != "qwen3:8b")


def call(model: str, text: str, tag: str) -> str:
    t0 = time.time()
    if model.startswith("qwen"):
        body = {"model": model, "prompt": text, "stream": False, "think": False,
                "options": {"num_predict": 4096, "temperature": 0, "num_ctx": 16384}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(body, fh)
        remote, out = f"/tmp/swarm_req_{os.getpid()}.json", f"/tmp/swarm_res_{os.getpid()}.json"
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", fh.name, f"macmini-tunnel:{remote}"], check=True, timeout=120)
        subprocess.run(["ssh", "-o", "BatchMode=yes", "macmini-tunnel",
                        f"curl -s --max-time 1500 http://127.0.0.1:11434/api/generate -H 'Content-Type: application/json' "
                        f"--data-binary @{remote} -o {out}"], check=True, timeout=1600)
        d = json.loads(subprocess.run(["ssh", "-o", "BatchMode=yes", "macmini-tunnel", f"cat {out}"], check=True,
                                      capture_output=True, text=True, timeout=120).stdout)
        rec = {"prompt": d.get("prompt_eval_count", 0), "completion": d.get("eval_count", 0)}
        reply = d.get("response", "")
    else:
        if spent() >= CAP:
            raise SystemExit(f"token cap {CAP} reached")
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv(ROOT / ".env")
        r = OpenAI().chat.completions.create(model=model, messages=[{"role": "user", "content": text}])
        u = r.usage
        rec = {"prompt": u.prompt_tokens, "completion": u.completion_tokens}
        reply = r.choices[0].message.content or ""
    rec.update(model=model, tag=tag, total=rec["prompt"] + rec["completion"], seconds=round(time.time() - t0, 1))
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return reply


# ---- splicing and grading -------------------------------------------------------------------

def extract(reply: str, f) -> str | None:
    """The function `f` from the reply, dedented; None when it is not there or does not parse."""
    for block in re.findall(r"```(?:python|py)?\s*\n(.*?)```", reply, re.S) or [reply]:
        try:
            tree = ast.parse(textwrap.dedent(block))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == f["name"]:
                src = textwrap.dedent(block)
                lines = src.splitlines(keepends=True)
                first = min([d.lineno for d in node.decorator_list] + [node.lineno])
                return textwrap.dedent("".join(lines[first - 1:node.end_lineno]))
    return None


def spliced(f, fn_src: str) -> str:
    text = (ROOT / f["repo"] / f["file"]).read_text()
    lines = text.splitlines(keepends=True)
    node = find(ast.parse(text), f)
    first = min([d.lineno for d in node.decorator_list] + [node.lineno])
    ind = " " * node.col_offset
    new = textwrap.indent(fn_src.rstrip() + "\n", ind, lambda l: l.strip() != "")
    return "".join(lines[:first - 1]) + new + "".join(lines[node.end_lineno:])


def pytest(tree: Path, args: list) -> tuple:
    env = {**os.environ, "PYTHONPATH": str(tree), "HOME": os.environ.get("HOME", "/tmp")}
    try:
        p = subprocess.run(["bash", "-c", "ulimit -v 4000000; exec \"$@\"", "_", sys.executable, "-m", "pytest", "-q",
                            "-x" if args else "-q", "--tb=short", "-p", "no:cacheprovider", "-p", "no:randomly", *args],
                           cwd=tree, env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return p.returncode == 0, p.stdout[-2500:]


def grade(f, module_text: str, tests: list) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="swarm-"))
    tree = tmp / f["repo"]
    try:
        shutil.copytree(ROOT / f["repo"], tree, ignore=IGNORE)
        (tree / f["file"]).write_text(module_text)
        ok, out = pytest(tree, tests)
        res = {"direct": ok, "direct_out": out}
        if ok:
            full, fout = pytest(tree, [])
            res.update(full=full, full_out=fout[-800:])
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- commands -------------------------------------------------------------------------------

def calibrate():
    """Original passes its direct tests; a stub fails them (so the tests actually test it)."""
    keep = []
    for f in FUNCS:
        _, tests = test_context(f)
        orig = grade(f, (ROOT / f["repo"] / f["file"]).read_text(), tests)["direct"]
        stub = grade(f, stubbed(f), tests)["direct"]
        print(f"{fn_id(f):70} original={orig} stub={stub}", flush=True)
        if orig and not stub:
            keep.append(f)
    print(f"{len(keep)}/{len(FUNCS)} usable")
    (HERE / "functions_ok.json").write_text(json.dumps(keep, indent=1))


def purpose():
    out = json.loads(PURPOSES.read_text()) if PURPOSES.exists() else {}
    for f in json.loads((HERE / "functions_ok.json").read_text()):
        if f["doc"] or fn_id(f) in out:
            continue
        text = (ROOT / f["repo"] / f["file"]).read_text()
        src = ast.get_source_segment(text, find(ast.parse(text), f))
        reply = call("gpt-5.6-terra", f"""You are designing this function for someone else to implement. Write its
docstring: one or two sentences saying what it is for and what it returns or does, in terms of
inputs and outputs. Do not describe how it works internally. Reply with the sentences only.

{src}""", "purpose")
        out[fn_id(f)] = reply.strip().strip('"')
        print(fn_id(f), "->", out[fn_id(f)], flush=True)
        PURPOSES.write_text(json.dumps(out, indent=1))


def run():
    purposes = json.loads(PURPOSES.read_text()) if PURPOSES.exists() else {}
    log = HERE / f"results_{MODEL.replace(':', '_')}.jsonl"
    done = {json.loads(l)["fn"] for l in log.read_text().splitlines()} if log.exists() else set()
    for f in json.loads((HERE / "functions_ok.json").read_text()):
        fid = fn_id(f)
        if fid in done:
            continue
        p = prompt(f, purposes.get(fid, ""))
        _, tests = test_context(f)
        attempts = []
        for attempt in range(2):
            reply = call(MODEL, p, fid)
            src = extract(reply, f)
            g = grade(f, spliced(f, src), tests) if src else {"direct": False, "direct_out": "no function in reply"}
            attempts.append({"src": src, **{k: v for k, v in g.items()}})
            if g["direct"]:
                break
            p += f"\n=== your previous answer ===\n```python\n{src or reply[:3000]}\n```\n" \
                 f"=== it failed the tests ===\n{g['direct_out'][-1500:]}\nFix the function. Return only the function."
        rec = {"fn": fid, "model": MODEL, "body_lines": f["body_lines"], "doc": f["doc"],
               "direct_first": attempts[0]["direct"], "direct": attempts[-1]["direct"],
               "full": attempts[-1].get("full", False), "attempts": attempts}
        with log.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"{MODEL:12} {fid:70} first={rec['direct_first']} direct={rec['direct']} full={rec['full']}", flush=True)


if __name__ == "__main__":
    {"calibrate": calibrate, "purpose": purpose, "run": run}[sys.argv[1]]()
