"""What to send an agent, and how much of it: named strategies with measured trade-offs.

Nothing stops a tool from sending the test files themselves, and for one question that is
the better answer. The A/B in bench/agent_ab measured both materials on the same questions,
and the winner depends on what is being asked:

- *which tests does this change break?* — the test code wins on a small suite (0.93 against
  0.88 F1) and roughly ties on a larger one. Prose describing a test does not say how it is
  wired; the code does.
- *which of these failures is a root cause, and which is only blocked behind one?* — the
  generated material wins outright, 100% against 40% exact, because the answer depends on a
  rule about how specdeck runs tests that no test file states.

So the strategies differ in what they carry. Adding material is not free in either
direction: giving the test files the dependency rule as well took "which tests break" from
0.93 to 0.85, and loading the generated document with coverage instructions took root-cause
sorting from 100% to 70%. Every arrangement measured so far is best at one question and
worse at the other, which is why this is a choice and not a default.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Profile:
    name: str
    index: bool             # the scenarios at all (ids, names, dependencies, node ids)
    steps: bool             # each scenario's recorded Given/When/Then
    coverage: bool          # the map of which scenarios run which functions
    rule: bool              # how to read depends_on — worth 40% -> 100% on triage
    test_sources: bool      # the test files verbatim
    summary: str
    measured: str
    requirements: bool = False   # each requirement's statement and rationale (29148 §5.2.8), with its tests

    @property
    def needs_coverage(self) -> bool:
        return self.coverage


PROFILES = {
    "brief": Profile(
        "brief", index=True, steps=False, coverage=True, rule=True, test_sources=False,
        summary="Cheapest. Scenario index, the coverage map and the dependency rule; no recorded steps.",
        measured="tapedeck 7.2k tokens (44% under the test files). Root cause vs blocked: 100% exact. "
                 "Not measured on 'which tests break' — the steps it drops are the behaviour detail."),
    "lean": Profile(
        "lean", index=True, steps=True, coverage=False, rule=True, test_sources=False,
        summary="Scenarios with their steps and the dependency rule, no coverage map.",
        measured="tapedeck 8.7k tokens. Root cause vs blocked: 100% exact. Without the map, 'which tests "
                 "break' was 0.80 F1 against 0.88 with it."),
    "balanced": Profile(
        "balanced", index=True, steps=True, coverage=True, rule=True, test_sources=False, requirements=True,
        summary="Everything specdeck knows, nothing it doesn't. The default.",
        measured="tapedeck 12.1k tokens: 0.88 F1 on 'which tests break', 100% exact on root cause. "
                 "specdeck's own 88-test suite 23.4k: 0.79 F1 and 11 wrong names, beating the test "
                 "files (0.75, 16) at 27% fewer tokens. Those numbers predate the requirements "
                 "section, which tapedeck (no requirements file) does not get."),
    "wiring": Profile(
        "wiring", index=False, steps=False, coverage=False, rule=False, test_sources=True,
        summary="The test files, verbatim, and nothing else. Best at 'which tests break'.",
        measured="tapedeck 12.8k tokens: 0.93 F1, the highest measured. The cost is structure: root "
                 "cause vs blocked drops to 40% exact, because no test file states the rule."),
    "everything": Profile(
        "everything", index=True, steps=True, coverage=True, rule=True, test_sources=True, requirements=True,
        summary="Both materials at once. Measured as a waste on a small suite; kept for large ones.",
        measured="tapedeck 21.3k+ tokens for the same 0.93 F1 as the test files alone — 67% more "
                 "tokens, identical answers."),
    "intent": Profile(
        "intent", index=False, steps=False, coverage=False, rule=False, test_sources=False, requirements=True,
        summary="The requirements alone: each statement, its rationale, and which tests verify it. For 'what is this test for?'",
        measured="Measured in bench/purpose (specdeck's own suite, comments and docs stripped); see "
                 "RESULTS.md there."),
}
DEFAULT = "balanced"

# What to send when you know what is being asked. Each is the best arrangement measured for
# that question, which is not the same arrangement twice: the dependency rule takes root-cause
# sorting from 40% to 100% and costs "which tests break" 0.93 -> 0.85, so the specialist for
# that question deliberately leaves it out.
FOR_TASK = {
    "breaks": ("wiring", "which tests a change breaks — the test code wins; adding anything else dilutes it"),
    "stuck": ("brief", "which failures are root causes and what to fix first — needs the rule, not the code"),
    "why": ("intent", "what a test or requirement is for — the reasons live in the requirements, not the tests"),
    "unknown": (DEFAULT, "no single question in mind — the arrangement that is never badly wrong"),
}


def for_task(task: Optional[str]) -> "Profile":
    key = (task or "unknown").lower()
    if key not in FOR_TASK:
        raise KeyError(f"unknown task {task!r}; choose from {', '.join(FOR_TASK)}")
    return get(FOR_TASK[key][0])


def get(name: Optional[str]) -> Profile:
    key = (name or DEFAULT).lower()
    if key not in PROFILES:
        raise KeyError(f"unknown profile {name!r}; choose from {', '.join(PROFILES)}")
    return PROFILES[key]


def test_files(spec: dict, root) -> list:
    """The test files behind this spec, from the node ids in each scenario's `logic`."""
    root = Path(root)
    seen: list = []
    for sc in spec.get("scenarios", []):
        for cmd in sc.get("logic") or []:
            if not cmd.startswith("pytest "):
                continue
            rel = cmd[len("pytest "):].split("::", 1)[0].strip()
            if rel not in seen:
                seen.append(rel)
    files = []
    for rel in seen:
        conftest = (root / rel).parent / "conftest.py"
        if conftest.exists() and str(conftest.relative_to(root)) not in files:
            files.append(str(conftest.relative_to(root)))
        if (root / rel).exists():
            files.append(rel)
    return files
