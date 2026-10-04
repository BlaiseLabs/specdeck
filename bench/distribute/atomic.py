"""Smaller jobs for weak workers, with and without specdeck's coverage map.

gpt-4o-mini failed every quiet-bug trial in run.py's format (see RESULTS.md, round 2).
Its reports showed three separate failures: it could not quote code exactly (9 of 20
fixes rejected for changed quotes or joined lines), it fixed where the symptom showed
rather than where the cause was, and it never said the code it was given was the wrong
code. These arms break the job down one step at a time, so each step's effect is
measured on its own:

  A   run.py's format unchanged (the control the earlier plain trials belong to)
  B   A, but code is shown with line numbers and every fix is "replace lines s..e with
      this", spliced by the harness. Removes the copying.
  C0  B's splice, and each job is ONE function: "is the cause of this failure in this
      function?" The functions are every function in the files the instructor names.
      No specdeck.
  C1  C0, but the functions are the ones the named tests actually run, from specdeck's
      coverage map of the last green run (pytest --specdeck-coverage), and the
      instructor sees that map for the failing tests. C1 against C0 is specdeck's
      contribution; nothing else differs.

The instructor (gpt-5.6-terra) never reads code in any arm. Graded with the full suite,
specdeck off, plus two checks the suite misses: whether each planted line was restored
as it was, and whether a string holding a secret still comes out redacted.

    python3 atomic.py --arms A,B,C0,C1 --seeds 3 --cap 1100000
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

import run as base
from bugs import PKG, REPO, SUBJECT, bugs, fresh_copy, plant
from groundtruth import run_suite

HERE = Path(__file__).resolve().parent
LOG = HERE / os.environ.get("ATOMIC_LOG", "atomic.jsonl" if SUBJECT == "tapedeck" else f"atomic_{SUBJECT}.jsonl")
WORKER_MODEL = os.environ.get("ATOMIC_WORKER", "gpt-4o-mini")
MAX_INVESTIGATIONS, MAX_FUNCS, MAX_TESTS_PER_INV, MAX_FILES_C0 = 3, 25, 2, 2
# v1 (seed 0 calibration) asked for verdict and fix in one call; gpt-4o-mini flagged the right
# functions but left the fix out. v2 splits them: a verdict call per function, then a fix
# call for each function flagged.
# v3: in v2 two fix workers copied the template's example line number (12) instead of a
# real one; the template now has no numbers, and a fix outside the flagged function is
# sent back once.
C_VERSION = 3
# v4 arms S and W (2026-10-04): C1's coverage routing plus (1) test ids resolved against the
# map (a parser cut ids at spaces: test_x["""Sum item] -> test_x["""Sum) and (2) for each
# investigation the instructor states what the tests need, shown to every worker. They
# differ in one thing: in S the strong model (terra) writes each fix, in W the worker does.
MAPPED_ARMS = ("C1", "S", "W")
V4_ARMS = ("S", "W")
# Stop spending in a trial past this. Checked before every model call, not only between
# rounds: one proofdeck round once cost 250k on its own.
TRIAL_CAP = int(os.environ.get("ATOMIC_TRIAL_CAP", "200000"))
POOL = 8

# --------------------------------------------------------------------------- prompts

INSTRUCT_C = """You lead a small debugging team on a Python repository. You cannot read the code
yourself; your workers can. Your job: get the test suite green by fixing the source
code (never the tests).

Work is split into investigations. Each investigation names 1-{mt} failing tests that you
think share a cause. {where}
Each worker looks at exactly ONE function, with the named tests' source and failure
output, and answers whether the cause of the failure is in that function. For each
function flagged, another worker writes the fix as a replacement for a range of
numbered lines. Workers do not see
each other's answers. You will get back every function a worker flagged, with the
current text of the lines it wants to replace.

At most {mi} investigations per round. Each costs one worker call per function checked,
so investigations that duplicate each other are waste, but a missed root cause leaves
the suite red.

Repository files (path, lines):
{files}

The repository's AGENTS.md:
{agents}

{evidence}

Reply with ONLY a JSON object:
{{"investigations": [{{"id": "i1", "tests": ["tests/x.py::test_y"]{files_field}}}]}}"""

WHERE_C0 = """Name up to {nf} source files where the cause could be; their functions are checked (at
most {mf} per investigation, taken in turn from each file you name, in file order)."""
EXPECT_NOTE = """
For each investigation also write `expectation`: what must be true for its tests to pass,
as concretely as the tests let you say it — the values, the case, the variant or branch that
should be in effect, and what happens instead. Workers check each function against it.
"""

WHERE_C1 = """specdeck recorded, on the last green run, which functions each test executes; the map
for the failing tests is below. Every function the named tests run is checked (at most
{mf} per investigation, so tests that run fewer functions are cheaper to investigate)."""

AGGREGATE_LINES = """Your workers' findings:

{reports}

Now write the fix as line replacements. Line numbers are the files' current numbering;
`new` replaces lines start..end inclusive (it may have more or fewer lines). Only source
files under {pkg}/ (tests may not be edited); ranges in one file must not overlap.
Reply with ONLY a JSON object:
{{"patches": [{{"file": "{pkg}/...", "start": 10, "end": 11, "new": "..."}}], "note": "one sentence"}}"""

RETRY_C = """Your patches were applied ({applied}). The suite now says:

{evidence}

You may run one more round of investigations, in the same JSON format as before
({{"investigations": [...]}}), or reply {{"investigations": []}} to stop."""

WORKER_FN = """You are checking ONE function for the cause of a failing test in a Python repository.

Failing tests (source, then failure output):
{tests}

The function, from {path} (line numbers are the file's):
{func}

Is the cause of these failures in this function? Answer "bug" only if a line in this
function is wrong; "no" if the function looks correct for what these tests need; "unsure"
if you cannot tell from this function alone.
Reply with ONLY a JSON object: {{"verdict": "bug|no|unsure", "why": "one sentence"}}"""

WORKER_FIX = """A failing test has been traced to ONE function in a Python repository. Write the fix.

Failing tests (source, then failure output):
{tests}

The function, from {path} (line numbers are the file's):
{func}

Why it was flagged: {why}

Replace the wrong line or lines. "start" and "end" are line numbers as printed beside the
function above (between {first} and {last}). Reply with ONLY a JSON object:
{{"start": <first line to replace>, "end": <last line to replace>,
  "new": "<the replacement text for those lines, with indentation>"}}"""

WORKER_B = """You are diagnosing a failing test suite in a Python repository. Your lead asks:

{question}

Failing tests (source, then failure output):
{tests}

Repository files you were given (each line starts with its line number):
{files}

Reply with ONLY a JSON object:
{{"cause": "one or two sentences", "file": "path", "start": 12, "end": 12,
  "new": "replacement text for lines start..end, with indentation",
  "confidence": "high|medium|low"}}
Leave out start/end/new if you cannot find the cause in what you were given."""

# --------------------------------------------------------------------------- code helpers


def numbered(text: str, start: int = 1) -> str:
    return "\n".join(f"{n:5d}  {line}" for n, line in enumerate(text.splitlines(), start))


def functions(tree: Path, rel: str) -> list:
    """Outermost functions and methods of one file: (qualname, start, end). A nested
    function is checked as part of the function that contains it."""
    p = tree / rel
    if not p.is_file():
        return []
    out = []

    def walk(node, prefix):
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([d.lineno for d in c.decorator_list] + [c.lineno])
                out.append((prefix + c.name, start, c.end_lineno))
            elif isinstance(c, ast.ClassDef):
                walk(c, prefix + c.name + ".")
    try:
        walk(ast.parse(p.read_text()), "")
    except SyntaxError:
        return []
    return out


def locate(tree: Path, key: str):
    """'tapedeck/x.py:Cls.meth' or 'tapedeck/x.py:f.inner' -> (rel, qualname, start, end)."""
    rel, _, qual = key.partition(":")
    fns = functions(tree, rel)
    parts = qual.split(".")
    for i in range(len(parts), 0, -1):          # walk up to the outermost function that owns it
        for name, s, e in fns:
            if name == ".".join(parts[:i]):
                return rel, name, s, e
    return None


def splice(tree: Path, patches: list) -> list:
    """Apply line-range replacements. Indentation of `new` is shifted to the first replaced
    line's when it came back flush-left, the commonest way a replacement goes wrong."""
    results = [None] * len(patches)
    by_file: dict = {}
    for i, pt in enumerate(patches):
        f = str(pt.get("file", ""))
        try:
            s, e = int(pt.get("start")), int(pt.get("end"))
        except (TypeError, ValueError):
            results[i] = "rejected: no line range"
            continue
        if not f.startswith(f"{PKG}/") or ".." in f or not (tree / f).is_file():
            results[i] = "rejected: not a source file"
            continue
        by_file.setdefault(f, []).append((s, e, str(pt.get("new", "")), i))
    for f, items in by_file.items():
        lines = (tree / f).read_text().splitlines()
        items.sort(key=lambda x: -x[0])
        taken = []
        for s, e, new, i in items:
            if not (1 <= s <= e <= len(lines)) or any(not (e < ts or s > te) for ts, te in taken):
                results[i] = "rejected: bad or overlapping range"
                continue
            want = len(lines[s - 1]) - len(lines[s - 1].lstrip())
            new_lines = new.split("\n") if new else []
            first = next((l for l in new_lines if l.strip()), "")
            have = len(first) - len(first.lstrip())
            if new_lines and have < want:
                pad = " " * (want - have)
                new_lines = [pad + l if l.strip() else l for l in new_lines]
            lines[s - 1:e] = new_lines
            taken.append((s, e))
            results[i] = "applied"
        (tree / f).write_text("\n".join(lines) + "\n")
    return results


def current(tree: Path, f: str, s, e) -> str:
    try:
        lines = (tree / f).read_text().splitlines()
        s, e = int(s), int(e)
        return "\n".join(lines[s - 1:e]) if 1 <= s <= e <= len(lines) else "(lines out of range)"
    except (OSError, TypeError, ValueError):
        return "(no such lines)"


# --------------------------------------------------------------------------- coverage


def coverage_map() -> dict:
    """nodeid -> [path:function] from a green run of pristine tapedeck with --specdeck-coverage."""
    from bugs import NARROW
    cache = HERE / "data" / (("coverage_by_test" if SUBJECT == "tapedeck" else f"coverage_by_test_{SUBJECT}")
                             + ("_narrow" if NARROW else "") + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    tree = fresh_copy(prefix="cov-green-")
    shutil.rmtree(tree / ".specdeck", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--specdeck-coverage"],
                   cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)}, capture_output=True, check=True)
    import yaml
    cov = json.loads((tree / ".specdeck" / "coverage.json").read_text())
    spec = yaml.safe_load((tree / ".specdeck" / "spec.yaml").read_text())
    out = {sc["logic"][0][len("pytest "):]: [f for f in cov.get(sc["id"], []) if f.startswith(f"{PKG}/")]
           for sc in spec["scenarios"]}
    cache.write_text(json.dumps(out, indent=1))
    return out


def coverage_evidence(cov: dict, failing: list) -> str:
    rows = ["Functions each failing test runs (specdeck coverage map, last green run):"]
    for n in failing:
        fns = cov.get(re.sub(r"\[.*\]$", "", n), [])
        rows.append(f"{n}  ({len(fns)} functions)")
        by_file: dict = {}
        for f in fns:
            p, _, q = f.partition(":")
            by_file.setdefault(p, []).append(q)
        for p, qs in by_file.items():
            rows.append(f"    {p}: {', '.join(qs)}")
    return "\n".join(rows)


# --------------------------------------------------------------------------- grading


def restored(tree: Path) -> dict:
    """Is each planted function now exactly as in pristine tapedeck?"""
    out = {}
    for b in bugs():
        def fn_text(root):
            for name, s, e in functions(root, b["path"]):
                if s <= b["line"] <= e:
                    return "\n".join((root / b["path"]).read_text().splitlines()[s - 1:e])
            return None
        pristine = fn_text(REPO)
        # find the same-named function in the patched tree (lines may have moved)
        name = next((n for n, s, e in functions(REPO, b["path"]) if s <= b["line"] <= e), None)
        now = None
        for n, s, e in functions(tree, b["path"]):
            if n == name:
                now = "\n".join((tree / b["path"]).read_text().splitlines()[s - 1:e])
        out[b["id"]] = pristine is not None and now == pristine
    return out


def redaction_holds(tree: Path):
    if SUBJECT != "tapedeck":
        return None
    code = ("from tapedeck.engine.serialize import Redactor, snapshot\n"
            "print(snapshot('Bearer sk-live-abc123', Redactor(['sk-live-abc123']), {}))")
    p = subprocess.run([sys.executable, "-c", code], cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)},
                       capture_output=True, text=True, timeout=60)
    return "sk-live-abc123" not in p.stdout and "[REDACTED]" in p.stdout


