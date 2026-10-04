"""Does a spec make a better handoff from messy information than a plan or a raw dump?

Three arms. In the first two a PREPARER reads the messy material and writes a handoff; a
fresh BUILDER that never sees the material builds from that handoff alone — how a spec is
actually used: one person synthesises, another builds.

  spec   preparer writes a specdeck-format spec  -> builder builds from the spec
  plan   preparer writes a prompt with a plan     -> builder builds from that prompt
  raw    no preparer: the builder gets the material itself, verbatim

The two preparer instructions are identical except for the format asked for, so a
difference between spec and plan is the format and nothing else. Every builder gets the
same interface contract so every build can be graded by the same hidden tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from requirements import INTERFACE, REQUIREMENTS

HERE = Path(__file__).parent
VERSION = __import__("os").environ.get("MESSY_MATERIAL", "material")
MATERIAL = HERE / VERSION
LOG = HERE / ("trials.jsonl" if VERSION == "material" else f"trials_{VERSION}.jsonl")
MODEL = os.environ.get("MESSY_MODEL", "gpt-5.6-terra")
DIFF = {r: d for r, d, _ in REQUIREMENTS}
BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)

HANDOFF = """\
Below are the internal documents of a small online shop: meeting notes, a support ticket,
a finance email, a chat export, personal notes and an order export. We need the shop's
order-pricing logic implemented.

You will not write the code. A developer who has never seen these documents will build it
from what you write, and from nothing else — so everything they need must be in your
output, and anything wrong in your output will be wrong in what they build. Read every
document; some things are mentioned once in passing, some are changed later, some are
proposed and rejected.
"""

AS_SPEC = """\
Write your handoff as a specification in specdeck's format: YAML with a `scenarios` list.
Each scenario has `id` (kebab-case), `name` (one sentence of what must be true), `steps`
(Given / When / Then; a Then carries an `assertion` with `expected_label` — the exact value
or condition), and `depends_on` where one behaviour builds on another. Be exhaustive: a
scenario for every rule, boundary and exception. Output only the YAML.
"""

AS_PLAN = """\
Write your handoff as a prompt for the developer: explain what to build and lay out a plan
for building it. Write it however serves them best. Be exhaustive: cover every rule,
boundary and exception. Output only the prompt.
"""

BUILD_FOOTER = """\
{interface}

Reply with one fenced Python block containing the complete `pricing.py`, and nothing else.
"""


def material() -> str:
    parts = []
    for p in sorted(MATERIAL.iterdir()):
        if p.is_file():
            parts.append(f"=== {p.name} ===\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def call(client, prompt: str, ledger: list, role: str) -> str:
    t0 = time.time()
    r = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}])
    u = r.usage
    ledger.append({"role": role, "prompt": u.prompt_tokens, "completion": u.completion_tokens,
                   "reasoning": getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0,
                   "total": u.total_tokens, "seconds": round(time.time() - t0, 1)})
    return r.choices[0].message.content or ""


def grade(code: str) -> dict:
    """Run the hidden tests against the build. Per-test outcomes so results can be reported
    per rule and per difficulty."""
    tmp = Path(tempfile.mkdtemp(prefix="messy-"))
    try:
        (tmp / "pricing.py").write_text(code, encoding="utf-8")
        shutil.copy(HERE / "test_acceptance.py", tmp / "test_acceptance.py")
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
                            "-p", "no:specdeck", "test_acceptance.py"],
                           cwd=tmp, capture_output=True, text=True, timeout=120)
        per = {}
        for line in p.stdout.splitlines():
            m = re.match(r"(PASSED|FAILED|ERROR)\s+\S+::(test_(r\d+)_\S*)", line)
            if m:
                per[m.group(2)] = m.group(1) == "PASSED"
        by_rule = defaultdict(list)
        for name, ok in per.items():
            by_rule[re.match(r"test_(r\d+)_", name).group(1).upper()].append(ok)
        return {"passed": sum(per.values()), "total": len(per) or 28, "per_test": per,
                "rules_fully_met": sorted(r for r, oks in by_rule.items() if all(oks)),
                "tail": p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-300:]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def trial(client, arm: str, seed: int) -> dict:
    ledger, handoff = [], ""
    mat = material()
    footer = BUILD_FOOTER.format(interface=INTERFACE)
    if arm == "raw":
        build_prompt = ("Implement the order-pricing logic for this shop from its internal documents. "
                        "Some things are mentioned once in passing, some are changed later, some are "
                        "proposed and rejected.\n\n" + mat + "\n\n" + footer)
    else:
        fmt = AS_SPEC if arm == "spec" else AS_PLAN
        handoff = call(client, HANDOFF + "\n" + INTERFACE + "\n" + fmt + "\n\n" + mat, ledger, "preparer")
        build_prompt = ("Implement the order-pricing logic described below.\n\n"
                        + ("```yaml\n" + handoff.strip() + "\n```" if arm == "spec" else handoff)
                        + "\n\n" + footer)
    reply = call(client, build_prompt, ledger, "builder")
    blocks = BLOCK.findall(reply)
    code = max(blocks, key=len) if blocks else ""
    g = grade(code) if code else {"passed": 0, "total": 28, "per_test": {}, "rules_fully_met": [],
                                  "tail": "no code block"}
    return {"arm": arm, "seed": seed, "model": MODEL, "material": VERSION, **g, "handoff": handoff, "code": code,
            "tokens": sum(c["total"] for c in ledger), "ledger": ledger}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="raw,plan,spec")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--seed-from", type=int, default=0)
    ap.add_argument("--budget", type=int, default=60_000)
    args = ap.parse_args()
    load_dotenv("/workspaces/python/.env")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    rows = [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []
    spent = sum(r["tokens"] for r in rows)
    done = {(r["arm"], r["seed"]) for r in rows}
    print(f"spent {spent:,} of {args.budget:,}", flush=True)
    for seed in range(args.seed_from, args.seed_from + args.runs):
        for arm in args.arms.split(","):
            if (arm, seed) in done:
                continue
            if spent > args.budget - 25_000:
                print("STOP: budget", flush=True)
                return 3
            r = trial(client, arm, seed)
            spent += r["tokens"]
            with LOG.open("a") as fh:
                fh.write(json.dumps(r) + "\n")
            print(f"  {arm:5} seed{seed}  {r['passed']:>2}/{r['total']} tests  "
                  f"{len(r['rules_fully_met']):>2}/12 rules  {r['tokens']:>6,} tok  | spent {spent:,}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
