"""Real bugs: the distribution recipe against one strong agent.

Each case (realbugs.py -> data/realbugs_cases.json) is a fix commit from the workspace's history
with its fixed source files rolled back: the commit's own tests fail. One bug per trial, every
arm on the same case, and only the commit's own test files are run (never a whole suite).

  R  the recipe. terra reads the failing output, the tests and which functions they run
     (traced on the buggy code, nearest first) and writes what must be true; gpt-4o-mini judges
     one function per call against that; terra picks at most two flagged functions and writes
     each fix. One retry round with the new output.
  T  terra alone: an agent with a read-only shell, a line-range edit tool and a test runner.

Graded: the commit's tests pass and no test file was edited. Cost: tokens per model, and USD
for gpt-4o-mini at list price. terra's price is not assumed: set REAL_TERRA_USD="in,cached,out"
(per million tokens) to price it; the token counts are kept either way.

    python3 real.py --arms R,T --cap 500000
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "navigate"))
import agent as nav  # noqa: E402  (its read-only shell)

load_dotenv("/workspaces/python/.env")
HERE = Path(__file__).resolve().parent
ROOT = Path("/workspaces/python")
LOG = HERE / os.environ.get("REAL_LOG", "real.jsonl")
STRONG, WEAK = "gpt-5.6-terra", "gpt-4o-mini"
PRICES = {WEAK: (0.15, 0.075, 0.60)}            # USD per million: input, cached input, output (list price)
if os.environ.get("REAL_TERRA_USD"):
    PRICES[STRONG] = tuple(float(x) for x in os.environ["REAL_TERRA_USD"].split(","))
MAX_FUNCS, MAX_FIX, POOL = 15, 2, 16
TRIAL_CAP = int(os.environ.get("REAL_TRIAL_CAP", "45000"))   # same for every arm, checked before each call
TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$")


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def build(case: dict) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="real-")) / case["repo"]
    dest.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(ROOT / case["repo"]), "archive", case["commit"]],
                          capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arch, check=True)
    for f in case["src"]:
        try:
            (dest / f).write_text(git(ROOT / case["repo"], "show", f"{case['commit']}^:{f}"))
        except subprocess.CalledProcessError:
            (dest / f).unlink(missing_ok=True)
    return dest


def env_for(tree: Path) -> dict:
    return {**os.environ, "PYTHONPATH": os.pathsep.join([str(tree), str(tree / "src")])}


def run_tests(tree: Path, tests: list) -> tuple:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-ra", "--tb=short", "-p", "no:cacheprovider",
                        "-p", "no:specdeck", "-p", "no:randomly", *tests], cwd=tree, env=env_for(tree),
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout[-12000:]


# The functions the failing tests run, and how many calls from the test: the same measure as
# identifiers.py and proofdeck's --profile distribute, traced on the buggy code.
TRACE = r'''
import json, os, re, sys
tree = sys.argv[1]; ids = sys.argv[2:]
TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$")
sys.path[:0] = [tree, os.path.join(tree, "src")]
seen = {}
def is_test(c):
    f = c.co_filename
    return c.co_name.startswith("test") and f.startswith(tree) and bool(TEST.search(f[len(tree) + 1:]))
def prof(frame, event, arg):
    if event != "call":
        return
    fn = frame.f_code.co_filename
    if not fn.startswith(tree + os.sep) or TEST.search(fn[len(tree) + 1:]):
        return
    d, f = 1, frame.f_back
    while f is not None and d < 80 and not is_test(f.f_code):
        f, d = f.f_back, d + 1
    if f is None or d >= 80:
        return
    key = (fn[len(tree) + 1:], frame.f_code.co_firstlineno)
    seen[key] = min(d, seen.get(key, 99))
import pytest
sys.setprofile(prof)
pytest.main(["-q", "-p", "no:cacheprovider", "-p", "no:specdeck", "-p", "no:randomly", *ids])
sys.setprofile(None)
print("REACH=" + json.dumps([[p, n, d] for (p, n), d in seen.items()]))
'''


def functions(text: str) -> list:
    """Outermost functions and methods: (qualname, first line incl. decorators, last line)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []

    def walk(node, prefix, inside):
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not inside:
                    out.append((prefix + c.name, min([d.lineno for d in c.decorator_list] + [c.lineno]), c.end_lineno))
                walk(c, prefix + c.name + ".", True)
            elif isinstance(c, ast.ClassDef):
                walk(c, prefix + c.name + ".", inside)
    walk(tree, "", False)
    return out