def bug_tests_pass(failed: list) -> dict:
    """A test fails if its node id is reported, or its whole file is (a collection error)."""
    bad = {re.sub(r"\[.*\]$", "", f) for f in failed}
    return {b["id"]: not any(t in bad or t.split("::")[0] in bad for t in b["failing"]) for b in bugs()}


# --------------------------------------------------------------------------- arms


def fn_job(client, tree, tests, output, key_or_span, ledger, expect: str = "", fixer: str = "") -> dict:
    rel, name, s, e = key_or_span
    text = "\n".join((tree / rel).read_text().splitlines()[s - 1:e])
    ttext = [f"--- {t} ---\n{base.test_source(tree, t)}\n\nfailure output:\n{base.failure_of(output, t)}"
             for t in tests]
    prompt = WORKER_FN.format(tests="\n\n".join(ttext), path=rel, func=numbered(text, s))
    if sum(c["total"] for c in ledger) > TRIAL_CAP:
        return {"file": rel, "function": name, "span": [s, e], "verdict": "skipped", "why": "trial cap"}
    lead = f"What your lead says these tests need:\n{expect}\n\n" if expect else ""
    prompt = prompt.replace("Is the cause of these failures in this function?",
                            lead + "Is the cause of these failures in this function?")
    rep = base.parse(base.call(client, WORKER_MODEL, [{"role": "user", "content": prompt}], ledger, "worker"))
    rep = {k: rep.get(k) for k in ("verdict", "why")} if isinstance(rep, dict) else {}
    if rep.get("verdict") == "bug" and sum(c["total"] for c in ledger) <= TRIAL_CAP:
        prompt = WORKER_FIX.format(tests="\n\n".join(ttext), path=rel, func=numbered(text, s), why=rep.get("why"),
                                   first=s, last=e)
        prompt = prompt.replace("Replace the wrong line or lines.", lead + "Replace the wrong line or lines.")
        msgs = [{"role": "user", "content": prompt}]
        for attempt in range(2):
            reply = base.call(client, fixer or WORKER_MODEL, msgs, ledger, "fixer" if fixer else "worker")
            fix = base.parse(reply)
            fix = fix if isinstance(fix, dict) else {}
            try:
                inside = s <= int(fix.get("start")) <= int(fix.get("end")) <= e
            except (TypeError, ValueError):
                inside = False
            if inside:
                rep.update({k: fix.get(k) for k in ("start", "end", "new")})
                break
            rep["fix_rejected"] = rep.get("fix_rejected", 0) + 1
            msgs += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": f"start and end must be line numbers from the listing, between {s} "
                                                 f"and {e}. Reply with the JSON object again."}]
    return {"file": rel, "function": name, "span": [s, e], **rep}


