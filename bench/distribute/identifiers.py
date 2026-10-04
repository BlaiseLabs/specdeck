"""Which tests make a bug hard for a weak model? Toward a proofdeck "distributable" flag.

For a sample of quiet single-line bugs (quiet.json / quiet_<subject>.json), route perfectly:
the worker gets the buggy function and the narrowest failing test that runs it (fewest
functions in the coverage map), exactly as a C1 investigation would in the best case. It
answers bug/no/unsure and, if bug, writes a line-range fix, which is spliced in and checked
by running the bug's failing tests. Each case records properties of the test/function pair
that a tool could compute without any model:

  breadth      functions the test runs (coverage map)
  depth        calls between the test function and the buggy function (traced, pristine code)
  test_lines   length of the test function
  fn_lines     length of the buggy function
  operator     the mutation (statement deletion, negation, constant, ...)

    DIST_SUBJECT=tapedeck DIST_BUGS=quiet python3 identifiers.py --n 40 --cap 110000
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

import atomic
import run as base
from bugs import REPO, SUBJECT, fresh_copy

HERE = Path(__file__).resolve().parent
LOG = HERE / f"identifiers_{SUBJECT}.jsonl"
QUIET = HERE / "data" / ("quiet.json" if SUBJECT == "tapedeck" else f"quiet_{SUBJECT}.json")

TRACE = r'''
import sys
tree, nodeid, rel, qual = sys.argv[1:5]
sys.path.insert(0, tree)
test_name = nodeid.split("::")[-1].split("[")[0]
best = [None]
def prof(frame, event, arg):
    if event != "call":
        return
    code = frame.f_code
    if code.co_filename.endswith(rel) and getattr(code, "co_qualname", code.co_name) == qual:
        d, f = 0, frame
        while f is not None and f.f_code.co_name != test_name:
            f, d = f.f_back, d + 1
        if f is not None and (best[0] is None or d < best[0]):
            best[0] = d
import pytest
sys.setprofile(prof)
pytest.main([nodeid, "-q", "-p", "no:cacheprovider", "-p", "no:specdeck", "-p", "no:randomly"])
sys.setprofile(None)
print("DEPTH=" + str(best[0]))
'''


def env_for(tree):
    return {**os.environ, "PYTHONPATH": str(tree)}


def depth(tree: Path, nodeid: str, rel: str, qual: str):
    p = subprocess.run([sys.executable, "-c", TRACE, str(tree), nodeid, rel, qual], cwd=tree, env=env_for(tree),
                       capture_output=True, text=True, timeout=300)
    m = re.search(r"DEPTH=(\S+)", p.stdout)
    return None if not m or m.group(1) == "None" else int(m.group(1))


def run_tests(tree: Path, tests: list) -> tuple:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-ra", "--tb=short", "-p", "no:cacheprovider",
                        "-p", "no:specdeck", *tests], cwd=tree, env=env_for(tree), capture_output=True,
                       text=True, timeout=600)
    return p.returncode, p.stdout


def cases(n: int, seed: int) -> list:
    cov = atomic.coverage_map()
    muts = [m for m in json.loads(QUIET.read_text()) if m["quiet"]]
    out = []
    for m in muts:
        owner = next(((name, s, e) for name, s, e in atomic.functions(REPO, m["path"]) if s <= m["line"] <= e), None)
        if not owner:
            continue
        failing = [atomic.resolve(cov, t) for t in m["failing"]]
        # failing tests that the map says run the buggy function (or something nested in it)
        runs = [t for t in failing if any((loc := atomic.locate(REPO, k)) and loc[0] == m["path"] and loc[1] == owner[0]
                                          for k in cov.get(t, []))]
        if not runs:
            continue
        test = min(runs, key=lambda t: len(cov.get(t, [])))
        out.append({**m, "failing": failing, "function": owner[0], "span": [owner[1], owner[2]], "test": test,
                    "breadth": len(cov.get(test, [])), "fn_lines": owner[2] - owner[1] + 1})
    random.Random(seed).shuffle(out)
    return out[:n]


def one(client, case: dict, ledger: list) -> dict:
    tree = fresh_copy(prefix="ident-")
    rel, qual = case["path"], case["function"]
    case["depth"] = depth(tree, case["test"], rel, qual)                 # pristine call path
    case["test_lines"] = len(base.test_source(tree, case["test"]).splitlines())
    p = tree / rel
    pristine = p.read_text()
    lines = pristine.splitlines(keepends=True)
    assert lines[case["line"] - 1].rstrip("\n") == case["original"]
    lines[case["line"] - 1] = case["mutated"] + "\n"
    p.write_text("".join(lines))
    _, out = run_tests(tree, [case["test"]])
    s, e = case["span"]
    r = atomic.fn_job(client, tree, [case["test"]], out, (rel, qual, s, e), ledger)
    case.update({"verdict": r.get("verdict"), "why": r.get("why"), "fix": {k: r.get(k) for k in ("start", "end", "new")}})
    case["fixed"] = case["restored"] = False
    if r.get("verdict") == "bug" and r.get("start"):
        applied = atomic.splice(tree, [{"file": rel, "start": r["start"], "end": r["end"], "new": r.get("new", "")}])
        case["applied"] = applied[0]
        if applied[0] == "applied":
            rc, _ = run_tests(tree, case["failing"][:6])
            case["fixed"] = rc == 0
            now = next(("\n".join(p.read_text().splitlines()[a - 1:b]) for n, a, b in atomic.functions(tree, rel)
                        if n == qual), None)
            case["restored"] = now == "\n".join(pristine.splitlines()[s - 1:e])
    return case


def spent() -> int:
    return sum(json.loads(l)["tokens"] for l in LOG.read_text().splitlines() if l.strip()) if LOG.exists() else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--cap", type=int, required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    client = OpenAI()
    todo = cases(a.n, a.seed)
    done = set()
    if LOG.exists():
        done = {(json.loads(l)["path"], json.loads(l)["line"], json.loads(l)["mutated"]) for l in LOG.read_text().splitlines()}
    todo = [c for c in todo if (c["path"], c["line"], c["mutated"]) not in done]
    print(f"{len(todo)} cases to run, spent {spent():,} of {a.cap:,}", flush=True)

    def go(case):
        if spent() > a.cap - 6000:
            return None
        ledger: list = []
        try:
            res = one(client, case, ledger)
        except Exception as exc:  # noqa: BLE001
            res = {**case, "error": repr(exc)[:300]}
        res["tokens"] = sum(c["total"] for c in ledger)
        with LOG.open("a") as fh:
            fh.write(json.dumps(res) + "\n")
        print(f"{res['path']}:{res['line']} {res['operator']:10} breadth={res.get('breadth')} depth={res.get('depth')} "
              f"verdict={res.get('verdict')} fixed={res.get('fixed')} restored={res.get('restored')} "
              f"| spent {spent():,}", flush=True)
        return res
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(go, todo))


if __name__ == "__main__":
    main()
