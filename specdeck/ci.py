"""A report for the place people actually look: the pull request.

``specdeck report`` reads the generated spec (and, when present, the requirements
report and a proofdeck JSON report) and writes Markdown shaped for a PR comment:
how many scenarios pass, what to fix first, every requirement with its status —
and, with proofdeck, whether a *met* requirement is proven or just passing.
"""
from __future__ import annotations

from typing import Optional

from .history import BAD, STATUS_ORDER, analyze
from .trust import join, summary_line

_LABEL = {"passed": "passing", "failed": "failing", "error": "error", "blocked": "blocked",
          "skipped": "skipped", "notrun": "not run"}


def scenarios_from_spec(spec: dict) -> list:
    """Scenario rows with id, name, status and depends_on, from the generated spec.

    The spec tags a skipped scenario ``status:skipped`` whether it was blocked by a
    failing prerequisite or skipped on its own; a skipped scenario with a failing
    or blocked prerequisite is shown as blocked, which is what specdeck did to it.
    """
    rows = []
    for sc in spec.get("scenarios", []):
        status = "notrun"
        for t in sc.get("tags") or []:
            if isinstance(t, str) and t.startswith("status:"):
                status = t[len("status:"):]
        if status == "pending":
            status = "notrun"
        rows.append({"id": sc["id"], "name": sc.get("name", sc["id"]), "group": sc.get("group", ""),
                     "status": status, "depends_on": list(sc.get("depends_on") or [])})
    by = {r["id"]: r for r in rows}
    changed = True
    while changed:  # a skip under a failing or blocked prerequisite is a block; cascades
        changed = False
        for r in rows:
            if r["status"] == "skipped" and any(by[d]["status"] in (*BAD, "blocked")
                                                for d in r["depends_on"] if d in by):
                r["status"] = "blocked"
                changed = True
    return rows


def build(spec: dict, requirements: Optional[dict] = None, proofdeck: Optional[dict] = None) -> dict:
    scenarios = scenarios_from_spec(spec)
    analysis = analyze(scenarios)
    counts = {k: 0 for k in STATUS_ORDER}
    for s in scenarios:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    by = {s["id"]: s for s in scenarios}
    trust = join(requirements, proofdeck, spec=spec) if requirements and proofdeck else None
    changed = None
    if proofdeck is not None:
        changed = {k: 0 for k in ("checked", "ran-only", "never-ran", "mock-only", "import-time")}
        for ln in proofdeck.get("lines", []):
            changed[ln["status"]] = changed.get(ln["status"], 0) + 1
    return {
        "feature": spec.get("feature", ""),
        "counts": {**counts, "total": len(scenarios)},
        "frontier": [by[i] for i in analysis["frontier"]],
        "blocked": [s for s in scenarios if s["status"] == "blocked"],
        "requirements": requirements,
        "trust": trust,
        "changed": changed,
    }


def render_markdown(model: dict) -> str:
    c = model["counts"]
    out = [f"## specdeck — {model['feature']}" if model["feature"] else "## specdeck", ""]
    if not c["total"]:
        out += ["No scenarios in the spec.", ""]
    else:
        rest = [f"{c[k]} {_LABEL[k]}" for k in STATUS_ORDER[1:] if c[k]]
        out.append(f"**{c['passed']} of {c['total']} scenarios pass**" + (" · " + " · ".join(rest) if rest else "") + ".")
        out.append("")
    if model["frontier"]:
        out += ["### Fix first", "",
                "Failing with every prerequisite passing — the root causes. Everything blocked below waits on these.", ""]
        for s in model["frontier"]:
            held = s.get("holding_up", 0)
            out.append(f"- `{s['id']}` {s['name']}" + (f" — holds up {held}" if held else ""))
        out.append("")
    if model["blocked"]:
        out += [f"<details><summary>{len(model['blocked'])} blocked by a failing prerequisite</summary>", ""]
        for s in model["blocked"]:
            roots = ", ".join(f"`{r}`" for r in s.get("blocked_by", [])) or "a prerequisite"
            out.append(f"- `{s['id']}` ← {roots}")
        out += ["", "</details>", ""]

    reqs = model["requirements"]
    if reqs:
        rc = reqs["counts"]
        bits = [f"{rc[k]} {k}" for k in ("failing", "blocked", "not-run", "untested") if rc.get(k)]
        out += ["### Requirements", "",
                f"**{rc['met']} of {rc['total']} met**" + (" · " + " · ".join(bits) if bits else "") + "."]
        trust = model["trust"]
        if trust:
            out.append(summary_line(trust).replace("trust: ", "", 1).capitalize() + ".")
        out.append("")
        verdicts = {r["id"]: r for r in trust["requirements"]} if trust else {}
        head = "| requirement | status | proof |" if trust else "| requirement | status |"
        out += [head, "|---|---|---|" if trust else "|---|---|"]
        mark = {"met": "✔", "failing": "✖", "blocked": "⊘", "not-run": "·", "untested": "?"}
        for r in reqs["requirements"]:
            row = f"| `{r['id']}` {_md(r.get('statement') or r.get('says', ''))} | {mark.get(r['status'], '?')} {r['status']} |"
            if trust:
                row += f" {_proof(verdicts[r['id']])} |"  # the join covers every requirement
            out.append(row)
        for u in reqs.get("unknown", []):
            out.append(f"| `{u['id']}` *(not in the requirements file)* | ! covered by {', '.join(u['scenarios'])} |"
                       + (" |" if trust else ""))
        out.append("")
        for p in reqs.get("problems", []):
            out.append(f"- ⚠ {p}")
        if reqs.get("problems"):
            out.append("")

    ch = model["changed"]
    if ch is not None:
        total = sum(ch.values())
        if total:
            parts = [f"{ch['checked']} checked"]
            for k, label in (("ran-only", "ran but not checked"), ("never-ran", "never ran"),
                             ("mock-only", "only checked through fakes"),
                             ("import-time", "run at import time, not checked")):
                if ch.get(k):
                    parts.append(f"{ch[k]} {label}")
            out += ["### Changed lines (proofdeck)", "", f"{total} changed source lines: " + ", ".join(parts) + ".", ""]
        else:
            out += ["### Changed lines (proofdeck)", "", "No changed source lines to check.", ""]
    return "\n".join(out).rstrip() + "\n"


def _proof(v: dict) -> str:
    if v["status"] != "met":
        return "—"
    where = lambda kind: ", ".join(f"`{x}`" for x in v["lines"][kind][:3])  # noqa: E731
    text = {
        "checked": "✔ checked",
        "unproven": "✖ **unproven** — a covering test ran " + where("unproven") + " and didn't notice it changed",
        "mock-only": "≈ **mock-only** — " + where("mock-only") + " only noticed through fakes",
        "untouched": "· untouched by this change",
    }[v["verdict"]]
    if v["through_fakes"]:
        text += f" · every covering test fakes {', '.join(f'`{n}`' for n in v['fakes'][:4])}"
    return text


def _md(s: str) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def exit_code(model: dict, *, fail_on_failing=False, fail_on_unmet=False, fail_on_untrusted=False) -> int:
    c = model["counts"]
    if fail_on_failing and (c["failed"] or c["error"] or c["blocked"]):
        return 1
    reqs, trust = model["requirements"], model["trust"]
    if fail_on_unmet and reqs and reqs["counts"]["met"] != reqs["counts"]["total"]:
        return 1
    if fail_on_untrusted and trust and trust["counts"]["untrusted"]:
        return 1
    return 0