def resolve(cov: dict, t: str) -> str:
    """The map's node id for a test the instructor named (exact, or the one id it begins)."""
    if t in cov or not cov:
        return t
    hits = [k for k in cov if k.startswith(t)]
    return hits[0] if len(hits) == 1 else t


def candidates(tree, arm, inv, cov) -> list:
    seen, out = set(), []
    if arm in MAPPED_ARMS:
        keys = []
        for t in inv.get("tests", [])[:MAX_TESTS_PER_INV]:
            keys += cov.get(re.sub(r"\[.*\]$", "", str(t)), [])
        spans = [locate(tree, k) for k in keys]
    else:   # in turn from each named file, so one big file cannot crowd out the others
        per_file = [[(f, n, s, e) for n, s, e in functions(tree, f)]
                    for f in [f for f in inv.get("files", []) if isinstance(f, str)][:MAX_FILES_C0]
                    if f.startswith(f"{PKG}/") and ".." not in f]
        spans = [fs[i] for i in range(max(map(len, per_file), default=0)) for fs in per_file if i < len(fs)]
    for sp in spans:
        if sp and (sp[0], sp[1]) not in seen:
            seen.add((sp[0], sp[1]))
            out.append(sp)
    return out[:MAX_FUNCS]


def round_c(client, tree, arm, plan, output, cov, ledger) -> tuple:
    invs = [i for i in plan.get("investigations", []) if isinstance(i, dict)][:MAX_INVESTIGATIONS]
    jobs = []
    for inv in invs:
        tests = [t for t in inv.get("tests", []) if isinstance(t, str)][:MAX_TESTS_PER_INV]
        if arm in V4_ARMS:
            tests = [resolve(cov, t) for t in tests]
            inv["tests"] = tests
        expect = str(inv.get("expectation") or "") if arm in V4_ARMS else ""
        for sp in candidates(tree, arm, inv, cov):
            jobs.append((inv.get("id"), tests, sp, expect))
    fixer = base.INSTRUCTOR if arm == "S" else ""
    with ThreadPoolExecutor(POOL) as ex:
        results = list(ex.map(lambda j: {"inv": j[0], "tests": j[1],
                                         **fn_job(client, tree, j[1], output, j[2], ledger, j[3], fixer)}, jobs))
    flagged = [r for r in results if r.get("verdict") == "bug"]
    report = []
    for inv in invs:
        mine = [r for r in results if r["inv"] == inv.get("id")]
        report.append({"investigation": inv.get("id"), "tests": inv.get("tests"),
                       "functions_checked": len(mine),
                       "unsure": [f"{r['file']}:{r['function']}" for r in mine if r.get("verdict") == "unsure"],
                       "flagged": [{"file": r["file"], "function": r["function"], "start": r.get("start"),
                                    "end": r.get("end"), "current": current(tree, r["file"], r.get("start"), r.get("end")),
                                    "new": r.get("new"), "why": r.get("why")}
                                   for r in mine if r.get("verdict") == "bug"]})
    return invs, results, flagged, report


