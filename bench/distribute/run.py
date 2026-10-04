"""Does a spec make it easier for a strong model to hand debugging work to workers?

Three bugs are planted in tapedeck (bugs.py). An instructor model that cannot read the
code sees only the failing suite and splits the diagnosis into jobs. Each job goes to a
worker that sees only what the instructor asked for: a question, up to three source
files, and the named tests with their failure output. The instructor gets the workers'
reports back and writes the patches itself. If the suite is still red it may dispatch
one more round.

Arms differ only in what the suite tells the instructor and the workers:
  plain  pytest with specdeck off: every affected test fails (12)
  spec   pytest with specdeck on: 6 fail, 7 are skipped as blocked by a failing
         prerequisite, plus `specdeck context --task stuck` (the spec with statuses);
         each worker also gets the spec entries for its tests
The patched tree is always graded with specdeck off, so skipped is never green.

    python3 run.py --arm spec --worker gpt-5.4-mini --cap 360000 --ledger part2.jsonl
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent_ab"))
from groundtruth import run_suite  # noqa: E402
from bugs import BUG_SET, bugs, fixed, fresh_copy, plant  # noqa: E402

load_dotenv("/workspaces/python/.env")
HERE = Path(__file__).resolve().parent
INSTRUCTOR = "gpt-5.6-terra"
MAX_FILES, MAX_TESTS, MAX_JOBS, ROUNDS = 3, 3, 12, 2
OUTPUT_LIMIT = 14000   # characters of pytest output shown to the instructor

INSTRUCT = """You lead a small debugging team on a Python repository. You cannot read the code
yourself; your workers can. Your job: get the test suite green by fixing the source
code (never the tests).

Each job you dispatch goes to one worker, who sees only:
  - your question
  - the full text of up to {mf} repository files you name (source files, conftest, fakes)
  - the source and failure output of up to {mt} tests you name
Workers do not see each other's jobs or anything else. Each returns a JSON report with
the root cause, its location, and a proposed fix as an exact text replacement.

Dispatch as few or as many jobs as you judge best (at most {mj}). Workers cost money;
duplicate work is waste, but a missed root cause leaves the suite red.

Repository files (path, lines):
{files}

The repository's AGENTS.md:
{agents}

{evidence}

Reply with ONLY a JSON object:
{{"jobs": [{{"id": "j1", "question": "...", "files": ["path", ...], "tests": ["tests/x.py::test_y", ...]}}]}}"""

AGGREGATE = """Your workers' reports:

{reports}

Now write the fix. Each patch is an exact text replacement in one source file under
tapedeck/ (tests may not be edited): `old` must appear exactly once in the file as the
workers quoted it. Reply with ONLY a JSON object:
{{"patches": [{{"file": "tapedeck/...", "old": "...", "new": "..."}}], "note": "one sentence"}}"""

RETRY = """Your patches were applied ({applied}). The suite now says:

{evidence}

You may dispatch one more round of jobs, in the same JSON format as before
({{"jobs": [...]}}), or reply {{"jobs": []}} to stop."""

WORKER = """You are diagnosing a failing test suite in a Python repository. Your lead asks:

{question}

{spec}Failing tests (source, then failure output):
{tests}

Repository files you were given:
{files}

Reply with ONLY a JSON object:
{{"cause": "one or two sentences", "file": "path", "line": 123,
  "fix": {{"file": "path", "old": "exact text from the file", "new": "replacement"}},
  "confidence": "high|medium|low"}}
