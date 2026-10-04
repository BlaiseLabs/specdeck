"""Re-grade every stored suite with the fixed grader, and establish what is actually killable.

Two corrections over the first pass:
  * pruning now repeats until the kept subset is really green (one pass was not enough for a
    module that patches global state, which scored a spurious 0)
  * each trial records WHICH mutants died, so mutants nobody can kill — semantically
    equivalent to the original — can be excluded instead of inflating the denominator
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import grader
from proofdeck.mutate import apply as apply_mutant

rows = [json.loads(l) for l in open("trials.jsonl")]
modules = sorted({r["module"] for r in rows})

# the real hand-written suite, through the same instrument
human = {}
for module in modules:
    tree = grader.sandbox()
    try:
        original = (tree / module).read_text()
        kids = set()
        for line_no, m in grader.mutable_lines(module, cap=30):
            (tree / module).write_text(apply_mutant(original, m))
            p, f, e, _ = grader._pytest(tree, "tests", timeout=300)
            if f or e:
                kids.add(line_no)
            (tree / module).write_text(original)
        human[module] = sorted(kids)
        print(f"human suite {module.split('/')[-1]:16} killed {len(kids)}", flush=True)
    finally:
        shutil.rmtree(tree.parent, ignore_errors=True)

out = []
for i, r in enumerate(rows, 1):
    if not r.get("code"):
        out.append(r); continue
    g = grader.grade(r["code"], r["module"])
    out.append({**{k: v for k, v in r.items() if k != "tail"}, **g})
    print(f"  {i}/{len(rows)} {r['arm']:10} {r['module'].split('/')[-1]:14} "
          f"caught {g.get('caught',0):.0%} valid {g.get('validity',0):.0%}", flush=True)

killable = {}
for module in modules:
    union = set(human.get(module, []))
    for r in out:
        if r["module"] == module:
            union |= set(r.get("killed_ids") or [])
    killable[module] = sorted(union)
    print(f"killable in {module.split('/')[-1]:16} {len(union)} of {r.get('mutants', 0)}")

for r in out:
    k = killable.get(r["module"], [])
    if k and r.get("killed_ids") is not None:
        r["caught_of_killable"] = round(len(set(r["killed_ids"]) & set(k)) / len(k), 3)
Path("regraded.jsonl").write_text("\n".join(json.dumps(r) for r in out) + "\n")
Path("baseline.json").write_text(json.dumps({"human_killed": human, "killable": killable}, indent=1))
print("wrote regraded.jsonl and baseline.json")
