"""`specdeck context` — one document that answers an agent's questions about a suite.

What goes in here was decided by measurement, not taste (bench/agent_ab). Against the
raw test files as a baseline, on questions with mechanical ground truth:

- the spec alone scored 0.80 on "which tests fail if this line changes" (tests: 0.93);
  it names no source code, so a diff cannot be connected to a scenario. Adding the
  **coverage map** took it to 0.88 and cut missed failures from 25 to 4 (tests: 2).
- on "which of these failures are root causes", every arm scored 0.77 / 40% exact —
  precisely what the *wrong* blocking rule scores. Stating the **rule** in 100 tokens
  took both the spec and the test files to 1.00 / 100%.

So: say the rule, name the code, and number the scenarios so the map is cheap.

What a test is *for* is not in the tests at all. Asked why tests exist, with comments
and docs stripped from the code (bench/purpose), an agent given this document without
the requirements scored 6/10: it carried requirement ids as tags but never what they
say or why. So the requirements go in with their reasons and the tests linked to them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .tracing import index

RULE = """\
## How to read `depends_on`

`depends_on` is documentation order, not execution order: specdeck never reorders
tests, and the scenarios below are listed in the order they run. A scenario is
skipped as *blocked* only when a prerequisite that ran **earlier** did not pass, and
blocking cascades from there. A scenario whose only failed prerequisite runs **later**
is not skipped at all — it runs normally, and can fail on its own.
"""

COVERAGE_NOTE = """\
## Which scenarios execute which code

Recorded by running the suite with each test in its own coverage context. The numbers
are the `n:` of the scenarios above.

**Read this as a bound, not a hint.** For a change inside a function, the scenarios
listed against that function are the only ones that execute it, so they are the only
ones that can fail. Naming any other scenario is always wrong. A function that does not
appear below is executed by no scenario at all: changing its body cannot make any test
fail.

**The bound is over code that runs during a test.** Lines that run while the module is
imported are deliberately not listed, because every test would run them and that
distinguishes nothing. Those are the module's top level — constants, class bodies,
decorators, default arguments. A change there is outside the bound and can affect any
scenario whose test imports the module.

**A function most of the suite runs is marked `most (N of M)` instead of being listed.**
Such a function — a hook, a constructor, a config reader — is run by nearly every test, so
knowing that tells you nothing about which test a change to it would break, and the honest
answer is usually far *fewer* tests than the count, not more. Do not read a large count as
a long answer. If you cannot narrow it down from the scenarios' Then steps, say so rather
than listing them.

**Executing a function is not checking it.** A scenario listed here runs that code but
only fails if one of its Then steps actually disagrees, so the real answer is usually
smaller than the list. Which of them would notice is not in this document and cannot be
read off the tests either — it is what `proofdeck check` measures by changing the line
and running them.
"""




KEEP_WHEN_TRIMMED = ("id", "n", "name", "group", "depends_on", "logic")

FOCUS_NOTE = """\
Scenarios that execute none of the files in question are listed in brief — id, name,
dependencies and how to run them — with their steps left out. They are still here
because `depends_on` reaches across the whole suite and a prerequisite you cannot see
is worse than one described briefly.
"""


def _trim(sc: dict) -> dict:
    """A scenario in brief: enough to depend on it, run it and name it; no steps."""
    out = {k: sc[k] for k in KEEP_WHEN_TRIMMED if k in sc}
    status = [t for t in sc.get("tags") or [] if isinstance(t, str) and t.startswith("status:")]
    if status:
        out["tags"] = status
    return out


REQUIREMENTS_NOTE = """\
## Requirements

What the project needs and why, from its requirements file. Under each requirement are
the tests linked to it with `covers=`. A test can serve more than one requirement; when
it does, the others are named next to it, so "which tests exist only for this" is the
tests with nothing named beside them.
"""


def _requirements(report: dict) -> str:
    """Each requirement's says, why and status, then the tests that cover it."""
    reqs = report.get("requirements") or []
    by_test: dict = {}
    for r in reqs:
        for sc in r.get("scenarios") or []:
            by_test.setdefault(sc.get("nodeid") or sc.get("id"), []).append(r["id"])
    out = [REQUIREMENTS_NOTE]
    for r in reqs:
        out.append(f"### {r['id']} ({r.get('status', 'unknown')})")
        out.append("")
        out.append(f"Says: {r['says']}")
        if r.get("why"):
            out.append(f"Why: {' '.join(str(r['why']).split())}")
        tests = r.get("scenarios") or []
        if not tests:
            out.append("Tests: none")
        else:
            out.append("Tests:")
            for sc in tests:
                key = sc.get("nodeid") or sc.get("id")
                also = [o for o in by_test.get(key, []) if o != r["id"]]
                note = f" (also covers {', '.join(also)})" if also else ""
                out.append(f"- `{key}` — scenario `{sc.get('id')}`{note}")
        out.append("")
    return "\n".join(out).rstrip()


