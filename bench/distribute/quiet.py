"""Find quiet bugs: mutants whose every failing test fails at an `assert` in the test.

The first two bug sets clustered trivially because each bug left the same exception
(`NameError: raw`, `AttributeError: _module_path`) in every test it broke. A quiet bug
leaves no shared error text: each test just reports a different wrong value, so the
instructor has to work out which failures share a cause.

    python3 quiet.py            # writes data/quiet.json (no API calls)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, "/workspaces/python/proofdeck")
from proofdeck.mutate import apply, mutants_for_line  # noqa: E402

from bugs import REPO, fresh_copy  # noqa: E402

HERE = Path(__file__).resolve().parent
_TB = re.compile(r"^(/\S+?):(\d+): (.*)$")
_FAILED = re.compile(r"^(FAILED|ERROR) (\S+)")


def run(tree: Path) -> dict:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=line", "-rfE", "-p", "no:cacheprovider",
                        "-p", "no:specdeck"], cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)},
                       capture_output=True, text=True, timeout=120)
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


def check(job) -> dict | None:
    tree, rel, m = job
    p = Path(tree) / rel
    orig = p.read_text()
    p.write_text(apply(orig, m))
    try:
        r = run(Path(tree))
    except subprocess.TimeoutExpired:
        r = None
    finally:
        p.write_text(orig)
    if not r or not r["failed"]:
        return None
    quiet = (not r["errors"] and len(r["tb"]) == len(r["failed"])
             and all(f.startswith("tests/") and msg.startswith(("assert ", "AssertionError")) for f, msg in r["tb"]))
    return {"path": rel, "line": m.line, "operator": m.operator, "original": m.original, "mutated": m.mutated,
            "failing": r["failed"], "errors": r["errors"], "quiet": quiet,
            "messages": sorted({msg[:80] for _, msg in r["tb"]})}


def work(args):
    """One worker: its own tapedeck copy, a slice of the mutants."""
    jobs = args
    tree = fresh_copy(prefix="quiet-")
    out = []
    for rel, m in jobs:
        res = check((str(tree), rel, m))
        if res:
            out.append(res)
    return out


def main() -> None:
    jobs = []
    for p in sorted((REPO / "tapedeck").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = str(p.relative_to(REPO))
        text = p.read_text()
        for n in range(1, len(text.splitlines()) + 1):
            for m in mutants_for_line(text, n, rel):
                jobs.append((rel, m))
    print(f"{len(jobs)} mutants", flush=True)
    k = 4
    with ProcessPoolExecutor(k) as ex:
        results = [r for part in ex.map(work, [jobs[i::k] for i in range(k)]) for r in part]
    (HERE / "data").mkdir(exist_ok=True)
    (HERE / "data" / "quiet.json").write_text(json.dumps(results, indent=1))
    q = [r for r in results if r["quiet"]]
    print(f"{len(results)} killed, {len(q)} quiet", flush=True)
    for r in sorted(q, key=lambda r: -len(r["failing"])):
        print(f"  {r['path']}:{r['line']} {r['operator']} {len(r['failing'])} tests "
              f"in {len({f.split('::')[0] for f in r['failing']})} files | {r['mutated'].strip()[:60]}")


if __name__ == "__main__":
    main()
