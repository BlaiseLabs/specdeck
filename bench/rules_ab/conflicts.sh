#!/usr/bin/env bash
# The `conflicts` clause, four wordings, PT arm, all six pressured tasks, brief summaries, account two.
cd "$(dirname "$0")"
export RULES_PRESSURE=1 RULES_SUMMARY=brief RULES_ACCOUNT=2
for v in none current marker requester; do
  RULES_VARIANT=conflicts-$v RULES_LOG=results_conflicts_$v.jsonl \
    python3 run.py --cap 190000 --arms PT --reps 1 --workers 6 > conflicts_$v.log 2>&1 &
done
wait
python3 judge.py results_conflicts_*.jsonl > judge.log 2>&1
