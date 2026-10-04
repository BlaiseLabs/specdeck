"""What reviewers and agents see: findings anchored to lines, never a score.

Google found mutation scores "neither concrete nor actionable"; a finding on a
line with the exact change nobody noticed is both.
"""
from __future__ import annotations

from collections import defaultdict

from .model import FindingKind, LineStatus

GATING = {FindingKind.SURVIVING_MUTANT, FindingKind.WEAKENED_TEST, FindingKind.CHECKER_TAMPERING,
          FindingKind.CONTRADICTS_REQUIREMENT, FindingKind.REQUIREMENT_CHANGED}

_SECTIONS = {  # kind -> (title, explanation shown once; None = show each finding's own message)
    FindingKind.SURVIVING_MUTANT: (
        "Ran, but no test noticed a change",
        "Each change below leaves every test passing. For each line, check what the requirement says it "
        "should do, and add a test whose expected value comes from the requirement, not from running the code."),
    FindingKind.CONTRADICTS_REQUIREMENT: ("Tests that contradict an approved requirement", None),
    FindingKind.REQUIREMENT_CHANGED: ("Approved requirements changed on this branch", None),
    FindingKind.WEAKENED_TEST: ("Tests weakened on this branch", None),
    FindingKind.CHECKER_TAMPERING: ("Checker settings changed on this branch",
                                    "proofdeck ignores these and keeps the base branch's settings."),
    FindingKind.MOCK_ONLY: ("Only checked through fakes", None),
    FindingKind.RECOMPUTED_EXPECTATION: (
        "Expected value computed like the code",
        "These tests work out the expected value the same way the code does, so they can't disagree with it."),
    FindingKind.SPECIAL_CASED_INPUT: (
        "Code special-cases a test's exact input",
        "These branches return a fixed value for exactly the inputs a test uses. Check they're in the requirement."),
    FindingKind.VACUOUS_ASSERTION: ("Assertions that can't fail", "They pass for almost any result."),
    FindingKind.DRAFT_REQUIREMENT: (
        "New requirements to review",
        "Added on this branch. Read each one against what was actually asked for. Expected values taken "
        "from them count as confirmed once they are merged."),
    FindingKind.UNCONFIRMED_EXPECTATION: (
        "Expected values to confirm against the requirement",
        "These were written on this branch together with the code they check. A value copied from what the "
        "code printed passes even when the code is wrong, so check each one against the requirement."),
}


def _never_ran(report) -> list:
    return sorted(k for k, v in report.lines.items() if v == LineStatus.NEVER_RAN)


def render_markdown(report) -> str:
    counts = defaultdict(int)
    for status in report.lines.values():
        counts[status] += 1
    total = len(report.lines)
    out = ["## proofdeck", ""]
    if total:
        out.append(f"Checked, meaning a change to the line makes a test fail: **{counts[LineStatus.CHECKED]} of {total}** changed lines.")
        extra = []
        if counts[LineStatus.RAN_ONLY]:
            extra.append(f"{counts[LineStatus.RAN_ONLY]} ran without being checked")
        if counts[LineStatus.NEVER_RAN]:
            extra.append(f"{counts[LineStatus.NEVER_RAN]} never ran")
        if counts[LineStatus.MOCK_ONLY]:
            extra.append(f"{counts[LineStatus.MOCK_ONLY]} only checked through fakes")
        if counts[LineStatus.IMPORT_TIME]:
            extra.append(f"{counts[LineStatus.IMPORT_TIME]} run at import time and were not checked "
                         f"(fork mode; `--mode subprocess` checks them)")
        if extra:
            out.append(" · ".join(extra) + ".")
    else:
        out.append("No changed source lines to check.")
    out.append("")

    grouped = defaultdict(list)
    for f in report.findings:
        grouped[f.kind].append(f)
    order = list(_SECTIONS)
    for kind in order[:1]:
        out += _section(kind, grouped.get(kind, []))
    never = _never_ran(report)
    if never:
        out += ["### Never ran", "", "No test executes these changed lines.", ""]
        for path, line in never:
            snip = report.sources.get((path, line), "")
            out.append(f"- `{path}:{line}`" + (f" `{snip}`" if snip else ""))
        out.append("")
    for kind in order[1:]:
        out += _section(kind, grouped.get(kind, []))
    gate = "fails" if exit_code(report) else "passes"
    out.append(f"_Gate {gate}. Surviving changes, lines no test runs, weakened tests, checker changes, changed "
               f"requirements and tests that contradict one fail it; everything else is for the reviewer._")
    return "\n".join(out).rstrip() + "\n"


def _section(kind, items) -> list:
    if not items:
        return []
    title, why = _SECTIONS[kind]
    out = [f"### {title}", ""]
    if why:
        out += [why, ""]
    for f in items:
        head = f"- `{f.path}:{f.line}`"
        if f.mutant is not None:
            if not why:
                head += f" {f.message}"
            out += [head, "", "  ```diff", f"  - {f.mutant.original.strip()}", f"  + {f.mutant.mutated.strip()}",
                    "  ```", ""]
        else:
            if f.snippet and kind == FindingKind.DRAFT_REQUIREMENT:
                rid, _, says = f.snippet.partition(": ")
                head += f" **{rid}**: {says}"
            elif f.snippet:
                head += f" `{f.snippet}`"
            if not why:
                head += f" {f.message}"
            out.append(head)
    return out + [""]


def render_json(report) -> dict:
    """Lines with their status, findings, and the evidence per test: which tests ran each
    changed line, which noticed its mutant, and what each of those tests fakes. Tools that
    know what a test is *for* (specdeck's requirements) join on the node ids."""
    tests = sorted({n for v in report.ran_by.values() for n in v} | {n for v in report.killed_by.values() for n in v})
    return {
        "lines": [{"path": p, "line": n, "status": s.value, "at_import": (p, n) in report.at_import,
                   "ran_by": sorted(report.ran_by.get((p, n), [])),
                   "killed_by": list(report.killed_by.get((p, n), []))}
                  for (p, n), s in sorted(report.lines.items())],
        "tests": [{"nodeid": n, "fakes": list(report.fakes.get(n, []))} for n in tests],
        "findings": [
            {"kind": f.kind.value, "path": f.path, "line": f.line, "message": f.message,
             "mutant": None if f.mutant is None else {"operator": f.mutant.operator, "original": f.mutant.original,
                                                      "mutated": f.mutant.mutated}}
            for f in report.findings
        ],
    }


def exit_code(report, gate: bool = True) -> int:
    """1 when gating and something is unverified: a surviving mutant, a changed line no test runs,
    a weakened test or a checker change. Unconfirmed expectations and the other detectors are
    advisory and never fail the gate on their own."""
    if not gate:
        return 0
    if any(f.kind in GATING for f in report.findings):
        return 1
    if _never_ran(report):
        return 1
    return 0