def round_b(client, tree, plan, output, ledger) -> tuple:
    jobs = [j for j in plan.get("jobs", []) if isinstance(j, dict)][:base.MAX_JOBS]

    def one(job):
        files = [f for f in job.get("files", [])[:base.MAX_FILES] if isinstance(f, str)]
        tests = [t for t in job.get("tests", [])[:base.MAX_TESTS] if isinstance(t, str)]
        ftext = []
        for f in files:
            p = tree / f
            ok = ".." not in f and not f.startswith("/") and p.is_file()
            ftext.append(f"--- {f} ---\n" + (numbered(p.read_text()) if ok else "(no such file)"))
        ttext = [f"--- {t} ---\n{base.test_source(tree, t)}\n\nfailure output:\n{base.failure_of(output, t)}"
                 for t in tests]
        prompt = WORKER_B.format(question=job.get("question", ""), tests="\n\n".join(ttext) or "(none named)",
                                 files="\n\n".join(ftext) or "(none named)")
        rep = base.parse(base.call(client, WORKER_MODEL, [{"role": "user", "content": prompt}], ledger, "worker"))
        rep = rep if isinstance(rep, dict) else {}
        if rep.get("file") and rep.get("start"):
            rep["current"] = current(tree, str(rep["file"]), rep.get("start"), rep.get("end"))
        return {"job": job.get("id"), "files": files, "tests": tests, "report": rep}
    with ThreadPoolExecutor(POOL) as ex:
        reports = list(ex.map(one, jobs))
    return jobs, reports


