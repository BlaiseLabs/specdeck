# Agent A/B on tapedeck: spec vs test files

Model `gpt-5.4-mini`. Subject: tapedeck (/workspaces/python/tapedeck). Every answer is scored against ground truth produced by actually applying the change and running the suite (see groundtruth.py).

## Impact — which tests fail if this line changes like so?

| arm | n | mean F1 | exact | precision | recall | F1 on killed | F1 on survived |
|---|---|---|---|---|---|---|---|
| brief | 32 | 0.42 | 16% | 0.52 | 0.49 | 0.56 | 0.00 |
| wiring | 32 | 0.45 | 22% | 0.50 | 0.54 | 0.60 | 0.00 |

## Triage — which failures are root causes, which are blocked, fix what first?

| arm | n | mean F1 | failed F1 | blocked F1 | exact | fix-first right |
|---|---|---|---|---|---|---|
| brief | 10 | 0.50 | 0.59 | 0.41 | 0% | 70% |
| wiring | 10 | 0.58 | 0.62 | 0.55 | 0% | 30% |

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
| brief | 6 | 53,210 | 34,560 | 5,445 | 0 | 58,655 |
| wiring | 6 | 86,816 | 62,720 | 6,244 | 0 | 93,060 |
| all | 12 | 140,026 | 97,280 | 11,689 | 0 | 151,715 |
