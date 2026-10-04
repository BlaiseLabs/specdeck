"""quiet.py for a suite too slow to run whole per mutant (proofdeck: 80 s).

A mutant can only fail tests that execute its line, so each mutant runs only the tests
the coverage map of a green run says execute its function. Import-time lines are not in
the map and are skipped. The chosen set is re-checked with full runs before use.

    DIST_SUBJECT=proofdeck python3 quiet_cov.py     # writes data/quiet_<subject>.json
"""
from __future__ import annotations

import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, "/workspaces/python/proofdeck")
from proofdeck.mutate import apply, mutants_for_line  # noqa: E402

import atomic  # noqa: E402
from bugs import PKG, REPO, SUBJECT, fresh_copy  # noqa: E402
from quiet import run  # noqa: E402

HERE = Path(__file__).resolve().parent
SAMPLE, WORKERS, SKIP = 450, 3, ("_forkrun.py", "_pytrace.py", "__main__.py")


def run_some(tree: Path, tests: list) -> dict:
    import os
    import subprocess
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=line", "-rfE", "-p", "no:cacheprovider",
                        "-p", "no:specdeck", *tests], cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)},
                       capture_output=True, text=True, timeout=180)
    from quiet import _FAILED, _TB
    failed, errors, tbs = [], [], []
    for line in p.stdout.splitlines():
        m = _FAILED.match(line)
        if m:
            (failed if m.group(1) == "FAILED" else errors).append(m.group(2).split(" - ")[0])
            continue
        m = _TB.match(line)
        if m and str(tree) in m.group(1):
            tbs.append((m.group(1).replace(str(tree) + "/", ""), m.group(3)))
    return {"failed": sorted(set(failed)), "errors": sorted(set(errors)), "tb": tbs}


def work(jobs):
    tree = fresh_copy(prefix="quietcov-")
    out = []
    for rel, m, tests in jobs:
        p = tree / rel
        orig = p.read_text()
        p.write_text(apply(orig, m))
        try:
            r = run_some(tree, tests)
        except Exception:  # noqa: BLE001  timeouts and crashes are not quiet
            r = None
        finally:
            p.write_text(orig)
        if not r or not r["failed"]:
            continue
        quiet = (not r["errors"] and len(r["tb"]) == len(r["failed"])
                 and all(f.startswith("tests/") and msg.startswith(("assert ", "AssertionError")) for f, msg in r["tb"]))
        out.append({"path": rel, "line": m.line, "operator": m.operator, "original": m.original,
                    "mutated": m.mutated, "failing": r["failed"], "quiet": quiet, "covering": len(tests),
                    "messages": sorted({msg[:80] for _, msg in r["tb"]})})
    return out


def main() -> None:
    cov = atomic.coverage_map()
    tests_of: dict = {}
    for node, fns in cov.items():
        for f in fns:
            tests_of.setdefault(f, set()).add(node)
    jobs = []
    for p in sorted((REPO / PKG).rglob("*.py")):
        if "__pycache__" in p.parts or p.name in SKIP:
            continue
        rel = str(p.relative_to(REPO))
        text = p.read_text()
        keys = [k for k in tests_of if k.startswith(rel + ":")]
        spans = [(atomic.locate(REPO, k), k) for k in keys]
        for n in range(1, len(text.splitlines()) + 1):
            tests = set()
            for sp, k in spans:
                if sp and sp[2] <= n <= sp[3]:
                    tests |= tests_of[k]
            if not tests:
                continue
            for m in mutants_for_line(text, n, rel):
                jobs.append((rel, m, sorted(tests)))
    random.Random(7).shuffle(jobs)
    print(f"{len(jobs)} mutants on covered lines; sampling {min(SAMPLE, len(jobs))}", flush=True)
    jobs = jobs[:SAMPLE]
    with ProcessPoolExecutor(WORKERS) as ex:
        results = [r for part in ex.map(work, [jobs[i::WORKERS] for i in range(WORKERS)]) for r in part]
    out = HERE / "data" / f"quiet_{SUBJECT}.json"
    out.write_text(json.dumps(results, indent=1))
    q = [r for r in results if r["quiet"]]
    print(f"{len(results)} killed, {len(q)} quiet", flush=True)
    for r in sorted(q, key=lambda r: -len(r["failing"]))[:40]:
        print(f"  {r['path']}:{r['line']} {r['operator']} {len(r['failing'])} tests in "
              f"{len({f.split('::')[0] for f in r['failing']})} files | {r['mutated'].strip()[:70]}", flush=True)


if __name__ == "__main__":
    main()