def traced(tree: Path, ids: list) -> list:
    p = subprocess.run([sys.executable, "-c", TRACE, str(tree), *ids], cwd=tree, env=env_for(tree),
                       capture_output=True, text=True, timeout=300)
    m = re.search(r"REACH=(.*)", p.stdout)
    best: dict = {}
    for path, line, d in (json.loads(m.group(1)) if m else []):
        owner = next(((q, s, e) for q, s, e in functions((tree / path).read_text()) if s <= line <= e), None)
        if owner:
            best[(path, *owner)] = min(d, best.get((path, *owner), 99))
    return sorted(((*k, d) for k, d in best.items()), key=lambda r: (r[4], r[0], r[2]))


def numbered(text: str, start: int) -> str:
    return "\n".join(f"{n:5d}  {line}" for n, line in enumerate(text.splitlines(), start))


def splice(tree: Path, f: str, s: int, e: int, new: str) -> str:
    p = tree / f
    if TEST.search(f) or ".." in f or f.startswith("/") or not p.is_file():
        return "rejected: not a source file"
    lines = p.read_text().splitlines()
    if not 1 <= s <= e <= len(lines):
        return "rejected: bad range"
    want = len(lines[s - 1]) - len(lines[s - 1].lstrip())
    new_lines = new.split("\n") if new else []
    first = next((l for l in new_lines if l.strip()), "")
    if new_lines and len(first) - len(first.lstrip()) < want:
        pad = " " * (want - (len(first) - len(first.lstrip())))
        new_lines = [pad + l if l.strip() else l for l in new_lines]
    lines[s - 1:e] = new_lines
    p.write_text("\n".join(lines) + "\n")
    return "applied"


class Meter:
    def __init__(self, client):
        self.client, self.rows = client, []

    def tokens(self) -> int:
        return sum(r["total"] for r in self.rows)

    def over(self) -> bool:
        return self.tokens() > TRIAL_CAP

    def chat(self, model: str, prompt: str, role: str) -> dict:
        r = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}],
                                                response_format={"type": "json_object"})
        u = r.usage
        self.rows.append({"model": model, "role": role, "input": u.prompt_tokens, "output": u.completion_tokens,
                          "cached": getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0, "total": u.total_tokens})
        m = re.search(r"\{.*\}", r.choices[0].message.content or "", re.S)
        try:
            out = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            out = {}
        return out if isinstance(out, dict) else {}

    def usd(self, model: str):
        if model not in PRICES:
            return None
        pi, pc, po = PRICES[model]
        return round(sum(((r["input"] - r["cached"]) * pi + r["cached"] * pc + r["output"] * po) / 1e6
                         for r in self.rows if r["model"] == model), 5)


EXPECT = """A Python project's tests fail. You coordinate a team; you do not read the source yourself.

Failing test output:
{out}

The failing tests' source:
{tests}

Functions the failing tests run, nearest first (calls from the test):
{fns}

Write what must be true for these tests to pass, as concretely as the tests let you say it:
the values, the case, the behaviour, and what happens instead. Workers will check each
function above against it. Reply with ONLY a JSON object: {{"expectation": "..."}}"""

VERDICT = """You are checking ONE function for the cause of failing tests in a Python project.

Failing test output (short):
{out}

What your lead says the tests need:
{expect}

The function, from {path} (line numbers are the file's):
{func}

Is the cause of the failure in this function? "bug" only if a line in it is wrong; "no" if
it looks right for what the tests need; "unsure" if you cannot tell from it alone.
Reply with ONLY a JSON object: {{"verdict": "bug|no|unsure", "why": "one sentence"}}"""

TRIAGE = """Your workers checked {n} functions. They flagged these as the cause:
{flags}

What the tests need: {expect}

Pick at most {k}, the ones most likely to be the real cause. Reply with ONLY a JSON object:
{{"fix": ["path:function", ...]}}"""

