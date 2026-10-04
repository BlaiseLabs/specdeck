"""`proofdeck check [--base main] [--format markdown|json] [--no-gate]`."""
from __future__ import annotations

import argparse
import json
import sys

from .analyze import analyze
from .git import GitError
from .report import exit_code, render_json, render_markdown


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="proofdeck", description="Would your tests notice if the changed code were wrong?")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="analyse the working tree against a base branch")
    c.add_argument("--base", default="main", help="branch or commit to compare against (default: main)")
    c.add_argument("--repo", default=".", help="repository to analyse (default: current directory)")
    c.add_argument("--format", choices=("markdown", "json"), default="markdown")
    c.add_argument("--no-gate", action="store_true", help="always exit 0; report only")
    c.add_argument("--test-command", default=None, help="how to run the tests (default: python -m pytest)")
    c.add_argument("--coverage", default=None, metavar="FILE",
                   help="coverage.py data with per-test contexts (pytest --cov-context=test) to use instead "
                        "of tracing the tests again")
    c.add_argument("--mode", choices=("auto", "fork", "subprocess"), default=None,
                   help="fork: one pytest process, a forked child per mutant (default where available); "
                        "subprocess: a pytest process per mutant")
    c.add_argument("--quiet", action="store_true", help="no progress on stderr")
    args = ap.parse_args(argv)

    progress = None if args.quiet else (lambda msg: print(f"proofdeck: {msg}", file=sys.stderr, flush=True))
    try:
        report = analyze(args.repo, base=args.base, test_command=args.test_command, progress=progress,
                         coverage=args.coverage, mode=args.mode)
    except (GitError, RuntimeError) as exc:
        print(f"proofdeck: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(render_json(report), indent=2))
    else:
        print(render_markdown(report), end="")
    return exit_code(report, gate=not args.no_gate)


if __name__ == "__main__":
    sys.exit(main())
