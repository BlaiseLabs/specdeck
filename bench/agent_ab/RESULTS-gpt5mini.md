# Agent A/B on tapedeck: spec vs test files

Model `gpt-5-mini`. Subject: tapedeck (/workspaces/python/tapedeck). Every answer is scored against ground truth produced by actually applying the change and running the suite (see groundtruth.py).

## Impact — which tests fail if this line changes like so?

| arm | n | mean F1 | exact | precision | recall | F1 on killed | F1 on survived |
|---|---|---|---|---|---|---|---|
| brief | 32 | 0.59 | 38% | 0.57 | 0.69 | 0.78 | 0.00 |
| wiring | 32 | 0.71 | 47% | 0.75 | 0.74 | 0.86 | 0.25 |

## Triage — which failures are root causes, which are blocked, fix what first?

| arm | n | mean F1 | failed F1 | blocked F1 | exact | fix-first right |
|---|---|---|---|---|---|---|
| brief | 10 | 1.00 | 1.00 | 1.00 | 100% | 90% |
| wiring | 10 | 0.75 | 0.64 | 0.85 | 40% | 100% |

## What the material answers without a model

Both tasks were also solved mechanically, straight from the same material, to see how much
of each one is reasoning and how much is lookup.

| computed directly from | impact F1 | triage F1 | triage exact |
|---|---|---|---|
| coverage: every test that runs the changed **file** | 0.35 | — | — |
| coverage: every test that runs the changed **function** | 0.57 | — | — |
| the DAG, 'anything upstream failed means blocked' | — | 0.74 | 40% |
| the DAG, 'blocked only by a prerequisite that already ran' | — | **1.00** | **100%** |

The last row is the finding that matters: triage is not a judgement call, it is a rule, and
the rule is exact. Every model arm scored what the *wrong* rule scores — because the spec
prints `depends_on` without saying that specdeck never reorders tests, so a prerequisite
listed later cannot block anything.

## Tokens

| arm | calls | prompt | of which cached | completion | of which reasoning | total |
|---|---|---|---|---|---|---|
| brief | 6 | 53,210 | 34,560 | 42,644 | 34,816 | 95,854 |
| wiring | 6 | 86,816 | 60,160 | 43,673 | 37,888 | 130,489 |
| all | 12 | 140,026 | 94,720 | 86,317 | 72,704 | 226,343 |