SOURCES_NOTE = """\
## The test files

Verbatim, for when the question is how a test is wired rather than what it checks.
"""


def build(spec: dict, coverage: Optional[dict] = None, focus: Optional[list] = None,
          profile: Optional[str] = None, root: Optional[str] = None,
          requirements: Optional[dict] = None) -> str:
    """The agent document: the requirements, the numbered spec, the coverage map, the rule.

    ``requirements``: the report a run wrote (.specdeck/requirements.json), for the
    requirements section; profiles that carry it skip it when there is none.

    ``focus``: source files the reader cares about (a diff, usually). Scenarios that run
    none of them are listed in brief, and the map covers only those files. On a large
    suite that is most of the document, and the part a reviewer will not read.
    """
    from . import profiles as _profiles

    prof = _profiles.get(profile)
    spec = dict(spec)
    sources = _profiles.test_files(spec, root) if (prof.test_sources and root) else []
    if not prof.coverage:
        coverage = None
    if not prof.index:
        spec = dict(spec, scenarios=[])   # after the node ids have been read out of it
    focus = [str(f) for f in (focus or [])]
    relevant: Optional[set] = None
    if focus and coverage:
        relevant = {sid for sid, places in coverage.items()
                    if any(p.split(":")[0] in focus for p in places)}
    scenarios = []
    numbers = {}
    trimmed = 0
    for i, sc in enumerate(spec.get("scenarios", []), 1):
        sc = dict(sc)
        sc["n"] = i
        numbers[sc["id"]] = i
        if not prof.steps:
            sc = _trim(sc)
        elif relevant is not None and sc["id"] not in relevant:
            sc = _trim(sc)
            trimmed += 1
        scenarios.append(sc)
    spec["scenarios"] = scenarios

    feature = spec.get("feature", "the suite")
    out = [f"# {feature} — the test suite, for an agent", ""]
    if prof.index:
        out += ["Generated by specdeck from the tests themselves, so it cannot drift from them.",
                "Each scenario is one test: `logic` is the pytest node id that runs it, the steps are",
                "what it does and checks as recorded while it ran, and `status:` is from the last run.", ""]
    if focus:
        out += [f"Written for a change to: {', '.join(f'`{f}`' for f in focus)}.", ""]
        if trimmed:
            out += [FOCUS_NOTE]
    if prof.requirements and requirements and requirements.get("requirements"):
        out += [_requirements(requirements), ""]
    if prof.index:
        out += ["## Scenarios", "",
                "```yaml", yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100).rstrip(), "```"]
    if coverage:
        shown = {sid: [p for p in places if not focus or p.split(":")[0] in focus]
                 for sid, places in coverage.items()}
        mapped = index({k: v for k, v in shown.items() if v}, numbers, total=len(numbers))
        lines = []
        for file in mapped:
            lines.append(f"{file}:")
            for fn, ns in mapped[file].items():
                if isinstance(ns, tuple):
                    lines.append(f"  {fn}: most ({ns[1]} of {ns[2]})")
                else:
                    lines.append(f"  {fn}: [{','.join(str(n) for n in ns)}]")
        out += ["", COVERAGE_NOTE, "```yaml", "\n".join(lines), "```"]
    # The blocking rule goes last, closest to the question being asked. Measured: with it
    # first and the coverage notes last, sorting failures from blocked fell from 100% exact
    # to 70% — the later text won the reader's attention.
    if sources:
        blocks = []
        for rel in sources:
            try:
                text = (Path(root) / rel).read_text(encoding="utf-8")
            except OSError:
                continue
            blocks.append(f"### {rel}\n\n```python\n{text}\n```")
        if blocks:
            out += ["", SOURCES_NOTE, "\n".join(blocks)]
    if prof.rule:
        out += ["", RULE]
    return "\n".join(out).rstrip() + "\n"


def load(spec_path, coverage_path) -> tuple:
    spec = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))
    cov = None
    p = Path(coverage_path)
    if p.exists():
        import json

        cov = json.loads(p.read_text(encoding="utf-8"))
    return spec, cov


def load_requirements(path) -> Optional[dict]:
    """The requirements report a run wrote, or None when the project has none."""
    import json

    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
