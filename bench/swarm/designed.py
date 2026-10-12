"""Swarm check, designed for it: can a small model write a function from a succinct spec alone?

The first check (swarm.py) blanked functions inside existing code, so every worker needed the
whole file. Here the strong model designs a NEW program for the swarm, in the `stateless`
effects style (pure functions over frozen data; side effects only through declared
abilities), so each function's dependencies are explicit. A worker sees only:

    domain.py (the data), abilities.py (the interfaces), its own spec (inputs, output, logic,
    examples), and one-line contracts of any functions it may call.

    python3 designed.py design  <prog>      terra: domain, abilities, fakes, specs
    python3 designed.py tests   <prog>      terra: hidden unit tests per function + acceptance
    python3 designed.py ref     <prog>      terra implements every function from its spec (ceiling)
    python3 designed.py work    <prog> <model>
    python3 designed.py report  <prog>
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/workspaces/python")
LEDGER = HERE / "ledger.jsonl"
STRONG = "gpt-5.6-terra"
CAP = int(os.environ.get("DESIGNED_CAP", "1200000"))   # OpenAI tokens in ledger.jsonl, all swarm runs

BRIEFS = {
    "srs": """A spaced-repetition review scheduler for flashcards (like Anki). Notes have one or
more cards (siblings). Each card has a state (new / learning / review / suspended), an ease factor,
an interval in days, a due date, a lapse count and a review history. Needed behaviour: grade a
review (again / hard / good / easy) and compute the next state, interval, ease and due date with
SM-2-style rules and learning steps; detect leeches (too many lapses) and suspend them; bury a
card's siblings for the rest of the day after it is reviewed; build today's study queue with a
daily new-card limit and review limit, overdue reviews first ordered by how overdue they are
relative to their interval; summarise a day's study (counts per grade, retention rate, time
spent); compute the current study streak in days from the review log.""",
    "ledger": """A shared-expenses ledger for a group trip (like Splitwise). Members pay for expenses
in different currencies; an expense is split equally, by exact amounts, by percentages, or by
shares, among a subset of members. Needed behaviour: validate an expense and its split
(amounts must add up, percentages to 100, no unknown members); convert to the group's base
currency with given exchange rates and round to cents without losing or creating money (the
remainder cents go to deterministic members); compute each member's balance; settle up with
the minimum number of transfers; record a payment between two members; produce a per-member
statement; undo the last action; reject edits to a closed trip.""",
}

STYLE = """Style (the `stateless` effects library, already imported in logic.py):
- A pure function takes data and returns data. No I/O, no globals, no mutation of inputs
  (frozen dataclasses: use dataclasses.replace).
- A service function is a generator that gets abilities with `x = yield from need(AbilityClass)`
  and calls their methods. To fail with a domain error: `return (yield from throw(SomeError(...)))`.
  It may call pure functions directly, and other services with `result = yield from other(...)`.
