"""Analyse a branch: what each changed line's tests would notice, plus every detector."""
from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Optional

from . import config as _config
from . import detectors, provenance, runner, tampering
from . import ledger as _ledger
from .diff import added_lines, changed_lines
from .git import branch_text, merge_base
from .model import Finding, FindingKind, LineStatus, Report
from .mutate import mutants_for_line

MSG_SURVIVED = ("Tests run this line, but changing it to `{mutated}` didn't make any of them fail. "
                "Check what the requirement says this line should do, and add a test whose expected "
                "value comes from the requirement, not from running the code.")
MSG_MOCK_ONLY = ("Only tests that replace {names} with fakes notice a change to this line, so it has never "
                 "been checked against the real {what}.")


def _dedupe(findings: list) -> list:
    seen, out = set(), []
    for f in findings:
        key = (f.kind, f.path, f.line)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def analyze(repo: str, base: str = "main", test_command: Optional[str] = None,
            progress: Optional[Callable[[str], None]] = None, *, coverage: Optional[str] = None,
            mode: Optional[str] = None) -> Report:
    """Analyse the working tree of `repo` against where it branched from `base`.

    Changed, non-arid source lines that tests execute get one mutant each (up to
    the per-file budget); each mutant runs only the tests that execute its line.
    Configuration comes from the base branch. `coverage`: a coverage.py file with
    per-test contexts to use instead of tracing. `mode`: auto, fork or subprocess.
    """
    say = progress or (lambda msg: None)
    repo = str(Path(repo).resolve())
    rev = merge_base(repo, base)
    cfg = _config.load(repo, rev)
    if test_command:
        cfg["test_command"] = test_command
    report = Report()
    findings = list(tampering.scan(repo, base))
    ledger = _ledger.load(repo, base)
    if ledger.note:
        report.notes.append(ledger.note)
    findings += _ledger.entry_findings(ledger)
    findings += provenance.unconfirmed_expectations(repo, base, ledger)
    findings += provenance.against_requirements(repo, base, ledger)

    sources = {p: lines for p, lines in changed_lines(repo, base).items()
               if not any(fnmatch(p, pat) for pat in cfg["exclude"])}
    texts = {p: branch_text(repo, p) or "" for p in sources}

    # static detectors, on test lines this branch added
    for tpath, tlines in added_lines(repo, base, tests=True, sources=False).items():
        ttext = branch_text(repo, tpath) or ""
        test_side = detectors.vacuous_assertions(ttext, tpath)
        for ipath, itext in texts.items():
            test_side += detectors.recomputed_expectations(ttext, itext, tpath)
            findings += [f for f in detectors.special_cased_inputs(ttext, itext, ipath) if f.line in sources[ipath]]
        findings += [f for f in test_side if f.line in tlines]

    # mutation
    candidates = {}
    for path, lines in sources.items():
        for n in sorted(lines):
            ms = mutants_for_line(texts[path], n, path)
            if ms:
                candidates.setdefault(path, {})[n] = ms
                report.sources[(path, n)] = texts[path].splitlines()[n - 1].strip()
    if candidates:
        cov_file = coverage if coverage is not None else cfg["coverage"]
        if cov_file and not Path(cov_file).is_absolute():
            cov_file = str(Path(repo) / cov_file)
        mode = mode or cfg["mode"]
        outcome = None
        say(f"checking {sum(len(v) for v in candidates.values())} changed lines")
        if mode != "subprocess" and runner.fork_supported():
            try:
                outcome = runner.run_fork(repo, texts, candidates, cfg, cov_file or None)
            except runner.ForkUnavailable as exc:
                if mode == "fork":
                    raise RuntimeError(str(exc)) from exc
                report.notes.append(f"fork mode failed, ran one process per mutant instead: {exc}")
        elif mode == "fork":
            raise RuntimeError("fork mode needs os.fork, which this system doesn't have")
        if outcome is None:
            if cov_file:
                report.notes.append("the coverage file is only used in fork mode; traced the tests instead")
            outcome = runner.run_subprocess(repo, texts, candidates, cfg, say)
        report.notes += outcome.notes
        report.stats = dict(outcome.stats)
        report.ran_by = {k: list(v) for k, v in outcome.covering.items()}
        report.fakes = {n: list(f) for n, f in outcome.fakes.items()}
        report.at_import = set(outcome.at_import)
        for key in outcome.import_time:
            report.lines[key] = LineStatus.IMPORT_TIME
        for key in outcome.never_ran:
            report.lines[key] = LineStatus.NEVER_RAN
        for key in outcome.baseline_failing:  # every test that runs it already fails
            report.lines[key] = LineStatus.RAN_ONLY
        for m, result, killers, fakes in outcome.results:
            where = (m.path, m.line)
            if killers:
                report.killed_by[where] = list(killers)
                for k, names in zip(killers, fakes):
                    if names:
                        report.fakes.setdefault(k, list(names))
            if result in ("timeout", "crash"):   # the mutant hung or crashed the tests: that is noticed
                report.lines[where] = LineStatus.CHECKED
            elif result == "survived":
                report.lines[where] = LineStatus.RAN_ONLY
                findings.append(Finding(FindingKind.SURVIVING_MUTANT, m.path, m.line,
                                        MSG_SURVIVED.format(mutated=m.mutated.strip()), m))
            elif fakes and all(fakes):
                names = list(dict.fromkeys(n for f in fakes for n in f))
                report.lines[where] = LineStatus.MOCK_ONLY
                shown = ", ".join(f"`{n}`" for n in names[:4])
                findings.append(Finding(FindingKind.MOCK_ONLY, m.path, m.line, MSG_MOCK_ONLY.format(
                    names=shown, what="one" if len(names) == 1 else "ones"), m))
            else:
                report.lines[where] = LineStatus.CHECKED
        if outcome.skipped:
            report.notes.append(f"{outcome.skipped} covered lines were over the per-file mutation budget "
                                f"and were not mutated")
        for note in report.notes:
            say(note)
        st = report.stats
        if st.get("mode") == "fork":
            parts = [f"collected in {st.get('collect_s', 0):.1f} s",
                     ("traced the tests" if st.get("traced") else "read coverage") + f" in {st.get('coverage_s', 0):.1f} s"]
            if "baseline_s" in st:
                parts.append(f"baseline in {st['baseline_s']:.1f} s")
            parts.append(f"{st.get('mutants', 0)} mutants in {st.get('run_s', 0):.1f} s")
            say("fork mode, 1 pytest start: " + ", ".join(parts))
        elif st:
            say(f"subprocess mode: {st.get('pytest_starts')} pytest starts in {st.get('total_s', 0):.1f} s")

    order = {k: i for i, k in enumerate(FindingKind)}
    report.findings = sorted(_dedupe(findings), key=lambda f: (order[f.kind], f.path, f.line))
    return report


def _stderr(msg: str) -> None:
    print(f"proofdeck: {msg}", file=sys.stderr, flush=True)