def trial_lines(client, arm: str, seed: int, ledger: list) -> dict:
    tree = fresh_copy(prefix=f"atomic-{arm}-")
    plant(tree)
    cov = coverage_map() if arm in MAPPED_ARMS else {}
    agents = (tree / "AGENTS.md").read_text() if (tree / "AGENTS.md").exists() else "(none)"

    def ev():
        out = base.pytest_output(tree, specdeck=False)
        text = "Test output (pytest -q -ra --tb=short):\n" + out
        if arm in MAPPED_ARMS:
            failing = run_suite(tree, specdeck=False)["failed"]
            text += "\n\n" + coverage_evidence(cov, failing)
        if arm in V4_ARMS:   # the instructor needs the tests themselves to say what they need
            srcs = [f"--- {t} ---\n{base.test_source(tree, t)}" for t in dict.fromkeys(
                re.sub(r"\[.*\]$", "", f) for f in failing)]
            text += "\n\nThe failing tests' source:\n" + "\n\n".join(srcs)[:24000]
        return out, text

    output, text = ev()
    if arm == "B":
        first = base.INSTRUCT.format(mf=base.MAX_FILES, mt=base.MAX_TESTS, mj=base.MAX_JOBS, files=base.file_list(tree),
                                     agents=agents, evidence=text)
        first = first.replace("proposed fix as an exact text replacement",
                              "proposed fix as a replacement for a range of numbered lines")
    else:
        where = (WHERE_C1 if arm in MAPPED_ARMS else WHERE_C0).format(mf=MAX_FUNCS, nf=MAX_FILES_C0)
        if arm in V4_ARMS:
            where += EXPECT_NOTE
            field = ', "expectation": "..."'
        else:
            field = "" if arm == "C1" else f', "files": ["{PKG}/..."]'
        first = INSTRUCT_C.format(mt=MAX_TESTS_PER_INV, where=where, mi=MAX_INVESTIGATIONS,
                                  files=base.file_list(tree), agents=agents, evidence=text, files_field=field)
    messages = [{"role": "user", "content": first}]
    rounds, log = [], []
    for rnd in range(base.ROUNDS):
        if sum(c["total"] for c in ledger) > TRIAL_CAP:
            rounds.append({"round": rnd + 1, "stopped": "trial cap"})
            break
        reply = base.call(client, base.INSTRUCTOR, messages, ledger, "instructor")
        messages.append({"role": "assistant", "content": reply})
        plan = base.parse(reply)
        if arm == "B":
            jobs, reports = round_b(client, tree, plan, output, ledger)
            if not jobs:
                break
            payload = [{"job": r["job"], **r["report"]} for r in reports]
            log += [{"round": rnd + 1, **r} for r in reports]
            n_jobs, n_calls, flagged = len(jobs), len(jobs), sum(bool(r["report"].get("start")) for r in reports)
        else:
            invs, results, flagged_rows, payload = round_c(client, tree, arm, plan, output, cov, ledger)
            if not invs:
                break
            log += [{"round": rnd + 1, **r} for r in results]
            n_jobs, n_calls, flagged = len(invs), len(results), len(flagged_rows)
        messages.append({"role": "user", "content": AGGREGATE_LINES.format(reports=json.dumps(payload, indent=1), pkg=PKG)})
        reply = base.call(client, base.INSTRUCTOR, messages, ledger, "instructor")
        messages.append({"role": "assistant", "content": reply})
        applied = splice(tree, [p for p in base.parse(reply).get("patches", []) if isinstance(p, dict)])
        suite = run_suite(tree, specdeck=False)
        rounds.append({"round": rnd + 1, "jobs": n_jobs, "functions_checked": n_calls, "flagged": flagged,
                       "patches": applied, "failed_after": len(suite["failed"]), "tail": suite["tail"]})
        if not suite["failed"] and suite["rc"] == 0:
            break
        if rnd + 1 < base.ROUNDS:
            output, text = ev()
            retry = RETRY_C if arm != "B" else base.RETRY
            messages.append({"role": "user", "content": retry.format(applied=", ".join(applied) or "none",
                                                                     evidence=text)})
    final = run_suite(tree, specdeck=False)
    return {"tree": str(tree), "final": final, "rounds": rounds, "job_log": log}


