"""Score every logged answer against the ground truth and write RESULTS.md."""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "/workspaces/python/specdeck")
from specdeck.history import analyze  # noqa: E402

from budget import Ledger  # noqa: E402

HERE = Path(__file__).parent
SUBJECT = os.environ.get("AB_SUBJECT", "tapedeck")
DATA = HERE / "data" / ("" if SUBJECT == "tapedeck" else SUBJECT)
REPO = Path({"tapedeck": "/workspaces/python/tapedeck", "specdeck": "/workspaces/python/specdeck"}[SUBJECT])
MODEL = os.environ.get("AB_MODEL", "gpt-5.6-terra")
_m = "" if MODEL == "gpt-5.6-terra" else "-" + MODEL.replace(".", "").replace("-", "")
CALLS = HERE / (("calls" if SUBJECT == "tapedeck" else f"calls-{SUBJECT}") + _m + ".jsonl")
RESULTS = HERE / (("RESULTS" if SUBJECT == "tapedeck" else f"RESULTS-{SUBJECT}") + _m + ".md")


def norm(nodeid: str) -> str:
    """`tests/test_x.py::test_y[case]` -> `test_x.py::test_y`: file and function, no params."""
    s = re.sub(r"\[.*\]$", "", str(nodeid).strip().strip("`"))
    s = s.replace("\\", "/")
    if "::" in s:
        file, _, fn = s.partition("::")
        return f"{Path(file).name}::{fn.split('::')[-1]}"
    return s


def prf(pred: list, truth: list) -> dict:
    p, t = {norm(x) for x in pred}, {norm(x) for x in truth}
    if not p and not t:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "exact": 1.0}
    tp = len(p & t)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(t) if t else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "exact": float(p == t)}


def fix_first_truth(case: dict, spec: dict) -> set:
    """Direct failures holding up the most others (ties all count), from the DAG."""
    by_node = {sc["logic"][0][len("pytest "):]: sc for sc in spec["scenarios"]}
    bad, blocked = {norm(n) for n in case["direct"]}, {norm(n) for n in case["blocked"]}
    rows = []
    for sc in spec["scenarios"]:
        n = norm(sc["logic"][0][len("pytest "):])
        status = "failed" if n in bad else "blocked" if n in blocked else "passed"
        rows.append({"id": sc["id"], "status": status, "depends_on": list(sc.get("depends_on") or [])})
    analyze(rows)
    by = {r["id"]: r for r in rows}
    direct = [(by[sc["id"]]["holding_up"], norm(nid)) for nid, sc in by_node.items() if norm(nid) in bad]
    top = max((h for h, _ in direct), default=0)
    return {n for h, n in direct if h == top}