Use "fix": null if you cannot find the cause in what you were given."""


def call(client, model: str, messages: list, ledger: list, role: str) -> str:
    r = client.chat.completions.create(model=model, messages=messages,
                                       response_format={"type": "json_object"})
    u = r.usage
    ledger.append({"role": role, "model": model, "prompt": u.prompt_tokens, "completion": u.completion_tokens,
                   "total": u.total_tokens,
                   "cached": getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0,
                   "reasoning": getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0})
    return r.choices[0].message.content or ""


def parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def pytest_output(tree: Path, specdeck: bool) -> str:
    args = [sys.executable, "-m", "pytest", "-q", "-ra", "--tb=short", "-p", "no:cacheprovider"]
    args += [] if specdeck else ["-p", "no:specdeck"]
    p = subprocess.run(args, cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)},
                       capture_output=True, text=True, timeout=600)
    out = p.stdout
    return out if len(out) <= OUTPUT_LIMIT else out[:OUTPUT_LIMIT // 2] + "\n[...cut...]\n" + out[-OUTPUT_LIMIT // 2:]


def spec_context(tree: Path) -> str:
    p = subprocess.run([sys.executable, "-m", "specdeck", "context", "--task", "stuck"], cwd=tree,
                       env={**os.environ, "PYTHONPATH": str(tree)}, capture_output=True, text=True, timeout=120)
    return p.stdout


def evidence(tree: Path, arm: str) -> str:
    out = "Test output (pytest -q -ra --tb=short):\n" + pytest_output(tree, specdeck=(arm == "spec"))
    if arm == "spec":
        out += ("\n\nThe suite's spec, generated by specdeck from the tests and the run above. Tests whose "
                "prerequisite scenario failed are skipped as blocked rather than run:\n\n" + spec_context(tree))
    return out


def file_list(tree: Path) -> str:
    rows = []
    for p in sorted(tree.rglob("*.py")):
        rel = p.relative_to(tree)
        if "__pycache__" in rel.parts or rel.parts[0] in ("bench",) or "_live" in rel.parts:
            continue
        rows.append(f"{rel} {len(p.read_text().splitlines())}")
    return "\n".join(rows)


def test_source(tree: Path, nodeid: str) -> str:
    path, _, name = nodeid.partition("::")
    name = re.sub(r"\[.*\]$", "", name)
    p = tree / path
    if not p.is_file() or ".." in path:
        return f"(no such test file: {path})"
    text = p.read_text()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = text.splitlines()
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            return "\n".join(lines[start - 1:node.end_lineno])
    return f"(no test {name} in {path})"


def failure_of(output: str, nodeid: str) -> str:
    """The --tb=short block for one test, or its SKIPPED/FAILED summary line."""
    name = nodeid.split("::")[-1]
    m = re.search(r"_{3,} " + re.escape(name) + r" _{3,}\n(.*?)(?=\n_{3,} |\n={3,} )", output, re.S)
    if m:
        return m.group(1)[-3000:]
    lines = [l for l in output.splitlines() if name in l and l.startswith(("FAILED", "SKIPPED", "ERROR"))]
    return "\n".join(lines) or "(this test is not failing in the latest run)"


def spec_entries(spec: str, tests: list) -> str:
    """The spec's scenario blocks whose logic runs one of these tests."""
    blocks = re.split(r"\n(?=- id: )", spec)
    keep = [b for b in blocks if any(f"pytest {t}" in b for t in tests)]
    return "\n".join(keep)


def run_worker(client, model, tree, job, output, spec, ledger) -> dict:
    files = [f for f in job.get("files", [])[:MAX_FILES] if isinstance(f, str)]
    tests = [t for t in job.get("tests", [])[:MAX_TESTS] if isinstance(t, str)]
    ftext = []
    for f in files:
        p = (tree / f)
        ok = ".." not in f and not f.startswith("/") and p.is_file()
        ftext.append(f"--- {f} ---\n" + (p.read_text() if ok else "(no such file)"))
    ttext = [f"--- {t} ---\n{test_source(tree, t)}\n\nfailure output:\n{failure_of(output, t)}" for t in tests]
    spec_part = ""
    if spec:
        entries = spec_entries(spec, tests)
        if entries:
            spec_part = "What the suite's spec says about these tests (generated by specdeck):\n" + entries + "\n\n"
    prompt = WORKER.format(question=job.get("question", ""), spec=spec_part,
                           tests="\n\n".join(ttext) or "(none named)", files="\n\n".join(ftext) or "(none named)")
    rep = parse(call(client, model, [{"role": "user", "content": prompt}], ledger, "worker"))
    rep = rep if isinstance(rep, dict) else {}
    return {"job": job.get("id"), "files": files, "tests": tests, "report": rep}


def apply_patches(tree: Path, patches: list) -> list:
    results = []
    for pt in patches:
        f, old, new = pt.get("file", ""), pt.get("old", ""), pt.get("new", "")
        p = tree / f
        if not f.startswith("tapedeck/") or ".." in f or not p.is_file() or not old:
            results.append("rejected")
            continue
        text = p.read_text()
        n = text.count(old)
        if n != 1:
            results.append(f"old found {n}x")
            continue
        p.write_text(text.replace(old, new))
        results.append("applied")
    return results


def bug_of_tests(tests: list) -> set:
    return {b["id"] for b in bugs() for t in tests if re.sub(r"\[.*\]$", "", t) in b["direct"] + b["blocked"]}


def located(report: dict) -> set:
    """Bugs a worker's report points at: right file, within 3 lines."""
    hit = set()
    fixes = report.get("fix")
    fixes = [x for x in (fixes if isinstance(fixes, list) else [fixes]) if isinstance(x, dict)]  # some workers send several
    for b in bugs():
        tail = b["path"].split("tapedeck/", 1)[-1]
        line = report.get("line")
        if str(report.get("file") or "").endswith(tail) and isinstance(line, int) and abs(line - b["line"]) <= 3:
            hit.add(b["id"])
        if any(str(x.get("file") or "").endswith(tail) and b["mutated"].strip() in str(x.get("old") or "") for x in fixes):
            hit.add(b["id"])
    return hit


