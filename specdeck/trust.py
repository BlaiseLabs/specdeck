"""Join specdeck's requirements with proofdeck's evidence: is a *met* requirement proven?

specdeck knows what each test is for (``covers=``) and whether it passes. proofdeck
knows, for every line a branch changed, which tests executed it, whether any of them
noticed the line being wrong, and what each of those tests replaces with fakes.
Joined on the pytest node id, that answers the question neither tool can alone:
is this requirement *met*, or just *passing*?

Per requirement, over the changed lines its covering tests execute:

- ``unproven``   a covering test ran a changed line and did not notice its mutant
- ``mock-only``  changed lines were only noticed through fakes
- ``checked``    every changed line was noticed, by a test that fakes nothing
- ``untouched``  this change did not reach anything the covering tests run

Independently, ``through_fakes`` says every covering test fakes a collaborator —
true whatever the branch changed.
"""
from __future__ import annotations

from typing import Optional

VERDICTS = ("unproven", "mock-only", "checked", "untouched")
_WORST = {"unproven": 0, "mock-only": 1, "checked": 2, "untouched": 3}


def _nodeids(req: dict, by_scenario: dict) -> list:
    out = []
    for s in req.get("scenarios", []):
        n = s.get("nodeid") or by_scenario.get(s["id"])
        if n:
            out.append(n)
    return list(dict.fromkeys(out))


def join(requirements: dict, proofdeck: dict, *, spec: Optional[dict] = None) -> dict:
    """``requirements``: specdeck's requirements report; ``proofdeck``: ``proofdeck check --format json``.

    ``spec`` (the generated spec) is only used to find node ids for reports written
    before they carried one.
    """
    by_scenario = {}
    for sc in (spec or {}).get("scenarios", []):
        for cmd in sc.get("logic") or []:  # always strings in a generated spec
            if cmd.startswith("pytest "):
                by_scenario[sc["id"]] = cmd[len("pytest "):].strip()
    fakes = {t["nodeid"]: list(t.get("fakes") or []) for t in proofdeck.get("tests", [])}
    # a line that ran while its module was imported was "run" by every test; it says nothing
    # about any one requirement, and fork mode cannot check it at all (status import-time)
    lines = [ln for ln in proofdeck.get("lines", []) if not ln.get("at_import") and ln["status"] != "import-time"]

    out = []
    for req in requirements.get("requirements", []):
        mine = set(_nodeids(req, by_scenario))
        touched = [ln for ln in lines if mine & set(ln.get("ran_by") or [])]
        unproven = [ln for ln in touched if ln["status"] == "ran-only"]
        mock = [ln for ln in touched if ln["status"] == "mock-only"]
        checked = [ln for ln in touched if ln["status"] == "checked"]
        own = [ln for ln in checked
               if any(k in mine and not fakes.get(k) for k in ln.get("killed_by") or [])]
        if not touched:
            verdict = "untouched"
        elif unproven:
            verdict = "unproven"
        elif mock:
            verdict = "mock-only"
        else:
            verdict = "checked"
        faked = sorted({n for t in mine for n in fakes.get(t, [])})
        through_fakes = bool(mine) and all(fakes.get(t) for t in mine)
        out.append({
            "id": req["id"], "says": req.get("says", ""), "status": req["status"], "verdict": verdict,
            "tests": sorted(mine), "through_fakes": through_fakes, "fakes": faked,
            "lines": {
                "unproven": [_loc(ln) for ln in unproven],
                "mock-only": [_loc(ln) for ln in mock],
                "checked": [_loc(ln) for ln in checked],
            },
            "checked_by_own_test": bool(own),
        })

    met = [r for r in out if r["status"] == "met"]
    counts = {v: sum(1 for r in met if r["verdict"] == v) for v in VERDICTS}
    counts["met"] = len(met)
    counts["through_fakes"] = sum(1 for r in met if r["through_fakes"])
    counts["untrusted"] = counts["unproven"] + counts["mock-only"]
    out.sort(key=lambda r: (_WORST[r["verdict"]], not r["through_fakes"], r["id"]))
    return {"counts": counts, "requirements": out}


def _loc(ln: dict) -> str:
    return f"{ln['path']}:{ln['line']}"


def summary_line(result: dict) -> str:
    c = result["counts"]
    if not c["met"]:
        return "trust: no requirement is met, nothing to check"
    bits = [f"{c['checked']} checked"]
    if c["unproven"]:
        bits.append(f"{c['unproven']} unproven")
    if c["mock-only"]:
        bits.append(f"{c['mock-only']} mock-only")
    if c["untouched"]:
        bits.append(f"{c['untouched']} untouched by this change")
    s = f"trust: of {c['met']} met requirements, " + ", ".join(bits)
    if c["through_fakes"]:
        s += f"; {c['through_fakes']} met only by tests that fake a collaborator"
    return s


def render_text(result: dict) -> str:
    mark = {"checked": "✔", "unproven": "✖", "mock-only": "≈", "untouched": "·"}
    rows = [summary_line(result)]
    for r in result["requirements"]:
        flag = " (through fakes: " + ", ".join(r["fakes"]) + ")" if r["through_fakes"] else ""
        rows.append(f"  {mark[r['verdict']]} {r['id']:<28} {r['status']:<9} {r['verdict']:<10}{flag}")
        for kind in ("unproven", "mock-only"):
            for loc in r["lines"][kind]:
                rows.append(f"      {kind:<9} {loc}")
    return "\n".join(rows)
