"""Phase 1: three arms, a few runs each, with a hard token budget."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import trial

HERE = Path(__file__).parent
LOG = HERE / "trials.jsonl"


def spent() -> int:
    if not LOG.exists():
        return 0
    return sum(json.loads(l)["tokens"] for l in LOG.read_text().splitlines() if l.strip())


def done() -> set:
    if not LOG.exists():
        return set()
    return {(json.loads(l)["arm"], json.loads(l)["seed"]) for l in LOG.read_text().splitlines() if l.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="mono,split,spec")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--budget", type=int, default=600_000)
    ap.add_argument("--seed-from", type=int, default=0,
                    help="first seed; raise it to add runs without colliding with finished ones")
    args = ap.parse_args()
    have = done()
    print(f"spent so far {spent():,} of {args.budget:,}")
    for seed in range(args.seed_from, args.seed_from + args.runs):
        for arm in args.arms.split(","):
            if (arm, seed) in have:
                continue
            if spent() > args.budget - 60_000:
                print(f"STOP: {spent():,} spent, not enough headroom for another trial")
                return 3
            print(f"\n=== {arm} run {seed}")
            ledger: list = []
            try:
                res = trial.run(arm, seed, ledger)
            except Exception as exc:
                print(f"  TRIAL FAILED: {type(exc).__name__}: {exc}")
                continue
            with LOG.open("a") as fh:
                fh.write(json.dumps(res) + "\n")
            f = res["final"]
            print(f"  -> visible {f['visible']['passed']}/{f['visible']['total']}, "
                  f"held-out {f['held_out']['passed']}/{f['held_out']['total']}, "
                  f"{res['tokens']:,} tokens | running total {spent():,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
