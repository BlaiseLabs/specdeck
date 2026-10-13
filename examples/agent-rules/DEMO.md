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
| 44–52 s | Step 5: the failing rule test | "The suite fails on the rule, not on the feature: the refund itself passes its test." |
| 52–60 s | Step 6: AGENTS.md, broken rule first | "And AGENTS.md now opens with the broken rule, its reason, and the test to make pass — for the next agent session or the reviewer." |

## What it shows, and what it doesn't

- It shows a broken rule being **caught** and **reported where the agent reads**. It does not
  show that the file **prevents** the break. Whether it does was measured separately
  (`bench/rules_ab`); see the results there before claiming it.
- The rule tests are ordinary pytest. specdeck does not check rules by itself: someone writes the
  test, as with any fitness function. What specdeck adds is the link (rule → reason → test →
  status) and keeping the instructions file current from the last run.
- An agent that never runs the tests still breaks the rule. CI running `pytest` is what makes it
  stick.