def grade(arm, seed, tree, final, rounds, ledger, job_log) -> dict:
    tree = Path(tree)
    tok = lambda role: sum(c["total"] for c in ledger if c["role"] == role)  # noqa: E731
    return {"arm": arm, "c_version": 4 if arm in V4_ARMS else (C_VERSION if arm.startswith("C") else None),
            "fixer_tokens": tok("fixer"), "worker": WORKER_MODEL, "seed": seed, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "green": not final["failed"] and final["rc"] == 0, "failed_after": final["failed"],
            "bug_tests_pass": bug_tests_pass(final["failed"]), "restored": restored(tree),
            "redaction_holds": redaction_holds(tree), "rounds": rounds,
            "worker_calls": sum(1 for c in ledger if c["role"] == "worker"),
            "instructor_tokens": tok("instructor"), "worker_tokens": tok("worker"),
            "tokens": sum(c["total"] for c in ledger), "ledger": ledger, "job_log": job_log, "tree": str(tree)}


def one_trial(client, arm: str, seed: int) -> dict:
    ledger: list = []
    try:
        if arm == "A":
            t = base.trial(client, "plain", WORKER_MODEL, seed, ledger)
            return grade("A", seed, t["tree"], run_suite(Path(t["tree"]), specdeck=False), t["rounds"], ledger,
                         t["job_log"])
        r = trial_lines(client, arm, seed, ledger)
        return grade(arm, seed, r["tree"], r["final"], r["rounds"], ledger, r["job_log"])
    except Exception as exc:  # keep the spend of a trial that crashed
        return {"arm": arm, "seed": seed, "crashed": repr(exc), "tokens": sum(c["total"] for c in ledger),
                "ledger": ledger}


