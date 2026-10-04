"""Build `touches:` for every scenario: the source functions its test actually executes.

The A/B's failure analysis said the spec's gap is that it never names source code, so an
agent cannot connect "line N of file F changed" to any scenario. This is measured, not
guessed: the suite is traced once and each test's executed lines are mapped to the
function (or module top level) they fall in. specdeck could emit this from the same
trace; here it is produced out-of-tree so the experiment doesn't change the tool first.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, "/workspaces/python/proofdeck")
from proofdeck.runner import Workspace  # noqa: E402

REPO = Path("/workspaces/python/tapedeck")
OUT = Path(__file__).parent / "data" / "touches.json"


def owners(path: Path) -> list:
    """[(start, end, qualified_name)] for every function in the file, innermost last."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                out.append((child.lineno, child.end_lineno, name))
                walk(child, prefix=f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")

    walk(tree)
    return out


def main() -> None:
    targets = sorted(str(p.relative_to(REPO)) for p in (REPO / "tapedeck").rglob("*.py")
                     if "__pycache__" not in p.parts)
    with Workspace(REPO, workers=1) as ws:
        cov = ws.trace(targets)
    # Lines executed while the modules were imported are attributed to every test and say
    # nothing about which test exercises what — the same trap the trust join hit.
    at_import = cov.at_import
    by_file = {t: owners(REPO / t) for t in targets}
    per_test: dict = {}
    for (rel, line), nodeids in cov.by_line.items():
        if rel not in by_file or (rel, line) in at_import:
            continue
        hits = [name for start, end, name in by_file[rel] if start <= line <= end]
        where = f"{rel}:{hits[-1]}" if hits else f"{rel} (module level)"
        for n in nodeids:
            per_test.setdefault(n, {}).setdefault(where, 0)
            per_test[n][where] += 1
    # rank by how much of the function the test executed, so the list can be trimmed
    out = {n: [w for w, _ in sorted(d.items(), key=lambda kv: -kv[1])] for n, d in per_test.items()}
    OUT.write_text(json.dumps(out, indent=1))
    sizes = sorted(len(v) for v in out.values())
    print(f"{len(out)} tests; functions touched: median {sizes[len(sizes)//2]}, max {sizes[-1]}")


if __name__ == "__main__":
    main()