def trial(client, arm: str, worker_model: str, seed: int, ledger: list) -> dict:
    tree = fresh_copy(prefix=f"dist-{arm}-")
    plant(tree)
    rounds, all_jobs = [], []
    ev = evidence(tree, arm)
    agents = (tree / "AGENTS.md").read_text() if (tree / "AGENTS.md").exists() else "(none)"
    messages = [{"role": "user", "content": INSTRUCT.format(mf=MAX_FILES, mt=MAX_TESTS, mj=MAX_JOBS, files=file_list(tree),
                                                             agents=agents, evidence=ev)}]
    for rnd in range(ROUNDS):
        reply = call(client, INSTRUCTOR, messages, ledger, "instructor")
        messages.append({"role": "assistant", "content": reply})
        jobs = [j for j in parse(reply).get("jobs", []) if isinstance(j, dict)][:MAX_JOBS]
        if not jobs:
            break
        output = pytest_output(tree, specdeck=(arm == "spec"))
        spec = spec_context(tree) if arm == "spec" else ""
        reports = [run_worker(client, worker_model, tree, j, output, spec, ledger) for j in jobs]
        for r in reports:
            r["targets"] = sorted(bug_of_tests(r["tests"]))
            r["located"] = sorted(located(r["report"]))
            r["round"] = rnd + 1
        all_jobs += reports
        messages.append({"role": "user", "content": AGGREGATE.format(reports=json.dumps(
            [{"job": r["job"], **r["report"]} for r in reports], indent=1))})
        reply = call(client, INSTRUCTOR, messages, ledger, "instructor")
        messages.append({"role": "assistant", "content": reply})
        applied = apply_patches(tree, parse(reply).get("patches", []))
        suite = run_suite(tree, specdeck=False)
        rounds.append({"round": rnd + 1, "jobs": len(jobs), "patches": applied, "failed_after": len(suite["failed"]),
                       "tail": suite["tail"]})
        if not suite["failed"] and suite["rc"] == 0:
            break
        if rnd + 1 < ROUNDS:
            messages.append({"role": "user", "content": RETRY.format(applied=", ".join(applied) or "none",
                                                                     evidence=evidence(tree, arm))})
    final = run_suite(tree, specdeck=False)
    tok = lambda role: sum(c["total"] for c in ledger if c["role"] == role)  # noqa: E731
    return {
        "arm": arm, "bugs": BUG_SET, "worker": worker_model, "seed": seed, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "green": not final["failed"] and final["rc"] == 0, "failed_after": final["failed"],
        "bugs_fixed": fixed(tree), "rounds": rounds, "jobs": len(all_jobs),
        "jobs_per_bug": {b["id"]: sum(b["id"] in j["targets"] for j in all_jobs) for b in bugs()},
        "jobs_no_bug": sum(not j["targets"] for j in all_jobs),
        "jobs_located": sum(bool(j["located"]) for j in all_jobs),
        "jobs_on_blocked_only": sum(all(t.split("[")[0] in {x for b in bugs() for x in b["blocked"]} for t in j["tests"])
                                    and bool(j["tests"]) for j in all_jobs),
        "instructor_tokens": tok("instructor"), "worker_tokens": tok("worker"),
        "tokens": sum(c["total"] for c in ledger), "ledger": ledger, "job_log": all_jobs, "tree": str(tree),
    }


def spent(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(json.loads(l)["tokens"] for l in path.read_text().splitlines() if l.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["plain", "spec"], action="append", required=True)
    ap.add_argument("--worker", default=INSTRUCTOR)
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--reserve", type=int, default=80000, help="don't start a trial with less than this left")
    a = ap.parse_args()
    log = HERE / a.ledger
    client = OpenAI()
    for seed in range(a.seeds):
        for arm in a.arm:
            used = spent(log)
            if a.cap - used < a.reserve:
                print(f"stop: {used:,} of {a.cap:,} spent", flush=True)
                return
            ledger = []
            try:
                t = trial(client, arm, a.worker, seed, ledger)
            except Exception as exc:  # keep what the crashed trial spent on the ledger
                with log.open("a") as fh:
                    fh.write(json.dumps({"arm": arm, "bugs": BUG_SET, "worker": a.worker, "seed": seed, "crashed": repr(exc),
                                         "tokens": sum(c["total"] for c in ledger), "ledger": ledger}) + "\n")
                print(f"{BUG_SET} {arm:5s} seed{seed} CRASHED {exc!r} | spent {spent(log):,}", flush=True)
                continue
            with log.open("a") as fh:
                fh.write(json.dumps(t) + "\n")
            print(f"{BUG_SET} {arm:5s} seed{seed} green={t['green']} fixed={sum(t['bugs_fixed'].values())}/3 "
                  f"rounds={len(t['rounds'])} jobs={t['jobs']} per_bug={t['jobs_per_bug']} "
                  f"no_bug={t['jobs_no_bug']} located={t['jobs_located']} "
                  f"instr={t['instructor_tokens']:,} work={t['worker_tokens']:,} | spent {spent(log):,}", flush=True)


if __name__ == "__main__":
    main()
