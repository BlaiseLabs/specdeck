"""Can terra say WHAT to test and a cheap model write HOW?

terra writes, per function, a terse case list: the inputs and the exact expected result (the
oracle, which only the strong model can supply). A cheap model expands each list into pytest
code: objects, fakes, the call, the asserts. Checked three ways against the round-2 programs:

1. calibration: the expanded tests must pass on terra's reference (one fix-up retry, then any
   still-failing test is dropped and counted);
2. strength: proofdeck plants one-line bugs (mutants) in each reference function; count how many
   the new tests catch, against terra's original tests on the same mutants;
3. agreement: the new tests must accept and reject the same worker functions as the originals.

    python3 casetests.py cases  <prog>              terra: case lists
    python3 casetests.py expand <prog> <model>      cheap model: pytest code, calibrated
    python3 casetests.py score  <prog> <model>      strength + agreement
"""
from __future__ import annotations

import ast
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, "/workspaces/python/proofdeck")
import designed  # noqa: E402
from designed import STRONG, STYLE, call, callees, code_block, load, pdir, run_tests, save, shared  # noqa: E402
from proofdeck.mutate import apply, mutants_for_line  # noqa: E402

MUTANTS_PER_FN = 12


def cases(prog: str) -> None:
    d = load(prog, "design.json")
    out = load(prog, "cases.json") if (pdir(prog) / "cases.json").exists() else {}

    def one(f):
        if f["name"] in out:
            return
        reply = call(STRONG, f"""List the test cases for `{f['name']}`. Another model will turn your list
into pytest code; it will not think about the expected results, so every expected value must be
exact and correct. Cover the spec's examples, each rule, and its edge cases: 4-8 cases.
Be terse: give inputs as exact values (constructor-like notation for domain objects, and the
state of any fake ability), and the exact expected result or exception.

{STYLE}

{shared(d)}
=== app/fakes.py ===
{d['fakes_py']}

=== functions it may call ===
{callees(d, f)}

=== {f['signature']} ({f['kind']}) ===
{f['spec']}

Reply with JSON: {{"cases": [{{"name": "snake_case", "given": str, "expect": str}}]}}""",
                     f"{prog}:cases:{f['name']}", json_mode=True)
        out[f["name"]] = json.loads(reply)["cases"]

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, d["functions"]))
    save(prog, "cases.json", out)
    print(f"{sum(len(v) for v in out.values())} cases for {len(out)} functions")


def expand_prompt(d, f, case_list) -> str:
    if f["kind"] == "service":
        how = f"""`{f['name']}` is a service (an effect). Run it like this, supplying the fake abilities:
    from stateless import run, supply
    repo = InMemoryX(...)                      # the fake from app/fakes.py, set up as the case says
    result = run(supply(repo)({f['name']})(arg1, arg2))
A domain error surfaces as the raised exception: `with pytest.raises(SomeError): run(...)`."""
    else:
        how = f"""`{f['name']}` is a plain pure function: call it directly with positional arguments in
signature order, e.g. `result = {f['name']}(...)`. Never use run/supply for it."""
    return f"""Write pytest tests for `{f['name']}`: exactly one test function per case below, named
test_<case name>. Use exactly the given inputs and expected results. Do not compute or change an
expected value, and do not add cases.

{shared(d)}
=== app/fakes.py ===
{d['fakes_py']}

=== the function under test ===
{f['signature']}
{f['contract']}

{how}

=== cases ===
{json.dumps(case_list, indent=1, ensure_ascii=False)}

Start the file with: `import pytest`, `import dataclasses`, `from decimal import Decimal`,
`from datetime import date, datetime as dt, timedelta`, `from app.domain import *`,
`from app.abilities import *`, `from app.fakes import *`, `from app.logic import {f['name']}`.
Return one ```python block."""


def failing(out: str) -> list:
    import re
    return sorted(set(re.findall(r"FAILED test_it\.py::(\w+)", out) + re.findall(r"ERROR test_it\.py::(\w+)", out)))


def drop_tests(src: str, names: list) -> str:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    cut = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
            first = min([x.lineno for x in n.decorator_list] + [n.lineno])
            cut.update(range(first, n.end_lineno + 1))
    return "".join(l for i, l in enumerate(lines, 1) if i not in cut)


