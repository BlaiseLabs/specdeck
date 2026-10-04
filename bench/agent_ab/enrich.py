"""spec_v2: the generated spec plus a coverage map — the one thing the A/B said it lacked.

The failure analysis of round 1 was unambiguous: the spec never names a single piece of
source code, so when asked "line N of file F changed, which tests fail?" the agent reading
it has no bridge from the file to any scenario. The test files at least show their imports.

The map is mechanical (specdeck can emit it from a trace it already knows how to take) and
it is deliberately NOT the answer: predicting failures from coverage alone scores 0.57 F1,
because running a line is not noticing it changed. Scenarios are numbered so each reference
costs one token; the whole map is ~3.1k tokens, keeping this arm cheaper than the test files.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

HERE = Path(__file__).parent
SPEC = Path("/workspaces/python/tapedeck/.specdeck/spec.yaml")
OUT = HERE / "data" / "spec_v2.yaml"

PREAMBLE = """
# ---------------------------------------------------------------------------
# coverage map: which scenarios EXECUTE each source function.
#
# Recorded by running the suite under a line tracer. `n:` on each scenario above
# is its number here. Lines that run when a module is imported are excluded —
# every test would "touch" them, which says nothing about any one test.
#
# Executing a function is not the same as checking it: a scenario listed here
# runs that code, but only fails if one of its Then steps actually disagrees.
# ---------------------------------------------------------------------------
coverage:
"""


def main() -> None:
    spec = yaml.safe_load(SPEC.read_text())
    touches = json.loads((HERE / "data" / "touches.json").read_text())
    node2i = {}
    for i, sc in enumerate(spec["scenarios"], 1):
        sc["n"] = i
        node2i[sc["logic"][0][len("pytest "):]] = i

    inv: dict = {}
    for node, places in touches.items():
        i = node2i.get(node)
        if i is None:
            continue
        for place in places:
            file, _, fn = place.partition(":")
            file = file.split(" ")[0]
            inv.setdefault(file, {}).setdefault(fn or "(module level)", []).append(i)

    body = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)
    lines = [PREAMBLE.rstrip("\n")]
    for file in sorted(inv):
        lines.append(f"  {file}:")
        for fn in sorted(inv[file]):
            lines.append(f"    {fn}: [{','.join(str(x) for x in sorted(inv[file][fn]))}]")
    OUT.write_text(body + "\n".join(lines) + "\n")
    print(f"wrote {OUT} ({len(inv)} files, {sum(len(v) for v in inv.values())} functions)")


if __name__ == "__main__":
    main()
