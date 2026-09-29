"""``specdeck serve`` runs the viewer; ``specdeck where`` prints where history lives."""
from __future__ import annotations

import argparse
import os
import sys

from .recorder import DEFAULT_PORT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="specdeck", description="Live viewer for specdeck test runs.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the viewer on 127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.environ.get("SPECDECK_PORT") or DEFAULT_PORT))
    s.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="extra Host header name to accept (e.g. a tunnel hostname); the bind stays 127.0.0.1")
    sub.add_parser("where", help="print the history database path")
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from .server import serve

        return serve(args.port, tuple(args.allow_host))
    from .history import db_path

    print(db_path())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
