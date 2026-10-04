"""Does a spec make an unfamiliar codebase easier to find your way around?

Three copies of tapedeck (see strip.py): bare (no docstrings, no specdeck), docs (docstrings,
no specdeck), spec (docstrings + specdeck decorators + .specdeck/ + SPEC.md). The same agent,
with the same read-only shell, answers the same ten questions in each. Measured: answers
right, shell commands needed, tokens spent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import agent
import strip
from questions import QUESTIONS

HERE = Path(__file__).parent
LOG = HERE / "trials.jsonl"
MODEL = os.environ.get("NAV_MODEL", "gpt-5.6-terra")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="bare,docs,spec")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--seed-from", type=int, default=0)
    ap.add_argument("--max-calls", type=int, default=25)
    ap.add_argument("--budget", type=int, default=300_000)
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
            if spent > args.budget - 60_000:
                print("STOP: budget", flush=True)
                return 3
            repo = strip.build(arm, Path(f"/tmp/nav_run_{arm}_{seed}"))
            res = agent.answer(client, MODEL, repo, QUESTIONS, max_calls=args.max_calls)
            graded = {qid: bool(check(str(res["answers"].get(qid, "")))) for qid, _, check in QUESTIONS}
            row = {"arm": arm, "seed": seed, "model": MODEL, "correct": sum(graded.values()),
                   "graded": graded, **res}
            spent += res["tokens"]
            with LOG.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            read_spec = any("SPEC.md" in c or ".specdeck" in c for c in res["commands"])
            print(f"  {arm:5} seed{seed}  {row['correct']:>2}/10 right  {res['tool_calls']:>2} commands  "
                  f"{res['tokens']:>7,} tok  {res['seconds']:>5.0f}s"
                  + ("  (read the spec)" if read_spec else "") + f"  | spent {spent:,}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