- Already imported at the top of logic.py: `from app.domain import *`, `from app.abilities import *`,
  `from stateless import need, throw`, `import dataclasses, math, datetime`,
  `from datetime import date, datetime as dt, timedelta`, `from collections import Counter, defaultdict`.
  Any other import goes inside the function body."""

HEADER = """from __future__ import annotations
import dataclasses, math, datetime
from datetime import date, datetime as dt, timedelta
from collections import Counter, defaultdict
from stateless import need, throw
from app.domain import *
from app.abilities import *
"""


# The tests were told logic.py has these imports and some leaned on them; give tests the same.
TEST_PRELUDE = "import dataclasses, math, datetime\nfrom datetime import date, datetime as dt, timedelta\n"


def pdir(prog: str) -> Path:
    d = HERE / "designed" / prog
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(prog: str, name: str):
    return json.loads((pdir(prog) / name).read_text())


def save(prog: str, name: str, obj) -> None:
    (pdir(prog) / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False))


# ---- models ---------------------------------------------------------------------------------

def spent() -> int:
    if not LEDGER.exists():
        return 0
    return sum(r["total"] for r in map(json.loads, LEDGER.read_text().splitlines()) if not r["model"].startswith("qwen"))


def call(model: str, text: str, tag: str, json_mode: bool = False) -> str:
    t0 = time.time()
    if model.startswith("qwen"):
        body = {"model": model, "prompt": text, "stream": False, "think": False,
                "options": {"num_predict": 4096, "temperature": 0, "num_ctx": 16384}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(body, fh)
        remote, out = f"/tmp/dsw_req_{os.getpid()}.json", f"/tmp/dsw_res_{os.getpid()}.json"
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", fh.name, f"macmini-tunnel:{remote}"], check=True, timeout=120)
        subprocess.run(["ssh", "-o", "BatchMode=yes", "macmini-tunnel",
                        f"curl -s --max-time 1500 http://127.0.0.1:11434/api/generate -H 'Content-Type: application/json' "
                        f"--data-binary @{remote} -o {out}"], check=True, timeout=1600)
        d = json.loads(subprocess.run(["ssh", "-o", "BatchMode=yes", "macmini-tunnel", f"cat {out}"], check=True,
                                      capture_output=True, text=True, timeout=120).stdout)
        rec = {"prompt": d.get("prompt_eval_count", 0), "completion": d.get("eval_count", 0)}
        reply = d.get("response", "")
    else:
        if spent() >= CAP:
            raise SystemExit(f"token cap {CAP} reached")
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv(ROOT / ".env")
        kw = {"response_format": {"type": "json_object"}} if json_mode else {}
        r = OpenAI().chat.completions.create(model=model, messages=[{"role": "user", "content": text}], **kw)
        u = r.usage
        rec = {"prompt": u.prompt_tokens, "completion": u.completion_tokens}
        reply = r.choices[0].message.content or ""
    rec.update(model=model, tag=tag, total=rec["prompt"] + rec["completion"], seconds=round(time.time() - t0, 1))
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return reply


def code_block(reply: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", reply, re.S)
    return max(blocks, key=len) if blocks else reply


# ---- phases ---------------------------------------------------------------------------------

def design(prog: str) -> None:
    reply = call(STRONG, f"""You are the architect. Design a small Python program for the brief below as a set
of functions that separate, weaker workers will implement independently and in parallel.
Each worker sees ONLY: domain.py, abilities.py, its own function spec, and the one-line
contracts of the functions it is allowed to call. It never sees any other code.

{STYLE}

Requirements:
- 16 to 22 functions. At least two thirds are pure. Each body should need at most ~25 lines.
- A spec must be self-contained: exact input types and shapes, the output, the logic as
  numbered rules including edge cases and tie-breaks, and 2-3 concrete examples (input -> output).
  Anything a worker would have to guess is a defect in your spec.
- domain.py: frozen dataclasses, enums, constants and exception classes only.
- abilities.py: `from stateless import Ability`; each ability is `class X(Ability):` with typed
  method stubs that `raise NotImplementedError`. Import types from `app.domain`.
- fakes.py: in-memory implementations (`class InMemoryX(X)`) of every ability for tests,
  importing from `app.domain` and `app.abilities`.
- `calls` lists the other functions in this design that a function may call; keep it short.
- `contract` is one line: signature and what it returns.

Brief:
{BRIEFS[prog]}

Reply with JSON: {{"domain_py": str, "abilities_py": str, "fakes_py": str,
"functions": [{{"name": str, "kind": "pure"|"service", "signature": str, "contract": str,
"spec": str, "calls": [str]}}], "acceptance": [str]}}  where acceptance lists 4-6 end-to-end
scenarios in prose.""", f"{prog}:design", json_mode=True)
    d = json.loads(reply)
    save(prog, "design.json", d)
    print(f"{len(d['functions'])} functions ({sum(f['kind'] == 'pure' for f in d['functions'])} pure)")
    for f in d["functions"]:
        print(f"  {f['kind']:7} {f['contract'][:110]}")


def shared(d) -> str:
    return f"=== app/domain.py ===\n{d['domain_py']}\n=== app/abilities.py ===\n{d['abilities_py']}"


def callees(d, f) -> str:
    by = {g["name"]: g for g in d["functions"]}
    lines = [f"- {by[c]['contract']}" for c in f.get("calls", []) if c in by]
    return "\n".join(lines) or "(none)"


def worker_prompt(d, f) -> str:
    return f"""Implement one function. Its spec is below; it is everything you need.

{STYLE}

{shared(d)}

=== functions you may call (already implemented in logic.py) ===
{callees(d, f)}

=== your function: {f['signature']} ({f['kind']}) ===
{f['spec']}