def spent() -> int:
    return sum(json.loads(l)["tokens"] for l in LOG.read_text().splitlines() if l.strip()) if LOG.exists() else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="A,B,C0,C1")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--first-seed", type=int, default=0)
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--reserve", type=int, default=270_000, help="room a trial needs before it starts")
    a = ap.parse_args()
    client = OpenAI()
    arms = a.arms.split(",")
    for seed in range(a.first_seed, a.first_seed + a.seeds):
        order = arms if seed % 2 == 0 else arms[::-1]       # alternate run order
        for arm in order:
            if a.cap - spent() < a.reserve:
                print(f"stop: {spent():,} of {a.cap:,} spent", flush=True)
                return
            t = one_trial(client, arm, seed)
            with LOG.open("a") as fh:
                fh.write(json.dumps(t) + "\n")
            if "crashed" in t:
                print(f"{arm:3s} seed{seed} CRASHED {t['crashed']} | spent {spent():,}", flush=True)
                continue
            print(f"{arm:3s} seed{seed} green={t['green']} tests_pass={t['bug_tests_pass']} "
                  f"restored={t['restored']} redaction={t['redaction_holds']} calls={t['worker_calls']} "
                  f"instr={t['instructor_tokens']:,} work={t['worker_tokens']:,} | spent {spent():,}", flush=True)


if __name__ == "__main__":
    main()
