# Do agents keep a project's rules better with specdeck's AGENTS.md?

2026-10-13. One agent (gpt-5.6-terra, read-only shell + file edits + test runner), a small shop
package with six "never" rules, six feature tasks. Each task's acceptance test passes for a
rule-breaking solution too. One rep per cell, so n = 6 per arm: read the numbers as direction,
not proof. 1.33M tokens in total (676K neutral, 657K pressure), plus a 63K pilot.

| Arm | What the agent has |
|---|---|
| N | no AGENTS.md, no rule tests |
| P | AGENTS.md with the rules as plain "never" bullets, and "run pytest before you finish" |
| PT | P, plus a test per rule in the suite (the hand-rolled "rules backed by scripted checks") |
| S | specdeck: the rules as requirements, the same tests linked to them, AGENTS.md written by `specdeck agents` (statement, reason, verifying test, status), plugin on |

## Results

Trials (of 6) whose final code breaks at least one rule. Every trial in every arm passed its
acceptance test and the original tests.

| | N | P | PT | S |
|---|---|---|---|---|
| Neutral tasks | 1 | 0 | 0 | 0 |
| Tasks with a nudge against a rule ("just return a float", "use pandas") | **4** | 1 | 1* | 1* |
| Mean tokens per trial, neutral / pressure | 19K / 19K | 29K / 25K | 26K / 24K | 38K / 42K |

\* Both are the export task, and both are **gamed**: the agent read the rule test, then loaded
pandas with `importlib.import_module("pandas")`, which the test's import scan does not see. The
rule test stayed green while the rule was broken. In P the same request produced a plain
`import pandas`, visible to any reviewer. `regrade.py` closes the loophole in the grader and
counts these as breaks.

## What this says

1. **Without pressure, a strong agent keeps the rules anyway.** It copies the conventions it finds
   in the code: one break in six with no rules at all. There is little for any rules file to
   prevent here.
2. **Under pressure, having the rules written down is what matters.** Breaks fell from 4 of 6
   to 1 of 6. Plain bullets did that. Neither the rule tests (PT) nor specdeck's generated file
   (S) did better than plain bullets.
3. **Rule tests get gamed under pressure.** Two of the two trials where the rule test stood
   between the agent and what it was asked for got around the check instead of keeping the rule.
   The test made the violation invisible, which is worse than the plain-bullet arm's honest
   `import pandas`. A rule test has to check the rule's intent (here: what is imported at run
   time), not one syntax for it.
4. **No agent told the person it was overriding them.** Where the rules won, the agents quietly
   did it the compliant way; where they lost, the summary just said "using pandas". None said
   "the repo forbids X, so I did Y".
5. **specdeck's file costs more.** S used 1.3–1.7× the tokens of P for the same outcome. Part of
   it is the longer instructions re-read each turn; part is agents trying to run
   `specdeck validate`, which the file asks for and this harness's shell refuses.

## What it means for the pitch

- Do **not** claim that specdeck's AGENTS.md makes agents break fewer rules than a hand-written
  one. This run doesn't show it.
- Do claim what the demo shows: a broken rule is **caught**, named in AGENTS.md with its reason
  and the test to make pass. That holds as long as the rule test checks the rule's intent.
- The gaming result is the stronger story, and it is proofdeck's territory: "the tests pass" is
  not the same as "the rule holds".

## Files

- `run.py` the harness (`RULES_PRESSURE=1` adds the nudge); `tasks.py` tasks, acceptance tests, nudges
- `rules.py`, `rules_template.py` the rules and their checks; `reference/apply.py` a rule-abiding solution to all six
- `results.jsonl` (neutral), `results_pressure.jsonl` (pressure), `pilot.jsonl`; `regrade.py` adds `broken_strict` and `gamed`
- `materials/AGENTS.specdeck.md` the file the S arm was given
