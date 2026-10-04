"""Build the benchmark's questions from tapedeck, with mechanical ground truth.

Two question kinds, both things an agent gets asked about a suite it did not write:

T1 impact    "This line changed like so. Which tests fail?"  Truth: apply the mutant,
             run the whole suite with specdeck's prerequisite-skipping off, record failures.
T2 triage    "These tests did not pass. Which failed on their own, which were only
             blocked by a failing prerequisite, and what do you fix first?"  Truth: the
             same mutant run with specdeck on; FAILED vs SKIPPED(blocked) from pytest -ra.

Mutants come from proofdeck's operators, so the changes are the ones a reviewer would
actually see. Nothing here consults the spec: the spec arm cannot be favoured by
construction.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "/workspaces/python/proofdeck")
from proofdeck.mutate import apply, mutants_for_line  # noqa: E402

SEED = 7
WANT_KILLED, WANT_SURVIVED, WANT_TRIAGE = 24, 8, 10

# Subject under study. tapedeck is a plain library; specdeck is the second subject, three
# times the suite and a bigger spec-versus-tests gap, which is where the "the tests fit in
# context anyway" objection to round 1 gets tested.
SUBJECTS = {
    "tapedeck": {"repo": "/workspaces/python/tapedeck", "package": "tapedeck", "triage": True},
    "specdeck": {"repo": "/workspaces/python/specdeck", "package": "specdeck", "triage": False},
}
SUBJECT = os.environ.get("AB_SUBJECT", "tapedeck")
REPO = Path(SUBJECTS[SUBJECT]["repo"])
OUT = Path(__file__).parent / "data" / ("" if SUBJECT == "tapedeck" else SUBJECT)
SOURCES = sorted(p for p in (REPO / SUBJECTS[SUBJECT]["package"]).rglob("*.py") if "__pycache__" not in p.parts)

_FAIL = re.compile(r"^(FAILED|ERROR) (\S+)")


def _base(nodeid: str) -> str:
    """`tests/test_x.py::test_y[case]` -> `tests/test_x.py::test_y`."""
    return re.sub(r"\[.*\]$", "", nodeid)
_SKIP = re.compile(r"^SKIPPED \[\d+\] (\S+?):\d+: (.*)$")


def collected(tree: Path) -> set:
    """Node ids the suite really has. A suite that runs pytest inside pytest (pytester)
    prints the inner run's FAILED lines into the outer output; without this filter those
    phantom node ids land in the ground truth and no reader of the repo could ever name
    them. 28% of a first pass on specdeck was phantom."""
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider",
                        "-p", "no:specdeck"], cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)},
                       capture_output=True, text=True, timeout=300)
    return {line.strip() for line in p.stdout.splitlines() if "::" in line and line.strip().startswith("test")}


def run_suite(tree: Path, *, specdeck: bool, only: set = frozenset()) -> dict:
    """Failures (and, with specdeck, blocked skips) of a full run on `tree`."""
    args = [sys.executable, "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider"]
    args += [] if specdeck else ["-p", "no:specdeck"]  # --no-specdeck only exists while the plugin is loaded
    env = {**os.environ, "PYTHONPATH": str(tree)}  # the copy under test, not the installed package
    p = subprocess.run(args, cwd=tree, env=env, capture_output=True, text=True, timeout=600)
    failed, blocked = [], {}
    for line in p.stdout.splitlines():
        m = _FAIL.match(line)
        if m:
            nodeid = m.group(2).split(" - ")[0]
            if not only or _base(nodeid) in only:
                failed.append(nodeid)
        m = _SKIP.match(line)
        if m and "prerequisite scenario" in m.group(2):
            # "SKIPPED [1] tests/test_x.py:12: specdeck: prerequisite scenario 'id' blocked"
            blocked.setdefault(m.group(1), m.group(2))
    return {"failed": sorted(set(failed)), "blocked_files": blocked, "rc": p.returncode,
            "tail": p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""}


def blocked_nodeids(tree: Path) -> list:
    """`-ra` reports a skip by file:line, not node id; ask specdeck's spec for the ids."""
    out = tree / ".specdeck" / "spec.yaml"
    import yaml
    spec = yaml.safe_load(out.read_text()) if out.exists() else {"scenarios": []}
    ids = []
    for sc in spec["scenarios"]:
        if any(t == "status:skipped" for t in sc.get("tags", [])):
            ids.append(sc["logic"][0][len("pytest "):])
    return ids


