"""Generate tests, then let proofdeck say what they missed, twice."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import arms
import loop
from dotenv import load_dotenv
from openai import OpenAI
from run import BLOCK, SYSTEM, code_of

load_dotenv("/workspaces/python/.env")
MODEL = os.environ.get("GT_MODEL", "gpt-5.6-terra")
PATCH = bool(os.environ.get("LOOP_PATCH"))
LOG = Path(__file__).parent / ("loop_patch_trials.jsonl" if PATCH else "loop_trials.jsonl")


def call(client, prompt, ledger):
    t0 = time.time()
    r = client.chat.completions.create(model=MODEL, messages=[
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    u = r.usage
    ledger.append({"prompt": u.prompt_tokens, "completion": u.completion_tokens,
                   "total": u.total_tokens, "seconds": round(time.time() - t0, 1)})
    return r.choices[0].message.content or ""


def trial(client, module, seed, rounds=2):
    fb, held = loop.split_mutants(module)
    ledger, history = [], []
    code = code_of(call(client, arms.direct(module), ledger))
    if not code:
        return {"module": module, "seed": seed, "error": "no code block"}
    for i in range(rounds + 1):
        on_held = loop.grade_on(code, module, held)
        on_fb = loop.grade_on(code, module, fb)       # the set it is being coached on
        history.append({"round": i, "held_out": on_held.get("caught", 0.0),
                        "tests": on_held.get("tests", 0), "validity": on_held.get("validity", 0),
                        "killed_ids": on_held.get("killed_ids", []),
                        "feedback_set": on_fb.get("caught", 0.0),
                        "code_len": len(code)})
        print(f"    round {i}: coached-on {on_fb.get('caught',0):.0%} | HELD-OUT "
              f"{on_held.get('caught',0):.0%} ({on_held.get('killed',0)}/{len(held)}) | "
              f"{on_held.get('tests',0)} tests | validity {on_held.get('validity',0):.0%}", flush=True)
        if i == rounds:
            break
        surv = loop.survivors_for(code, module, fb)
        if not surv:
            print("    no survivors in the feedback set — nothing to feed back", flush=True)
            break
        if PATCH:
            # Ask for ADDITIONAL tests only. Rewriting the whole file each round made the
            # suite decay: validity fell 82% -> 77% -> 60% as working tests were lost in the
            # rewrite, and both scores fell with it. Appending cannot damage what already works.
            prompt = "\n\n".join([
                "You wrote a pytest suite for the module below. Some bugs slip past it.",
                arms.description(module),
                "## The tests you already have — they stay exactly as they are\n\n"
                "```python\n" + code + "\n```",
                "## " + loop.feedback_text(module, surv),
                "Write ONLY NEW tests to add to the file. Do not repeat, rewrite or modify any "
                "existing test. Give every new test a name that does not already appear above. "
                "Assume the imports and helpers already in the file are available.",
                arms.RULES, arms.FORMAT])
            reply = call(client, prompt, ledger)
            added = code_of(reply)
            merged = loop.append_tests(code, added) if added else code
            print(f"    feedback: showed {len(surv)} survivors | added {len(added)} chars "
                  f"| file {len(code)} -> {len(merged)} chars", flush=True)
            code = merged
        else:
            prompt = "\n\n".join([
                "Here is a pytest test suite you wrote for the module below, and the bugs it fails "
                "to catch.", arms.description(module),
                "## Your current tests\n\n```python\n" + code + "\n```",
                "## " + loop.feedback_text(module, surv), arms.RULES, arms.FORMAT])
            reply = call(client, prompt, ledger)
            new = code_of(reply)
            print(f"    feedback: showed {len(surv)} survivors | reply {len(reply)} chars "
                  f"| extracted {len(new)} chars", flush=True)
            if new:
                code = new
    final = history[-1]
    return {"module": module, "seed": seed, "model": MODEL, "arm": "loop-patch" if PATCH else "loop",
            "rounds": len(history) - 1, "history": history,
            "held_out": final["held_out"], "tests": final["tests"], "validity": final["validity"],
            "killed_ids": final["killed_ids"], "tokens": sum(c["total"] for c in ledger),
            "calls": len(ledger), "code": code}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", default="tapedeck/engine/tape.py,tapedeck/engine/serialize.py,"
                                         "tapedeck/engine/netguard.py")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--budget", type=int, default=450_000)
    args = ap.parse_args()
    spent = sum(json.loads(l).get("tokens", 0) for l in LOG.read_text().splitlines()) if LOG.exists() else 0
    done = {(json.loads(l)["module"], json.loads(l)["seed"])
            for l in LOG.read_text().splitlines()} if LOG.exists() else set()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print(f"spent {spent:,} of {args.budget:,}")
    for seed in range(args.runs):
        for module in args.modules.split(","):
            if (module, seed) in done:
                continue
            if spent > args.budget - 30_000:
                print("STOP: budget"); return 3
            print(f"  {module.split('/')[-1]} seed{seed}", flush=True)
            r = trial(client, module, seed, args.rounds)
            spent += r.get("tokens", 0)
            with LOG.open("a") as fh:
                fh.write(json.dumps(r) + "\n")
            print(f"  -> final held-out {r.get('held_out',0):.0%}  {r.get('tokens',0):,} tok "
                  f"| running {spent:,}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
