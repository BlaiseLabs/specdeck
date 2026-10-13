# Demo: "never" rules in AGENTS.md, backed by tests

The pain, in a developer's own words (r/ClaudeCode, October 2026):

> putting the "never" rules in CLAUDE.md and backing them with scripted checks
> — [comment](https://www.reddit.com/r/ClaudeCode/comments/1x3zfol/weekly_showcase_thread_what_are_you_building_with/pffkwrz/)

and, in the thread about whether generated CLAUDE.md files are good practice:

> The lines that earned their place in mine are all things it got wrong once.

People already do this by hand: a rule in the instructions file, a script that checks it. What
the hand-made version lacks is the link between the two. The file can't say which rules are
broken right now, the check doesn't say which rule it guards or why, and the two drift apart.
specdeck keeps each rule as a requirement — statement, reason, the test that checks it — and
writes them into AGENTS.md with their status from the last run.

## Run it

```bash
cd examples/agent-rules
./demo.sh            # pauses between steps; PAUSE=0 ./demo.sh runs straight through
```

It works in a scratch copy, so this directory is left as it is.

## The 60 seconds

| Time | On screen | Say |
|---|---|---|
| 0–8 s | The quote above | "Lots of us keep 'never' rules in CLAUDE.md and a script that checks them. specdeck makes that one thing." |
| 8–18 s | Step 1: `requirements.yaml` | "Each rule is a requirement: what must hold, and why. The why is the bad session it came from." |
| 18–26 s | Step 2: the rule's test | "Each rule has a test. Ordinary pytest, linked to the rule." |
| 26–36 s | Step 3: `pytest`, `specdeck agents`, AGENTS.md | "One command writes the rules into AGENTS.md, with the test behind each and whether it held in the last run. Your hand-written part of the file stays." |
| 36–44 s | Step 4: the agent's refund function | "An agent adds a refund, the quick way: float." |
| 44–52 s | Step 5: the failing rule test | "The suite fails on the rule, not on the feature, and the summary names the rule that broke." |
| 52–60 s | Step 6: AGENTS.md, broken rule first | "And AGENTS.md now opens with the broken rule, its reason, and the test to make pass — for the next agent session or the reviewer." |

## What it shows, and what it doesn't

- It shows a broken rule being **caught**, named in the run's summary line and at the top of
  AGENTS.md with its reason and the test to make pass.
- It does not show **prevention**, and the A/B in `bench/rules_ab` says not to claim it for
  specdeck. With terra, a teammate's nudge against a rule ("just use pandas") led to a break in
  4 of 6 tasks with no rules written down and 1 of 6 with any rules file. Plain bullets did as
  well as specdeck's generated file, which cost more tokens.
- A rule test is only as good as its check. In that A/B, both agents that hit a rule test under
  pressure got around it (`importlib.import_module("pandas")` past an import scan) rather than
  keeping the rule. One sentence in AGENTS.md, which `specdeck agents` now writes ("making the
  test pass without keeping the rule is breaking the rule"), stopped that in 4 of 4 follow-up
  trials, and so did a test that checks the rule's intent at run time. Small numbers; a reviewer
  should still look.
- An agent that never runs the tests still breaks the rule. CI running `pytest` is what makes it
  stick.