Return ONLY the complete function definition in one ```python block."""


def tests(prog: str) -> None:
    d = load(prog, "design.json")
    out = load(prog, "tests.json") if (pdir(prog) / "tests.json").exists() else {}

    def one(f):
        if f["name"] in out:
            return
        reply = call(STRONG, f"""Write hidden pytest tests for the function `{f['name']}`, from its spec only.
No implementation exists yet; the tests decide whether an implementation is accepted, so test
exactly what the spec says (its examples, its rules, its edge cases) and nothing it leaves open.

{STYLE}

{shared(d)}
=== app/fakes.py ===
{d['fakes_py']}

=== functions it may call ===
{callees(d, f)}

=== {f['signature']} ({f['kind']}) ===
{f['spec']}

Imports: `from app.domain import *`, `from app.abilities import *`, `from app.fakes import *`,
`from app.logic import {f['name']}`, and for services `from stateless import run, supply`
(run a service with `run(supply(fake1, fake2)({f['name']})(args))`; a domain error surfaces as
the raised exception). 4-8 test functions. Return one ```python block.""", f"{prog}:tests:{f['name']}")
        out[f["name"]] = code_block(reply)

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, d["functions"]))
    save(prog, "tests.json", out)
    if "__acceptance__" not in out:
        specs = "\n\n".join(f"### {f['signature']} ({f['kind']})\n{f['spec']}" for f in d["functions"])
        reply = call(STRONG, f"""Write end-to-end pytest tests for the whole program, one per scenario below,
using only the public functions in the specs. They run against an implementation assembled
from independently written functions, so test behaviour the specs pin down.

{STYLE}

{shared(d)}
=== app/fakes.py ===
{d['fakes_py']}

=== all function specs ===
{specs}

=== scenarios ===
""" + "\n".join(f"- {s}" for s in d["acceptance"]) + """