FIX = """Fix the bug in this function so the failing tests pass, without changing behaviour the
tests don't ask to change.

Failing test output (short):
{out}

What the tests need: {expect}

Why it was flagged: {why}

The function, from {path} (line numbers are the file's, between {first} and {last}):
{func}

Reply with ONLY a JSON object:
{{"start": <first line to replace>, "end": <last line to replace>, "new": "<replacement text, with indentation>"}}"""


def arm_r(m: Meter, tree: Path, case: dict) -> dict:
    rounds, checked = [], set()
    for rnd in range(2):
        rc, out = run_tests(tree, case["tests"])
        if rc == 0 or m.over():
            break
        ids = [l.split(" ")[1] for l in out.splitlines() if l.startswith(("FAILED ", "ERROR "))] or case["tests"]
        fns = [f for f in traced(tree, ids) if (f[0], f[1]) not in checked][:MAX_FUNCS]
        if not fns:
            break
        tsrc = "\n\n".join(f"--- {t} ---\n{(tree / t).read_text()[:6000]}" for t in case["tests"])[:14000]
        expect = str(m.chat(STRONG, EXPECT.format(out=out[-5000:], tests=tsrc, fns="\n".join(
            f"{d} call(s): {p}:{q}" for p, q, s, e, d in fns)), "instructor").get("expectation", ""))
        short = out[-2000:]

        def judge(f):
            p, q, s, e, d = f
            if m.over():
                return {"fn": f, "verdict": "skipped"}
            text = "\n".join((tree / p).read_text().splitlines()[s - 1:e])[:8000]
            r = m.chat(WEAK, VERDICT.format(out=short, expect=expect, path=p, func=numbered(text, s)), "worker")
            return {"fn": f, "verdict": r.get("verdict"), "why": r.get("why")}
        with ThreadPoolExecutor(POOL) as ex:
            verdicts = list(ex.map(judge, fns))
        checked |= {(f[0], f[1]) for f in fns}
        flags = [v for v in verdicts if v["verdict"] == "bug"]
        log = {"round": rnd + 1, "checked": len(fns), "expectation": expect,
               "flagged": [f"{v['fn'][0]}:{v['fn'][1]} ({v['fn'][4]})" for v in flags], "fixes": []}
        if flags and not m.over():
            pick = m.chat(STRONG, TRIAGE.format(n=len(fns), k=MAX_FIX, expect=expect, flags="\n".join(
                f"- {v['fn'][0]}:{v['fn'][1]} ({v['fn'][4]} calls deep): {v['why']}" for v in flags)), "instructor")
            wanted = {str(x) for x in pick.get("fix", []) if isinstance(x, str)}
            for v in [v for v in flags if f"{v['fn'][0]}:{v['fn'][1]}" in wanted][:MAX_FIX]:
                if m.over():
                    break
                p, q = v["fn"][0], v["fn"][1]
                lines = (tree / p).read_text().splitlines()
                s, e = next(((a, b) for n, a, b in functions("\n".join(lines)) if n == q), v["fn"][2:4])
                fx = m.chat(STRONG, FIX.format(out=short, expect=expect, why=v["why"], path=p, first=s, last=e,
                                               func=numbered("\n".join(lines[s - 1:e]), s)), "fixer")
                try:
                    res = splice(tree, p, int(fx["start"]), int(fx["end"]), str(fx.get("new", "")))
                except (KeyError, TypeError, ValueError):
                    res = "rejected: no range"
                log["fixes"].append({"fn": f"{p}:{q}", "result": res})
        rounds.append(log)
    return {"rounds": rounds}


TOOLS = [
    {"type": "function", "name": "bash", "description": "Run a read-only shell command in the repository "
     "(ls, cat, grep, sed -n, nl, find, head, tail, wc).",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"type": "function", "name": "replace_lines", "description": "Replace lines start..end (1-based, inclusive) of a "
     "source file with new text. Test files cannot be edited.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start": {"type": "integer"},
                                                     "end": {"type": "integer"}, "new": {"type": "string"}},
                    "required": ["path", "start", "end", "new"]}},
    {"type": "function", "name": "run_tests", "description": "Run the failing tests and show the result.",
     "parameters": {"type": "object", "properties": {}}},
]
SOLO = """These tests fail in the repository you can explore: {tests}

{out}

Fix the source (not the tests) so they pass. Use the tools; when they pass, or you cannot make
progress, reply with a one-line summary."""