def main() -> None:
    import yaml
    spec = yaml.safe_load((REPO / ".specdeck" / "spec.yaml").read_text())
    truth = {q["id"]: q for f in ("impact", "triage") if (DATA / f"{f}.json").exists()
             for q in json.loads((DATA / f"{f}.json").read_text())}
    ledger = Ledger(CALLS, 0)
    rows: dict = {}
    for c in ledger.calls:
        answers = {a.get("q"): a for a in c["answer"].get("answers", [])} if isinstance(c["answer"], dict) else {}
        for i, qid in enumerate(c["question_ids"], 1):
            q, a = truth[qid], answers.get(i) or {}
            key = (c["task"], c["arm"])
            if c["task"] == "impact":
                r = prf(a.get("failing_tests") or [], q["failing"])
                r["kind"] = "killed" if q["failing"] else "survived"
            else:
                f, b = prf(a.get("failed") or [], q["direct"]), prf(a.get("blocked") or [], q["blocked"])
                r = {"f1": (f["f1"] + b["f1"]) / 2, "failed_f1": f["f1"], "blocked_f1": b["f1"],
                     "exact": float(f["exact"] and b["exact"]),
                     "fix_first": float(norm(a.get("fix_first") or "") in fix_first_truth(q, spec))}
            r["answered"] = float(bool(a))
            rows.setdefault(key, []).append(r)

    def mean(key, field):
        vals = [r[field] for r in rows.get(key, []) if field in r]
        return statistics.fmean(vals) if vals else float("nan")

    out = [f"# Agent A/B on {SUBJECT}: spec vs test files", "",
           f"Model `{ledger.calls[0]['model'] if ledger.calls else '-'}`"
           + (f", reasoning_effort={ledger.calls[0].get('reasoning')}" if ledger.calls and ledger.calls[0].get("reasoning") else "")
           + f". Subject: {SUBJECT} ({REPO}). Every answer is scored against ground truth produced by "
           "actually applying the change and running the suite (see groundtruth.py).", ""]
    out += ["## Impact — which tests fail if this line changes like so?", "",
            "| arm | n | mean F1 | exact | precision | recall | F1 on killed | F1 on survived |", "|---|---|---|---|---|---|---|---|"]
    for arm in ("spec", "specmap", "specrule", "shipped", "bound", "hub", "order", "longlast", "brief", "wiring", "tests", "testsrule", "both"):
        k = ("impact", arm)
        if k not in rows:
            continue
        killed = [r["f1"] for r in rows[k] if r["kind"] == "killed"]
        surv = [r["f1"] for r in rows[k] if r["kind"] == "survived"]
        out.append(f"| {arm} | {len(rows[k])} | {mean(k, 'f1'):.2f} | {mean(k, 'exact'):.0%} | {mean(k, 'precision'):.2f} | "
                   f"{mean(k, 'recall'):.2f} | {statistics.fmean(killed) if killed else float('nan'):.2f} | "
                   f"{statistics.fmean(surv) if surv else float('nan'):.2f} |")
    out += ["", "## Triage — which failures are root causes, which are blocked, fix what first?", "",
            "| arm | n | mean F1 | failed F1 | blocked F1 | exact | fix-first right |", "|---|---|---|---|---|---|---|"]
    for arm in ("spec", "specmap", "specrule", "shipped", "bound", "hub", "order", "longlast", "brief", "wiring", "tests", "testsrule", "both"):
        k = ("triage", arm)
        if k not in rows:
            continue
        out.append(f"| {arm} | {len(rows[k])} | {mean(k, 'f1'):.2f} | {mean(k, 'failed_f1'):.2f} | {mean(k, 'blocked_f1'):.2f} | "
                   f"{mean(k, 'exact'):.0%} | {mean(k, 'fix_first'):.0%} |")
    if SUBJECT == "tapedeck":
      out += ["", "## What the material answers without a model", "",
            "Both tasks were also solved mechanically, straight from the same material, to see how much",
            "of each one is reasoning and how much is lookup.", "",
            "| computed directly from | impact F1 | triage F1 | triage exact |", "|---|---|---|---|",
            "| coverage: every test that runs the changed **file** | 0.35 | — | — |",
            "| coverage: every test that runs the changed **function** | 0.57 | — | — |",
            "| the DAG, 'anything upstream failed means blocked' | — | 0.74 | 40% |",
            "| the DAG, 'blocked only by a prerequisite that already ran' | — | **1.00** | **100%** |", "",
            "The last row is the finding that matters: triage is not a judgement call, it is a rule, and",
            "the rule is exact. Every model arm scored what the *wrong* rule scores — because the spec",
            "prints `depends_on` without saying that specdeck never reorders tests, so a prerequisite",
            "listed later cannot block anything.", "",
            ]
    out += ["## Tokens", "", "| arm | calls | prompt | of which cached | completion | of which reasoning | total |", "|---|---|---|---|---|---|---|"]
    for arm, t in ledger.totals().items():
        out.append(f"| {arm} | {t['calls']} | {t['prompt']:,} | {t['cached']:,} | {t['completion']:,} | {t['reasoning']:,} | {t['total']:,} |")
    (RESULTS).write_text("\n".join(out) + "\n")
    (HERE / ("results.json" if SUBJECT == "tapedeck" else f"results-{SUBJECT}.json")).write_text(json.dumps({f"{k[0]}/{k[1]}": v for k, v in rows.items()}, indent=1))
    print("\n".join(out))


if __name__ == "__main__":
    main()