Imports: `from app.domain import *`, `from app.abilities import *`, `from app.fakes import *`,
`from app.logic import *`, `from stateless import run, supply`. Return one ```python block.""",
                     f"{prog}:acceptance")
        out["__acceptance__"] = code_block(reply)
        save(prog, "tests.json", out)
    print(f"{len(out)} test files")


def extract(reply: str, name: str) -> str | None:
    import ast
    import textwrap
    for block in re.findall(r"```(?:python|py)?\s*\n(.*?)```", reply, re.S) or [reply]:
        src = textwrap.dedent(block)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                lines = src.splitlines(keepends=True)
                first = min([x.lineno for x in node.decorator_list] + [node.lineno])
                return "".join(lines[first - 1:node.end_lineno])
    return None


def assemble(d, impl: dict) -> str:
    return HEADER + "\n\n" + "\n\n".join(impl.get(f["name"]) or
                                         f"def {f['name']}(*a, **k):\n    raise NotImplementedError\n"
                                         for f in d["functions"])


def run_tests(d, impl: dict, test_src: str) -> tuple:
    tmp = Path(tempfile.mkdtemp(prefix="dsw-"))
    try:
        app = tmp / "app"
        app.mkdir()
        (app / "__init__.py").write_text("")
        (app / "domain.py").write_text(d["domain_py"])
        (app / "abilities.py").write_text(d["abilities_py"])
        (app / "fakes.py").write_text(d["fakes_py"])
        (app / "logic.py").write_text(assemble(d, impl))
        (tmp / "test_it.py").write_text(TEST_PRELUDE + test_src)
        p = subprocess.run(["bash", "-c", "ulimit -v 3000000; exec \"$@\"", "_", sys.executable, "-m", "pytest", "-q",
                            "--tb=short", "-p", "no:cacheprovider", "-p", "no:randomly", "-p", "no:specdeck",
                            "test_it.py"], cwd=tmp, capture_output=True, text=True, timeout=300,
                           env={**os.environ, "PYTHONPATH": str(tmp)})
        m = re.search(r"(\d+) passed", p.stdout)
        f = re.search(r"(\d+) failed", p.stdout)
        e = re.search(r"(\d+) error", p.stdout)
        return p.returncode == 0, int(m.group(1)) if m else 0, (int(f.group(1)) if f else 0) + (int(e.group(1)) if e else 0), p.stdout[-2000:]
    except subprocess.TimeoutExpired:
        return False, 0, 0, "timeout"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def attempt(prog: str, model: str, label: str) -> None:
    """Every function from its spec alone, graded on its hidden tests with the reference for
    everything else (or, for the reference itself, with whatever else the reference has)."""
    d = load(prog, "design.json")
    t = load(prog, "tests.json")
    ref = load(prog, "ref.json")["impl"] if label != "ref" else {}
    out_path = pdir(prog) / f"{label}.json"
    res = load(prog, f"{label}.json") if out_path.exists() else {"impl": {}, "rows": {}}

    def one(f):
        name = f["name"]
        if name in res["rows"]:
            return
        p = worker_prompt(d, f)
        tries = []
        for k in range(2):
            reply = call(model, p, f"{prog}:{label}:{name}")
            src = extract(reply, name)
            base = dict(ref) if label != "ref" else {g["name"]: res["impl"].get(g["name"]) for g in d["functions"]}
            if label == "ref":   # the reference grades against the other reference functions that exist so far
                base = {k2: v for k2, v in base.items() if v}
            ok, npass, nfail, out = run_tests(d, {**base, name: src}, t[name]) if src else (False, 0, 0, "no function in reply")
            tries.append({"src": src, "ok": ok, "passed": npass, "failed": nfail, "out": out[-1500:],
                          "out_tokens": None})
            if ok:
                break
            p += f"\n\n=== your previous answer ===\n```python\n{src or reply[:2000]}\n```\n=== it failed these tests ===\n{out[-1500:]}\nFix the function. Return only the function."
        res["impl"][name] = tries[-1]["src"]
        res["rows"][name] = {"kind": f["kind"], "first": tries[0]["ok"], "ok": tries[-1]["ok"],
                             "passed": tries[-1]["passed"], "failed": tries[-1]["failed"], "tries": tries}
        print(f"{label:12} {f['kind']:7} {name:32} first={tries[0]['ok']} ok={tries[-1]['ok']} "
              f"({tries[-1]['passed']} passed, {tries[-1]['failed']} failed)", flush=True)
        save(prog, f"{label}.json", res)

    if label == "ref" or model.startswith("qwen"):
        for f in d["functions"]:      # in order: a service's callees exist before it is graded
            one(f)
    else:
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(one, d["functions"]))
    ok, npass, nfail, out = run_tests(d, res["impl"], t["__acceptance__"])
    res["acceptance"] = {"ok": ok, "passed": npass, "failed": nfail, "out": out}
    print(f"{label}: {sum(r['ok'] for r in res['rows'].values())}/{len(d['functions'])} functions; "
          f"assembled from {label} only, acceptance {npass} passed {nfail} failed", flush=True)
    save(prog, f"{label}.json", res)


def report(prog: str) -> None:
    d = load(prog, "design.json")
    labels = [p.stem for p in sorted(pdir(prog).glob("*.json")) if p.stem not in ("design", "tests")]
    data = {l: load(prog, f"{l}.json") for l in labels}
    print(f"{'function':32} {'kind':7} " + " ".join(f"{l[:12]:>12}" for l in labels))
    for f in d["functions"]:
        cells = []
        for l in labels:
            r = data[l]["rows"].get(f["name"])
            cells.append("." if r is None else ("FIRST" if r["first"] else ("retry" if r["ok"] else "fail")))
        print(f"{f['name']:32} {f['kind']:7} " + " ".join(f"{c:>12}" for c in cells))
    for l in labels:
        rows = data[l]["rows"].values()
        a = data[l].get("acceptance", {})
        print(f"{l:12} ok {sum(r['ok'] for r in rows)}/{len(rows)}  first {sum(r['first'] for r in rows)}  "
              f"pure {sum(r['ok'] for r in rows if r['kind'] == 'pure')}/{sum(r['kind'] == 'pure' for r in rows)}  "
              f"acceptance {a.get('passed')}/{(a.get('passed') or 0) + (a.get('failed') or 0)}")


if __name__ == "__main__":
    cmd, prog = sys.argv[1], sys.argv[2]
    if cmd == "design":
        design(prog)
    elif cmd == "tests":
        tests(prog)
    elif cmd == "ref":
        attempt(prog, STRONG, "ref")
    elif cmd == "work":
        model = sys.argv[3]
        attempt(prog, model, model.replace(":", "_"))
    elif cmd == "report":
        report(prog)
