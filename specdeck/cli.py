"""The specdeck command line.

    specdeck serve         run the viewer
    specdeck validate      lint the generated spec and the requirements file (CI gate)
    specdeck requirements  print the requirements report
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
        results.append((str(req), validate_requirements_file(req)))

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