def arm_t(m: Meter, tree: Path, case: dict, max_calls: int = 25) -> dict:
    _, out = run_tests(tree, case["tests"])
    pending = [{"role": "user", "content": SOLO.format(tests=" ".join(case["tests"]), out=out[-5000:])}]
    prev, calls, log = None, 0, []
    while calls < max_calls and not m.over():
        kw = {"previous_response_id": prev} if prev else {}
        r = m.client.responses.create(model=STRONG, input=pending, tools=TOOLS, **kw)
        u = r.usage
        m.rows.append({"model": STRONG, "role": "solo", "input": u.input_tokens, "output": u.output_tokens,
                       "cached": getattr(u.input_tokens_details, "cached_tokens", 0) or 0, "total": u.total_tokens})
        prev = r.id
        fcalls = [o for o in r.output if getattr(o, "type", "") == "function_call"]
        if not fcalls:
            break
        pending = []
        for fc in fcalls:
            calls += 1
            try:
                a = json.loads(fc.arguments or "{}")
            except json.JSONDecodeError:
                a = {}
            if fc.name == "bash":
                res = nav.run_cmd(str(a.get("command", "")), tree)
            elif fc.name == "replace_lines":
                try:
                    res = splice(tree, str(a.get("path", "")), int(a["start"]), int(a["end"]), str(a.get("new", "")))
                except (KeyError, TypeError, ValueError):
                    res = "rejected: need path, start, end, new"
            else:
                res = run_tests(tree, case["tests"])[1][-5000:]
            log.append({"tool": fc.name, "result": str(res)[:160]})
            pending.append({"type": "function_call_output", "call_id": fc.call_id, "output": str(res)})
    return {"tool_calls": calls, "log": log}


def trial(client, arm: str, case: dict) -> dict:
    tree = build(case)
    m = Meter(client)
    t0 = time.time()
    try:
        detail, err = (arm_r if arm == "R" else arm_t)(m, tree, case), None
    except Exception as exc:  # noqa: BLE001
        detail, err = {}, repr(exc)[:300]
    rc, out = run_tests(tree, case["tests"])
    edited = any((tree / t).read_text() != git(ROOT / case["repo"], "show", f"{case['commit']}:{t}")
                 for t in case["tests"] if (tree / t).exists())
    return {"arm": arm, "repo": case["repo"], "commit": case["commit"], "green": rc == 0 and not edited,
            "edited_tests": edited, "tail": out.strip().splitlines()[-1] if out.strip() else "", "error": err,
            "seconds": round(time.time() - t0), "tokens": m.tokens(),
            "tokens_by_model": {mod: sum(r["total"] for r in m.rows if r["model"] == mod) for mod in (STRONG, WEAK)},
            "usd_by_model": {mod: m.usd(mod) for mod in (STRONG, WEAK)}, "capped": m.over(),
            "ledger": m.rows, "detail": detail, "tree": str(tree)}


def spent() -> int:
    return sum(json.loads(l)["tokens"] for l in LOG.read_text().splitlines() if l.strip()) if LOG.exists() else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="R,T")
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cases = json.loads((HERE / "data" / "realbugs_cases.json").read_text())
    cases = cases[:a.limit] if a.limit else cases
    done = {(json.loads(l)["arm"], json.loads(l)["commit"]) for l in LOG.read_text().splitlines()} if LOG.exists() else set()
    jobs = [(arm, c) for c in cases for arm in a.arms.split(",") if (arm, c["commit"]) not in done]
    print(f"{len(cases)} cases, {len(jobs)} trials, spent {spent():,} of {a.cap:,}", flush=True)
    client = OpenAI()

    def go(job):
        arm, case = job
        if spent() > a.cap - TRIAL_CAP:
            return
        row = trial(client, arm, case)
        with LOG.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"{arm} {case['repo']:18} {case['commit'][:8]} green={row['green']} tok={row['tokens']:,} "
              f"(terra {row['tokens_by_model'][STRONG]:,}, mini {row['tokens_by_model'][WEAK]:,}) {row['seconds']}s "
              f"| {row['tail'][:50]} | spent {spent():,}", flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(go, jobs))


if __name__ == "__main__":
    main()