def expand(prog: str, model: str) -> None:
    d = load(prog, "design.json")
    cs = load(prog, "cases.json")
    ref = load(prog, "ref.json")["impl"]
    label = f"casetests2_{model.replace(':', '_')}"
    res = load(prog, f"{label}.json") if (pdir(prog) / f"{label}.json").exists() else {}

    def one(f):
        n = f["name"]
        if n in res:
            return
        p = expand_prompt(d, f, cs[n])
        rec = {"cases": len(cs[n])}
        src = None
        for k in range(2):
            src = code_block(call(model, p, f"{prog}:{label}:{n}"))
            try:
                ast.parse(src)
            except SyntaxError as e:
                out, ok = f"SyntaxError: {e}", False
            else:
                ok, npass, nfail, out = run_tests(d, ref, src)
            rec[f"try{k}"] = {"ok": ok, "out": out[-1200:]}
            if ok:
                break
            p += f"\n\n=== your tests ===\n```python\n{src}\n```\n=== they fail against a correct implementation ===\n{out[-1500:]}\n" \
                 "Fix the test code. The expected values come from the cases and must not change."
        bad = []
        if not rec[f"try{k}"]["ok"]:
            try:
                ast.parse(src)
                bad = failing(rec[f"try{k}"]["out"])
                src2 = drop_tests(src, bad)
                ok2 = run_tests(d, ref, src2)[0]
                src = src2 if ok2 else None
            except SyntaxError:
                src = None
        tree_tests = [x.name for x in ast.parse(src).body if isinstance(x, ast.FunctionDef) and x.name.startswith("test")] if src else []
        rec.update(src=src, dropped=bad, kept=len(tree_tests), first_ok=rec["try0"]["ok"])
        res[n] = rec
        print(f"{label:22} {n:30} cases {len(cs[n])} kept {len(tree_tests)} dropped {len(bad)} first_ok {rec['first_ok']}"
              + ("" if src else "  UNUSABLE"), flush=True)
        save(prog, f"{label}.json", res)

    if model.startswith("qwen"):
        for f in d["functions"]:
            one(f)
    else:
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(one, d["functions"]))


def fn_lines(src: str, name: str) -> range:
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return range(n.body[0].lineno, n.end_lineno + 1)
    return range(0)


def score(prog: str, model: str) -> None:
    from combine import rows
    d = load(prog, "design.json")
    orig = load(prog, "tests.json")
    ref = load(prog, "ref.json")["impl"]
    label = f"casetests2_{model.replace(':', '_')}"
    new = load(prog, f"{label}.json")
    logic = designed.assemble(d, ref)
    rnd = random.Random(1)
    report = {}

    def one(f):
        n = f["name"]
        muts = []
        for line in fn_lines(logic, n):
            muts += [(line, m) for m in mutants_for_line(logic, line, "logic.py")]
        rnd.shuffle(muts)
        muts = muts[:MUTANTS_PER_FN]
        new_src = new.get(n, {}).get("src")
        k_orig = k_new = 0
        usable = []
        for _, m in muts:
            try:
                mutated = apply(logic, m)
                ast.parse(mutated)
            except Exception:  # noqa: BLE001
                continue
            usable.append(m)
            k_orig += not run_logic(d, mutated, orig[n])
            if new_src:
                k_new += not run_logic(d, mutated, new_src)
        report[n] = {"mutants": len(usable), "killed_orig": k_orig, "killed_new": k_new if new_src else None}

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, d["functions"]))

    agree = {}
    for worker in ["gpt-4o-mini", "qwen3_8b"]:
        try:
            R = rows(prog, worker)
        except FileNotFoundError:
            continue
        for n, (r, src) in R.items():
            new_src = new.get(n, {}).get("src")
            if not new_src or not src:
                continue
            ok_new = run_tests(d, {**ref, n: src}, new_src)[0]
            agree.setdefault(worker, []).append((n, r["ok"], ok_new))
    save(prog, f"{label}_score.json", {"mutation": report, "agreement": agree})
    m = report.values()
    tot = sum(r["mutants"] for r in m)
    have = [r for r in m if r["killed_new"] is not None]
    print(f"{prog} {label}: mutants {tot}; caught by original tests {sum(r['killed_orig'] for r in m)}, "
          f"by new tests {sum(r['killed_new'] for r in have)} (on {sum(r['mutants'] for r in have)} with usable new tests; "
          f"originals on those {sum(r['killed_orig'] for r in have)})")
    for w, rows_ in agree.items():
        same = sum(a == b for _, a, b in rows_)
        diff = [(n, a, b) for n, a, b in rows_ if a != b]
        print(f"  agreement on {w}'s functions: {same}/{len(rows_)}; differ: {diff}")


def run_logic(d, logic_src: str, test_src: str) -> bool:
    """run_tests with a ready-made logic.py."""
    impl = {}
    tree = ast.parse(logic_src)
    lines = logic_src.splitlines(keepends=True)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            impl[n.name] = "".join(lines[n.lineno - 1:n.end_lineno])
    return run_tests(d, impl, test_src)[0]


if __name__ == "__main__":
    cmd, prog = sys.argv[1], sys.argv[2]
    {"cases": lambda: cases(prog), "expand": lambda: expand(prog, sys.argv[3]),
     "score": lambda: score(prog, sys.argv[3])}[cmd]()
