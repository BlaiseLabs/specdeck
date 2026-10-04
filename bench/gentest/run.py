"""Does routing test generation through a spec produce tests that catch more real bugs?"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import arms
import grader
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv("/workspaces/python/.env")
MODEL = os.environ.get("GT_MODEL", "gpt-5.6-terra")
HERE = Path(__file__).parent
LOG = HERE / "trials.jsonl"
SYSTEM = ("You are a meticulous Python engineer who writes tests that would catch a subtly "
          "wrong implementation, not tests that merely exercise the happy path.")
BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


def call(client, prompt: str, ledger: list) -> str:
    t0 = time.time()
    r = client.chat.completions.create(model=MODEL, messages=[
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    u = r.usage
    ledger.append({"prompt": u.prompt_tokens, "completion": u.completion_tokens, "total": u.total_tokens,
                   "reasoning": getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0,
                   "seconds": round(time.time() - t0, 1)})
    return r.choices[0].message.content or ""


def code_of(text: str) -> str:
    blocks = BLOCK.findall(text)
    return max(blocks, key=len).strip() if blocks else ""


def trial(client, arm: str, module: str, seed: int) -> dict:
    ledger: list = []
    middle = ""
    if arm == "direct":
        out = call(client, arms.direct(module), ledger)
    elif arm == "plan":
        middle = call(client, arms.plan_step(module), ledger)
        out = call(client, arms.tests_from_plan(module, middle), ledger)
    elif arm == "spec":
        middle = call(client, arms.spec_step(module), ledger)
        out = call(client, arms.tests_from_spec(module, middle), ledger)
    elif arm == "spec-open":
        middle = call(client, arms.spec_step_open(module), ledger)
        out = call(client, arms.tests_from_spec_open(module, middle), ledger)
    elif arm == "spec-nocap":          # only the generation cap lifted, spec step unchanged
        middle = call(client, arms.spec_step(module), ledger)
        out = call(client, arms.tests_from_spec_open(module, middle), ledger)
    else:
        raise SystemExit(f"unknown arm {arm}")
    code = code_of(out)
    if not code:
        return {"arm": arm, "module": module, "seed": seed, "model": MODEL,
                "tokens": sum(c["total"] for c in ledger), "error": "no code block in the reply"}
    scored = grader.grade(code, module)
    return {"arm": arm, "module": module, "seed": seed, "model": MODEL,
            "tokens": sum(c["total"] for c in ledger), "calls": len(ledger),
            "middle_tokens": len(middle) // 4, **scored, "code": code, "middle": middle}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="direct,plan,spec")
    ap.add_argument("--modules", default="tapedeck/engine/tape.py")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--seed-from", type=int, default=0)
    ap.add_argument("--budget", type=int, default=500_000)
    args = ap.parse_args()
    spent = sum(json.loads(l)["tokens"] for l in LOG.read_text().splitlines()) if LOG.exists() else 0
    done = {(json.loads(l)["arm"], json.loads(l)["module"], json.loads(l)["seed"])
            for l in LOG.read_text().splitlines()} if LOG.exists() else set()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print(f"spent {spent:,} of {args.budget:,}")
    for seed in range(args.seed_from, args.seed_from + args.runs):
        for module in args.modules.split(","):
            for arm in args.arms.split(","):
                if (arm, module, seed) in done:
                    continue
                if spent > args.budget - 20_000:
                    print("STOP: budget"); return 3
                r = trial(client, arm, module, seed)
                spent += r["tokens"]
                with LOG.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(f"  {arm:7} {module.split('/')[-1]:14} seed{seed}  "
                      f"valid {r.get('validity', 0):.0%}  "
                      f"tests={r.get('tests', 0):>3} caught={r.get('caught', 0):.0%} "
                      f"({r.get('killed', 0)}/{r.get('mutants', 0)})  {r['tokens']:>6,} tok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
