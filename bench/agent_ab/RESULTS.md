# Agent A/B on tapedeck: spec vs test files

Model `gpt-5.6-terra`. Subject: tapedeck (/workspaces/python/tapedeck). Every answer is scored against ground truth produced by actually applying the change and running the suite (see groundtruth.py).

## Impact — which tests fail if this line changes like so?

| arm | n | mean F1 | exact | precision | recall | F1 on killed | F1 on survived |
|---|---|---|---|---|---|---|---|
| spec | 32 | 0.80 | 59% | 0.81 | 0.82 | 0.86 | 0.62 |
| specmap | 32 | 0.88 | 78% | 0.88 | 0.90 | 0.97 | 0.62 |
| shipped | 32 | 0.88 | 75% | 0.86 | 0.90 | 0.96 | 0.62 |
| bound | 32 | 0.85 | 69% | 0.84 | 0.87 | 0.96 | 0.50 |
| hub | 32 | 0.89 | 75% | 0.88 | 0.90 | 0.97 | 0.62 |
| order | 32 | 0.85 | 72% | 0.85 | 0.85 | 0.96 | 0.50 |
| longlast | 32 | 0.88 | 72% | 0.88 | 0.89 | 0.96 | 0.62 |
| tests | 32 | 0.93 | 88% | 0.92 | 0.93 | 0.99 | 0.75 |
| testsrule | 32 | 0.85 | 75% | 0.86 | 0.85 | 0.92 | 0.62 |
| both | 32 | 0.93 | 88% | 0.92 | 0.93 | 0.99 | 0.75 |

## Triage — which failures are root causes, which are blocked, fix what first?

| arm | n | mean F1 | failed F1 | blocked F1 | exact | fix-first right |
|---|---|---|---|---|---|---|
| spec | 10 | 0.77 | 0.67 | 0.88 | 40% | 100% |
| specrule | 10 | 1.00 | 1.00 | 1.00 | 100% | 90% |
| shipped | 10 | 1.00 | 1.00 | 1.00 | 100% | 90% |
| bound | 10 | 0.96 | 0.96 | 0.96 | 80% | 90% |
| hub | 10 | 0.96 | 0.96 | 0.96 | 70% | 70% |
| order | 10 | 1.00 | 1.00 | 1.00 | 100% | 100% |
| longlast | 10 | 1.00 | 1.00 | 1.00 | 100% | 100% |
| brief | 10 | 1.00 | 1.00 | 1.00 | 100% | 90% |
| tests | 10 | 0.78 | 0.68 | 0.88 | 40% | 100% |
| testsrule | 10 | 1.00 | 1.00 | 1.00 | 100% | 100% |
| both | 10 | 0.77 | 0.67 | 0.88 | 40% | 100% |

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
| spec | 6 | 60,572 | 0 | 19,394 | 13,909 | 79,966 |
| tests | 6 | 86,624 | 0 | 18,905 | 13,209 | 105,529 |
| both | 6 | 137,210 | 0 | 20,492 | 14,784 | 157,702 |
| specmap | 4 | 55,127 | 0 | 14,675 | 11,014 | 69,802 |
| specrule | 2 | 19,445 | 0 | 5,236 | 3,143 | 24,681 |
| testsrule | 6 | 87,224 | 0 | 23,214 | 17,538 | 110,438 |
| shipped | 6 | 81,758 | 0 | 19,848 | 14,110 | 101,606 |
| bound | 6 | 82,766 | 0 | 20,716 | 14,737 | 103,482 |
| hub | 6 | 82,808 | 0 | 20,761 | 15,055 | 103,569 |
| order | 6 | 81,842 | 0 | 21,734 | 15,995 | 103,576 |
| longlast | 6 | 82,808 | 0 | 19,770 | 14,054 | 102,578 |
| brief | 2 | 16,791 | 0 | 5,487 | 3,393 | 22,278 |
| all | 62 | 874,975 | 0 | 210,232 | 150,941 | 1,085,207 |