def fresh_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="agentab-"))
    dest = tmp / REPO.name
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns(
        "__pycache__", ".git", ".specdeck", "*.egg-info", "bench", ".pytest_cache"))
    return dest


def snippet(text: str, line: int, before: int = 6, after: int = 6) -> str:
    lines = text.splitlines()
    lo, hi = max(1, line - before), min(len(lines), line + after)
    return "\n".join(f"{n:4d}  {lines[n - 1]}" for n in range(lo, hi + 1))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"subject: {SUBJECT} ({REPO})", flush=True)
    rng = random.Random(SEED)
    candidates = []
    for p in SOURCES:
        text = p.read_text()
        rel = str(p.relative_to(REPO))
        for n in range(1, text.count("\n") + 1):
            ms = mutants_for_line(text, n, rel)
            if ms:
                candidates.append((rel, n, ms[0]))
    rng.shuffle(candidates)
    print(f"{len(candidates)} mutable lines in {len(SOURCES)} files", flush=True)

    killed, survived, triage = [], [], []
    tree = fresh_copy()
    real = {_base(n) for n in collected(tree)}
    print(f"{len(real)} tests collected; failures outside this set are from nested runs and ignored", flush=True)
    base = run_suite(tree, specdeck=False, only=real)
    assert not base["failed"], f"baseline must be green: {base}"
    print("baseline green:", base["tail"], flush=True)
    t0 = time.time()
    for i, (rel, n, m) in enumerate(candidates):
        want_triage = WANT_TRIAGE if SUBJECTS[SUBJECT]["triage"] else 0
        if len(killed) >= WANT_KILLED and len(survived) >= WANT_SURVIVED and len(triage) >= want_triage:
            break
        src = (REPO / rel).read_text()
        (tree / rel).write_text(apply(src, m))
        try:
            r1 = run_suite(tree, specdeck=False, only=real)
            case = {"id": f"m{i:03d}", "path": rel, "line": n, "original": m.original, "mutated": m.mutated,
                    "operator": m.operator, "snippet": snippet(src, n), "failing": r1["failed"]}
            if r1["failed"]:
                if len(killed) < WANT_KILLED:
                    killed.append(case)
                    if len(triage) < WANT_TRIAGE and SUBJECTS[SUBJECT]["triage"]:
                        r2 = run_suite(tree, specdeck=True, only=real)
                        blocked = sorted(set(blocked_nodeids(tree)) - set(r2["failed"]))
                        if blocked:  # only cascades make a triage question
                            triage.append({**case, "direct": r2["failed"], "blocked": blocked})
            elif len(survived) < WANT_SURVIVED:
                survived.append(case)
        finally:
            (tree / rel).write_text(src)
            sd = tree / ".specdeck"
            if sd.exists():
                shutil.rmtree(sd)
        print(f"  {i + 1}: {rel}:{n} {m.operator} -> {len(r1['failed'])} fail | killed {len(killed)} "
              f"survived {len(survived)} triage {len(triage)} | {time.time() - t0:.0f}s", flush=True)
    shutil.rmtree(tree.parent, ignore_errors=True)
    impact = killed + survived
    rng.shuffle(impact)
    (OUT / "impact.json").write_text(json.dumps(impact, indent=1))
    (OUT / "triage.json").write_text(json.dumps(triage, indent=1))
    print(f"wrote {len(impact)} impact questions ({len(killed)} killed, {len(survived)} survived) "
          f"and {len(triage)} triage questions", flush=True)


if __name__ == "__main__":
    main()
