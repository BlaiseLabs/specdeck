"""Plant the three bugs in a fresh tapedeck copy and record what the suite says.

Three single-line mutants from agent_ab's ground truth, chosen so their failing tests
don't overlap and each sits in a different file. With specdeck on, each one's later
scenarios are skipped as blocked, so the spec arm sees 6 failures + 7 blocked where
the plain arm sees 12 failures.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent_ab"))
from groundtruth import run_suite  # noqa: E402

SUBJECT = os.environ.get("DIST_SUBJECT", "tapedeck")   # proofdeck: the replication subject
REPO = Path(f"/workspaces/python/{SUBJECT}")
PKG = SUBJECT                                            # the package directory inside the repo
DATA = Path(__file__).resolve().parent.parent / "agent_ab" / "data"
# easy: one bug per test file. hub: one bug breaking 17 tests across 7 files, plus two
# small bugs whose failing tests sit in files the hub also breaks.
SETS = {"easy": ("m007", "m013", "m016"), "hub": ("m003", "m000", "m026")}
BUG_SET = os.environ.get("DIST_BUGS", "easy")
# DIST_NARROW=1: copies also get narrow/<subject>_test_units.py, direct unit tests of the
# planted bugs' functions (rigged on purpose), and each bug fails those too.
NARROW = os.environ.get("DIST_NARROW") == "1"
NARROW_TESTS = Path(__file__).resolve().parent / "narrow" / f"{SUBJECT}_test_units.py"
NARROW_FAILING = Path(__file__).resolve().parent / "data" / f"narrow_failing_{SUBJECT}.json"
BUG_IDS = SETS.get(BUG_SET, ())
QUIET = Path(__file__).resolve().parent / "data" / (
    "quiet_set.json" if SUBJECT == "tapedeck" else f"quiet_set_{SUBJECT}.json")


def bugs() -> list:
    """Mutants with `direct` and `blocked` test lists. Mutants outside triage.json were
    never run with specdeck on; all their failing tests count as direct."""
    by_id = {m["id"]: {**m, "direct": m["failing"], "blocked": []} for m in json.loads((DATA / "impact.json").read_text())}
    by_id.update({t["id"]: t for t in json.loads((DATA / "triage.json").read_text())})
    if BUG_SET == "quiet":
        out = json.loads(QUIET.read_text())
        if NARROW:
            extra = json.loads(NARROW_FAILING.read_text())
            for b in out:
                b["failing"] = b["direct"] = b["failing"] + extra.get(b["id"], [])
        return out
    return [by_id[i] for i in BUG_IDS]


def fresh_copy(prefix: str = "dist-") -> Path:
    dest = Path(tempfile.mkdtemp(prefix=prefix)) / REPO.name
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns(
        "__pycache__", ".git", ".specdeck", "*.egg-info", "bench", ".pytest_cache"))
    if NARROW:
        shutil.copy(NARROW_TESTS, dest / "tests" / "test_units_narrow.py")
    return dest


def plant(tree: Path) -> None:
    for b in bugs():
        p = tree / b["path"]
        lines = p.read_text().splitlines(keepends=True)
        assert lines[b["line"] - 1].rstrip("\n") == b["original"], (b["id"], lines[b["line"] - 1])
        indent = b["original"][: len(b["original"]) - len(b["original"].lstrip())]
        new = b["mutated"] if b["mutated"].startswith(indent) else indent + b["mutated"].lstrip()
        lines[b["line"] - 1] = new + "\n"
        p.write_text("".join(lines))


def fixed(tree: Path) -> dict:
    """Which bugs are gone: the planted line no longer reads as the mutant does."""
    out = {}
    for b in bugs():
        line = (tree / b["path"]).read_text().splitlines()[b["line"] - 1:b["line"] + 3]
        out[b["id"]] = not any(l.strip() == b["mutated"].strip() for l in line)
    return out


if __name__ == "__main__":
    t = fresh_copy()
    plant(t)
    plain = run_suite(t, specdeck=False)
    spec = run_suite(t, specdeck=True)
    want = sorted({x for b in bugs() for x in b["direct"] + b["blocked"]})
    print("plain failed", len(plain["failed"]), "== union", sorted(plain["failed"]) == want, plain["tail"])
    print("spec  failed", len(spec["failed"]), spec["tail"])
    print("spec failed == direct union", sorted(spec["failed"]) == sorted(x for b in bugs() for x in b["direct"]))
    print(t)
