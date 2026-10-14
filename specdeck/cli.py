"""The specdeck command line.

    specdeck serve         run the viewer
    specdeck validate      lint the generated spec and the requirements file (CI gate)
    specdeck requirements  print the requirements report
    specdeck trust         join the requirements with a proofdeck report: met, or just passing?
    specdeck report        Markdown for a PR comment: scenarios, fix-first, requirements, proof
    specdeck context       one document describing the suite, for a coding agent
    specdeck agents        the project's rules, written into AGENTS.md with their status
    specdeck check         evaluate the rules' checks against the change, without running tests
    specdeck breaks        how often each rule broke, from the log pytest runs append to
    specdeck rules         share rules through a registry (search, add, publish, init)
    specdeck srs           the software requirements specification, in ISO/IEC/IEEE 29148:2018 form
    specdeck watch         re-run the suite when a file changes
    specdeck runs          list recorded runs
    specdeck diff A B      what changed between two runs
    specdeck prune         drop old runs from the shared history
    specdeck forget SLUG   remove a project from history
    specdeck where         print the history database path
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .recorder import DEFAULT_PORT

DEFAULT_SPEC = ".specdeck/spec.yaml"
DEFAULT_REQUIREMENTS = "requirements.yaml"


def _add_serve(sub) -> None:
    s = sub.add_parser("serve", help="run the viewer on 127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.environ.get("SPECDECK_PORT") or DEFAULT_PORT))
    s.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="extra Host header name to accept (e.g. a tunnel hostname); the bind stays 127.0.0.1")


def _add_validate(sub) -> None:
    v = sub.add_parser("validate", help="lint the spec and requirements file; non-zero on error")
    v.add_argument("spec", nargs="?", default=DEFAULT_SPEC, help=f"spec file (default {DEFAULT_SPEC})")
    v.add_argument("--requirements", default=None,
                   help=f"requirements file to check too (default {DEFAULT_REQUIREMENTS} if it exists)")
    v.add_argument("--strict", action="store_true", help="treat warnings as errors")
    v.add_argument("--format", choices=("text", "json"), default="text")


def _add_requirements(sub) -> None:
    r = sub.add_parser("requirements", help="print the requirements report from the last run")
    r.add_argument("--report", default=".specdeck/requirements.json",
                   help="report written by the last run (default .specdeck/requirements.json)")
    r.add_argument("--format", choices=("text", "json"), default="text")
    r.add_argument("--fail-on-unmet", action="store_true", help="exit non-zero unless every requirement is met")


def _add_trust(sub) -> None:
    t = sub.add_parser("trust", help="join the requirements report with proofdeck's JSON: is each met "
                                     "requirement proven, or just passing?")
    t.add_argument("proofdeck", help="proofdeck check --format json output; - for stdin")
    t.add_argument("--report", default=".specdeck/requirements.json",
                   help="requirements report from the last run (default .specdeck/requirements.json)")
    t.add_argument("--spec", default=DEFAULT_SPEC, help="generated spec, for node ids (default .specdeck/spec.yaml)")
    t.add_argument("--format", choices=("text", "json"), default="text")
    t.add_argument("--fail-on-untrusted", action="store_true",
                   help="exit non-zero if a met requirement is unproven or mock-only on this change")


def _add_report(sub) -> None:
    r = sub.add_parser("report", help="a Markdown report shaped for a pull-request comment")
    r.add_argument("--spec", default=DEFAULT_SPEC, help=f"generated spec (default {DEFAULT_SPEC})")
    r.add_argument("--requirements-report", default=".specdeck/requirements.json",
                   help="requirements report from the last run, if it exists")
    r.add_argument("--proofdeck", default=None, metavar="FILE",
                   help="proofdeck check --format json output, to say whether met requirements are proven")
    r.add_argument("--format", choices=("markdown", "json"), default="markdown")
    r.add_argument("--out", default=None, help="write here instead of stdout")
    r.add_argument("--fail-on-failing", action="store_true", help="exit 1 if any scenario fails or is blocked")
    r.add_argument("--fail-on-unmet", action="store_true", help="exit 1 unless every requirement is met")
    r.add_argument("--fail-on-untrusted", action="store_true",
                   help="exit 1 if a met requirement is unproven or mock-only (needs --proofdeck)")


def _add_context(sub) -> None:
    c = sub.add_parser("context", help="one document describing the suite for a coding agent: the "
                                       "scenarios, how to read depends_on, and which scenarios run which code")
    c.add_argument("--spec", default=DEFAULT_SPEC, help=f"generated spec (default {DEFAULT_SPEC})")
    c.add_argument("--coverage", default=".specdeck/coverage.json",
                   help="coverage map from a run with --specdeck-coverage (included when it exists)")
    c.add_argument("--requirements-report", default=None, metavar="PATH",
                   help="requirements report from the last run (default: requirements.json next to the spec)")
    c.add_argument("--out", default=None, help="write here instead of stdout")
    c.add_argument("--profile", default=None, metavar="NAME",
                   help="how much to send: lean | balanced (default) | brief | intent | max. `specdeck context "
                        "--profiles` describes each with its measured cost and accuracy")
    c.add_argument("--task", default=None, metavar="WHAT",
                   help="what the agent will be asked — breaks | stuck | why | unknown. Picks the profile that "
                        "measured best for that question, which is not the same one twice")
    c.add_argument("--profiles", action="store_true", help="describe the strategies and exit")
    c.add_argument("--for", dest="focus", default=None, metavar="PATHS",
                   help="comma-separated source files the reader cares about (a diff). Scenarios that run "
                        "none of them are listed in brief and the coverage map covers only these files — "
                        "much shorter on a large suite")


def _add_rules(sub) -> None:
    r = sub.add_parser("rules", help="share rules through a registry: a git repository of rule cards")
    rs = r.add_subparsers(dest="rules_cmd", required=True)
    reg = {"default": os.environ.get("SPECDECK_REGISTRY"), "metavar": "DIR_OR_GIT_URL",
           "help": "the registry: a directory, or a git URL to clone (default $SPECDECK_REGISTRY)"}
    i = rs.add_parser("init", help="create an empty registry (a git repository with rules/)")
    i.add_argument("path")
    s = rs.add_parser("search", help="find rules in the registry")
    s.add_argument("query", nargs="*")
    s.add_argument("--registry", **reg)
    w = rs.add_parser("show", help="print a rule card")
    w.add_argument("id")
    w.add_argument("--registry", **reg)
    a = rs.add_parser("add", help="add a rule from the registry to requirements.yaml, with its check")
    a.add_argument("id")
    a.add_argument("--registry", **reg)
    a.add_argument("--requirements", default=DEFAULT_REQUIREMENTS)
    p = rs.add_parser("publish", help="publish one of this project's rules into a registry checkout and commit it")
    p.add_argument("id")
    p.add_argument("--registry", **reg)
    p.add_argument("--requirements", default=DEFAULT_REQUIREMENTS)
    p.add_argument("--message", default="")


def _add_watch(sub) -> None:
    w = sub.add_parser("watch", help="re-run the suite when a file changes")
    w.add_argument("pytest_args", nargs=argparse.REMAINDER, help="arguments passed through to pytest")
    w.add_argument("--root", default=".", help="directory to watch (default .)")
    w.add_argument("--interval", type=float, default=0.6)
    w.add_argument("--no-live", action="store_true", help="do not add --specdeck-live")


def _add_history(sub) -> None:
    rs = sub.add_parser("runs", help="list recorded runs")
    rs.add_argument("--project", default=None, help="project slug (default: the project in this directory)")
    rs.add_argument("--limit", type=int, default=20)
    rs.add_argument("--format", choices=("text", "json"), default="text")

    d = sub.add_parser("diff", help="what changed between two runs")
    d.add_argument("left", type=int, metavar="RUN_A")
    d.add_argument("right", type=int, metavar="RUN_B")
    d.add_argument("--project", default=None)
    d.add_argument("--format", choices=("text", "json"), default="text")

    p = sub.add_parser("prune", help="drop all but the newest runs per project")
    p.add_argument("--keep", type=int, default=200, help="runs to keep per project (default 200)")
    p.add_argument("--project", default=None, help="only this project")

    f = sub.add_parser("forget", help="remove a project and its history")
    f.add_argument("slug")

    sub.add_parser("where", help="print the history database path")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="specdeck", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add_serve(sub)
    _add_validate(sub)
    _add_requirements(sub)
    _add_trust(sub)
    _add_report(sub)
    _add_context(sub)
    s = sub.add_parser("srs", help="the software requirements specification, generated in the form of "
                                   "ISO/IEC/IEEE 29148:2018 (content of §9.6), from the requirements and the last run")
    s.add_argument("--requirements", default=DEFAULT_REQUIREMENTS, help="requirements file (default requirements.yaml)")
    s.add_argument("--report", default=".specdeck/requirements.json",
                   help="requirements report from the last run, for verification status")
    s.add_argument("--spec", default=DEFAULT_SPEC, help="generated spec, for tests that trace to no requirement")
    s.add_argument("--out", default=None, help="write here instead of stdout (e.g. SRS.md)")
    a = sub.add_parser("agents", help="write the project's rules (design constraints, standards compliance, "
                                      "Definition of Done) into the agent instructions file, with their status")
    a.add_argument("--requirements", default=DEFAULT_REQUIREMENTS, help="requirements file (default requirements.yaml)")
    a.add_argument("--report", default=".specdeck/requirements.json",
                   help="requirements report from the last run, for each rule's status")
    a.add_argument("--out", action="append", default=None, metavar="FILE",
                   help="instructions file to update; repeat for several, - for stdout. Default: CLAUDE.md "
                        "and/or AGENTS.md, whichever exist (both if both do), else AGENTS.md. Only the "
                        "generated block is replaced; the rest of each file is kept")
    k = sub.add_parser("check", help="evaluate the rules' checks against the change, without running tests "
                                     "(for CI, a pre-commit hook, or an agent's hook after an edit)")
    k.add_argument("--requirements", default=DEFAULT_REQUIREMENTS, help="requirements file (default requirements.yaml)")
    k.add_argument("--base", default="HEAD", help="git revision to compare the change against (default HEAD: "
                                                  "the uncommitted work)")
    k.add_argument("--spec", default=DEFAULT_SPEC, help="generated spec, for `test:` checks (last run's outcomes)")
    k.add_argument("--format", choices=("text", "json"), default="text")
    k.add_argument("--fail", action="store_true", help="exit 1 when a rule is broken")
    b = sub.add_parser("breaks", help="how often each rule broke, from the log pytest runs append to")
    b.add_argument("--log", default=".specdeck/breaks.jsonl", help="breaks log (default .specdeck/breaks.jsonl)")
    b.add_argument("--format", choices=("text", "json"), default="text")
    b.add_argument("--all", action="store_true", help="every requirement, not only rules with a check or a break")
    _add_rules(sub)
    _add_watch(sub)
    _add_history(sub)
    args = ap.parse_args(argv)
    return _dispatch(args)


def _dispatch(args) -> int:
    if args.cmd == "serve":
        from .server import serve

        return serve(args.port, tuple(args.allow_host))
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "requirements":
        return cmd_requirements(args)
    if args.cmd == "trust":
        return cmd_trust(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "srs":
        return cmd_srs(args)
    if args.cmd == "agents":
        return cmd_agents(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "rules":
        return cmd_rules(args)
    if args.cmd == "breaks":
        return cmd_breaks(args)
    if args.cmd == "watch":
        from .watch import watch

        extra = [a for a in (args.pytest_args or []) if a != "--"]
        return watch(Path(args.root), extra, interval=args.interval, live=not args.no_live)
    if args.cmd in ("runs", "diff", "prune", "forget", "where"):
        return _history_cmd(args)
    return 2


# --------------------------------------------------------------------------- #
# validate / requirements
# --------------------------------------------------------------------------- #
def cmd_validate(args) -> int:
    from .validation import validate_file, validate_requirements_file

    spec = Path(args.spec)
    req = args.requirements
    if req is None and Path(DEFAULT_REQUIREMENTS).exists():
        req = DEFAULT_REQUIREMENTS
    results = []
    if not spec.exists():
        # Before the first test run there is no spec, but the requirements can already be checked:
        # a project starts by writing them. Only an explicitly named, missing spec is an error.
        if args.spec != DEFAULT_SPEC or not req:
            print(f"specdeck: no spec at {spec} — run pytest first (or pass a path)", file=sys.stderr)
            return 2
        print(f"specdeck: no spec at {spec} yet, so only {req} is checked; run pytest to check the tests "
              f"against it too", file=sys.stderr)
        spec = None
    else:
        results.append((str(spec), validate_file(spec)))
    if req:
        results.append((str(req), validate_requirements_file(req, spec=spec)))

    errors = sum(1 for _, ds in results for d in ds if d.severity == "error")
    warnings = sum(1 for _, ds in results for d in ds if d.severity == "warning")
    failed = errors > 0 or (args.strict and warnings > 0)

    if args.format == "json":
        print(json.dumps({"ok": not failed, "errors": errors, "warnings": warnings,
                          "files": [{"file": f, "diagnostics": [d.to_dict() for d in ds]} for f, ds in results]},
                         indent=2))
    else:
        for fname, ds in results:
            errs = sum(1 for d in ds if d.severity == "error")
            print(f"{fname}: {'OK' if not errs else 'FAIL'} ({errs} errors, {len(ds) - errs} warnings)")
            for d in ds:
                print(d.format())
        print(f"{'✖' if failed else '✔'} {errors} errors, {warnings} warnings across {len(results)} file(s)")
    return 1 if failed else 0


def cmd_requirements(args) -> int:
    path = Path(args.report)
    if not path.exists():
        print(f"specdeck: no report at {path} — run pytest first", file=sys.stderr)
        return 2
    rep = json.loads(path.read_text(encoding="utf-8"))
    if args.format == "json":
        print(json.dumps(rep, indent=2))
    else:
        c = rep["counts"]
        print(f"requirements: {c['met']} of {c['total']} met"
              + "".join(f", {c[k]} {k}" for k in ("failing", "blocked", "not-run", "untested") if c.get(k)))
        for r in rep["requirements"]:
            mark = {"met": "✔", "failing": "✖", "blocked": "⊘", "not-run": "·", "untested": "?"}.get(r["status"], "?")
            print(f"  {mark} {r['id']:<28} {r['status']:<9} {r.get('says', '')}")
            for s in r.get("scenarios", []):
                print(f"      {s['status']:<8} {s['id']}")
        for u in rep.get("unknown", []):
            print(f"  ! unknown requirement {u['id']!r} covered by {', '.join(u['scenarios'])}")
        for p in rep.get("problems", []):
            print(f"  ! {p}")
    if args.fail_on_unmet and rep["counts"]["met"] != rep["counts"]["total"]:
        return 1
    return 0


def cmd_agents(args) -> int:
    from .agents import block, write
    from .requirements import load

    if not Path(args.requirements).exists():
        print(f"specdeck: no requirements file at {args.requirements}", file=sys.stderr)
        return 2
    reqs = load(args.requirements)
    report = _load_json(args.report, "requirements report") if Path(args.report).exists() else None
    if report is None:
        print("specdeck: no requirements report yet — run pytest first for each rule's status (continuing)",
              file=sys.stderr)
    from .agents import targets

    outs = args.out or [str(p) for p in targets(Path("."))]
    if outs == ["-"]:
        print(block(reqs, report))
        return 0
    for out in outs:
        write(Path(out), reqs, report)
    print(f"specdeck: updated the rules in {', '.join(outs)}", file=sys.stderr)
    return 0


def cmd_check(args) -> int:
    import yaml

    from .checks import describe, run_all
    from .requirements import load

    if not Path(args.requirements).exists():
        print(f"specdeck: no requirements file at {args.requirements}", file=sys.stderr)
        return 2
    reqs = load(args.requirements)
    for prob in reqs.problems:
        print(f"specdeck: warning: {prob}", file=sys.stderr)
    tests = None
    if Path(args.spec).exists():
        tests = {}
        for sc in (yaml.safe_load(Path(args.spec).read_text(encoding="utf-8")) or {}).get("scenarios") or []:
            status = next((t[7:] for t in sc.get("tags") or [] if isinstance(t, str) and t.startswith("status:")), None)
            if status:
                tests[sc["id"]] = status
                for cmd in sc.get("logic") or []:
                    if isinstance(cmd, str) and cmd.startswith("pytest "):
                        tests[cmd[7:].strip()] = status
    results = run_all(reqs, Path("."), args.base, tests)
    if not results:
        print("specdeck: no requirement has a `check:`; see docs/RULE-CHECKS.md", file=sys.stderr)
    if args.format == "json":
        print(json.dumps({rid: r.to_dict() for rid, r in results.items()}, indent=2))
    else:
        mark = {"holds": "✔", "broken": "✖", "error": "!"}
        for rid, r in results.items():
            state = "n/a" if not r.applicable else r.state
            print(f"{mark.get(r.state, '?') if r.applicable else '·'} {rid:<28} {state:<7} "
                  f"{describe(reqs.items[rid].check)}")
            for e in r.evidence[:5]:
                print(f"      {e}")
            if r.error:
                print(f"      {r.error}")
    return 1 if args.fail and any(r.state == "broken" for r in results.values()) else 0


def cmd_rules(args) -> int:
    import yaml

    from . import registry as _r
    from .requirements import load

    try:
        if args.rules_cmd == "init":
            print(f"specdeck: registry ready at {_r.init(Path(args.path))}", file=sys.stderr)
            return 0
        if not args.registry:
            print("specdeck: say which registry: --registry DIR_OR_GIT_URL, or set SPECDECK_REGISTRY", file=sys.stderr)
            return 2
        if args.rules_cmd == "publish":
            reqs = load(args.requirements)
            req = reqs.items.get(args.id)
            if req is None:
                print(f"specdeck: no requirement {args.id} in {args.requirements}", file=sys.stderr)
                return 2
            out = _r.publish(Path(args.registry), _r.card_from(req.to_dict()), args.message)
            print(f"specdeck: published {args.id} to {out}; push the registry to share it", file=sys.stderr)
            return 0
        reg = _r.open_registry(args.registry)
        if args.rules_cmd == "search":
            found = reg.search(" ".join(args.query))
            for card in found:
                print(_r.summary(card))
            print(f"specdeck: {len(found)} rule(s) in {reg.spec}" + (f" @ {reg.revision}" if reg.revision else ""),
                  file=sys.stderr)
            return 0
        card = reg.card(args.id)
        if args.rules_cmd == "show":
            print(yaml.safe_dump(card, sort_keys=False, allow_unicode=True, width=100), end="")
            return 0
        source = f"{reg.spec}@{reg.revision}" if reg.revision else reg.spec
        entry = _r.add(Path(args.requirements), card, source)
        print(entry)
        print(f"specdeck: added {args.id} to {args.requirements} from {source}. Its text goes into the agents' "
              f"instructions: read it before committing. Run pytest to check it.", file=sys.stderr)
        return 0
    except _r.RegistryError as exc:
        print(f"specdeck: {exc}", file=sys.stderr)
        return 2


def cmd_breaks(args) -> int:
    from .checks import summarize

    rows = summarize(Path(args.log))
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(f"specdeck: no runs logged at {args.log} yet; pytest appends one per run once a requirement "
              f"has a `check:`", file=sys.stderr)
        return 0
    shown = rows if args.all else [s for s in rows if s["checked"] or s["broken"]]
    print(f"{'rule':<28} {'applied':>7} {'broken':>6} {'rate':>6} {'commits':>7}  last broken")
    for s in shown:
        rate = f"{s['rate']:.0%}" if s["rate"] is not None else "—"
        last = f"{s['last_broken']['ts']} {s['last_broken']['sha']}" if s["last_broken"] else "never"
        print(f"{s['id']:<28} {s['applied']:>7} {s['broken']:>6} {rate:>6} {s['commits']:>7}  {last}")
        for e in s["last_evidence"][:2] if s["last_broken"] else []:
            print(f"{'':<30}{e}")
    if len(shown) < len(rows):
        print(f"({len(rows) - len(shown)} more requirements without a check never broke; --all lists them)")
    return 0


def cmd_srs(args) -> int:
    import yaml

    from .requirements import load
    from .srs import build

    reqs = load(args.requirements)
    if not reqs.items:
        print(f"specdeck: no requirements in {args.requirements}", file=sys.stderr)
        return 2
    report = _load_json(args.report, "requirements report") if Path(args.report).exists() else None
    if report is None:
        print("specdeck: no requirements report yet — run pytest first for verification status (continuing)",
              file=sys.stderr)
    spec = Path(args.spec)
    scenarios = (yaml.safe_load(spec.read_text(encoding="utf-8")) or {}).get("scenarios") if spec.exists() else None
    text = build(reqs, report, scenarios, reqs.srs, Path.cwd())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"specdeck: wrote {args.out}", file=sys.stderr)
    else:
        print(text, end="")
    return 0


def cmd_context(args) -> int:
    from . import profiles as _profiles
    from .context import build, load, load_requirements

    if args.profiles:
        for name, prof in _profiles.PROFILES.items():
            mark = "  (default)" if name == _profiles.DEFAULT else ""
            print(f"{name}{mark}\n    {prof.summary}\n    Measured: {prof.measured}")
        print("\nOr say what will be asked and let specdeck choose:")
        for task, (name, why) in _profiles.FOR_TASK.items():
            print(f"  --task {task:8} -> {name:9} {why}")
        return 0
    if args.task and args.profile:
        print("specdeck: give --task or --profile, not both", file=sys.stderr)
        return 2
    try:
        prof = _profiles.for_task(args.task) if args.task else _profiles.get(args.profile)
    except KeyError as exc:
        print(f"specdeck: {exc}", file=sys.stderr)
        return 2

    if not Path(args.spec).exists():
        print(f"specdeck: no spec at {args.spec} — run pytest first", file=sys.stderr)
        return 2
    spec, cov = load(args.spec, args.coverage)
    focus = [f.strip() for f in (args.focus or "").split(",") if f.strip()]
    if focus and not cov:
        print("specdeck: --for needs a coverage map; run pytest --specdeck-coverage first", file=sys.stderr)
        return 2
    if prof.needs_coverage and not cov:
        print(f"specdeck: profile {prof.name!r} wants a coverage map; run pytest --specdeck-coverage "
              f"for it (continuing without)", file=sys.stderr)
    reqs = load_requirements(args.requirements_report or Path(args.spec).parent / "requirements.json")
    if prof.requirements and not reqs and not prof.index:
        print("specdeck: no requirements report; run pytest with a requirements.yaml first", file=sys.stderr)
        return 2
    text = build(spec, cov, focus=focus, profile=prof.name, root=str(Path(args.spec).resolve().parent.parent),
                 requirements=reqs)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        note = "" if cov else "  (no coverage map; run pytest --specdeck-coverage to add one)"
        print(f"specdeck: wrote {args.out}{note}", file=sys.stderr)
    else:
        print(text, end="")
    return 0


def _load_json(path: str, what: str):
    if path == "-":
        return json.load(sys.stdin)
    p = Path(path)
    if not p.exists():
        print(f"specdeck: no {what} at {p}", file=sys.stderr)
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _load_spec(path: str):
    import yaml

    p = Path(path)
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def cmd_trust(args) -> int:
    from .trust import join, render_text

    proof = _load_json(args.proofdeck, "proofdeck report")
    reqs = _load_json(args.report, "requirements report — run pytest first")
    if proof is None or reqs is None:
        return 2
    result = join(reqs, proof, spec=_load_spec(args.spec))
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(render_text(result))
    return 1 if args.fail_on_untrusted and result["counts"]["untrusted"] else 0


def cmd_report(args) -> int:
    from .ci import build, exit_code, render_markdown

    spec = _load_spec(args.spec)
    if spec is None:
        print(f"specdeck: no spec at {args.spec} — run pytest first", file=sys.stderr)
        return 2
    reqs = json.loads(Path(args.requirements_report).read_text(encoding="utf-8")) \
        if Path(args.requirements_report).exists() else None
    proof = None
    if args.proofdeck:
        proof = _load_json(args.proofdeck, "proofdeck report")
        if proof is None:
            return 2
    model = build(spec, reqs, proof)
    text = json.dumps(model, indent=2) if args.format == "json" else render_markdown(model)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"specdeck: wrote {args.out}", file=sys.stderr)
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return exit_code(model, fail_on_failing=args.fail_on_failing, fail_on_unmet=args.fail_on_unmet,
                     fail_on_untrusted=args.fail_on_untrusted)


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #
def _when(started) -> str:
    """Runs store epoch seconds; show them as a local timestamp."""
    import datetime as dt

    try:
        return dt.datetime.fromtimestamp(float(started)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(started)[:19]


def _resolve_project(h, slug):
    if slug:
        p = h.project(slug)
        if not p:
            print(f"specdeck: no project {slug!r} in history", file=sys.stderr)
        return p
    root = str(Path.cwd().resolve())
    for p in h.projects():
        if p.get("root") == root:
            return p
    projects = h.projects()
    if len(projects) == 1:
        return projects[0]
    print("specdeck: which project? pass --project; known: "
          + ", ".join(p["slug"] for p in projects), file=sys.stderr)
    return None


def _history_cmd(args) -> int:
    from .history import History, db_path

    if args.cmd == "where":
        print(db_path())
        return 0
    with History() as h:
        if args.cmd == "prune":
            pid = None
            if args.project:
                p = _resolve_project(h, args.project)
                if not p:
                    return 2
                pid = p["id"]
            res = h.prune(keep_runs=args.keep, project_id=pid)
            print(f"specdeck: removed {res['removed_runs']} run(s) across {res['projects']} project(s) "
                  f"from {res['db']}")
            return 0
        if args.cmd == "forget":
            ok = h.forget(args.slug)
            print(f"specdeck: {'removed ' + args.slug if ok else 'no project ' + args.slug}")
            return 0 if ok else 2

        p = _resolve_project(h, args.project)
        if not p:
            return 2
        if args.cmd == "runs":
            rows = h.runs(p["id"], limit=args.limit)
            if args.format == "json":
                print(json.dumps(rows, indent=2, default=str))
            else:
                print(f"{p['slug']}: {len(rows)} most recent run(s)")
                for r in rows:
                    flags = "" if r.get("full") else " (partial)"
                    print(f"  #{r.get('n'):<5} {str(r.get('status')):<10} {_when(r.get('started'))}"
                          f"  {r.get('trigger', '')}{flags}"
                          + (f"  fixed {len(r['fixed'])}" if r.get("fixed") else "")
                          + (f"  regressed {len(r['regressed'])}" if r.get("regressed") else ""))
            return 0
        if args.cmd == "diff":
            left = _run_id_for(h, p, args.left)
            right = _run_id_for(h, p, args.right)
            if left is None or right is None:
                return 2
            res = h.compare(p["id"], left, right)
            if args.format == "json":
                print(json.dumps(res, indent=2, default=str))
            else:
                print(f"run #{res['left']['n']} -> #{res['right']['n']}")
                for key, label in (("fixed", "fixed"), ("regressed", "regressed"), ("new", "new"),
                                   ("gone", "gone"), ("still_failing", "still failing")):
                    items = res[key]
                    if not items:
                        continue
                    print(f"  {label} ({len(items)}):")
                    for it in items:
                        extra = f" (was {it['from']})" if it.get("from") else ""
                        print(f"    {it['id']}{extra}")
                print(f"  unchanged: {res['counts']['unchanged']}")
            return 0
    return 2


def _run_id_for(h, project, n: int):
    """Accept the run *number* shown in the UI and terminal."""
    for r in h.runs(project["id"], limit=1000):
        if r.get("n") == n or r["id"] == n:
            return r["id"]
    print(f"specdeck: no run #{n} in {project['slug']}", file=sys.stderr)
    return None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
