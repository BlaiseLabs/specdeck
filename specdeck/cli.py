"""The specdeck command line.

    specdeck serve         run the viewer
    specdeck validate      lint the generated spec and the requirements file (CI gate)
    specdeck requirements  print the requirements report
    specdeck trust         join the requirements with a proofdeck report: met, or just passing?
    specdeck report        Markdown for a PR comment: scenarios, fix-first, requirements, proof
    specdeck context       one document describing the suite, for a coding agent
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
    results = []
    if not spec.exists():
        print(f"specdeck: no spec at {spec} — run pytest first (or pass a path)", file=sys.stderr)
        return 2
    results.append((str(spec), validate_file(spec)))

    req = args.requirements
    if req is None and Path(DEFAULT_REQUIREMENTS).exists():
        req = DEFAULT_REQUIREMENTS
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
