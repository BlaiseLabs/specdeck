# Agent A/B on specdeck: spec vs test files

Model `gpt-5.6-terra`. Subject: specdeck (/workspaces/python/specdeck). Every answer is scored against ground truth produced by actually applying the change and running the suite (see groundtruth.py).

## Impact — which tests fail if this line changes like so?

| arm | n | mean F1 | exact | precision | recall | F1 on killed | F1 on survived |
|---|---|---|---|---|---|---|---|
| spec | 16 | 0.73 | 50% | 0.70 | 0.80 | 0.90 | 0.25 |
| shipped | 16 | 0.74 | 56% | 0.72 | 0.80 | 0.91 | 0.25 |
| bound | 16 | 0.72 | 50% | 0.70 | 0.78 | 0.88 | 0.25 |
| hub | 16 | 0.73 | 50% | 0.72 | 0.78 | 0.89 | 0.25 |
| longlast | 16 | 0.79 | 50% | 0.76 | 0.86 | 0.89 | 0.50 |
| tests | 16 | 0.75 | 56% | 0.74 | 0.78 | 0.91 | 0.25 |

## Triage — which failures are root causes, which are blocked, fix what first?

| arm | n | mean F1 | failed F1 | blocked F1 | exact | fix-first right |
|---|---|---|---|---|---|---|
## Tokens

| arm | calls | prompt | of which cached | completion | of which reasoning | total |
|---|---|---|---|---|---|---|
| spec | 2 | 39,769 | 0 | 7,246 | 5,897 | 47,015 |
| shipped | 2 | 55,313 | 0 | 8,852 | 6,163 | 64,165 |
| tests | 2 | 65,321 | 0 | 7,556 | 6,350 | 72,877 |
| bound | 2 | 56,091 | 0 | 8,039 | 6,000 | 64,130 |
| hub | 2 | 50,719 | 0 | 6,886 | 5,594 | 57,605 |
| longlast | 2 | 50,719 | 0 | 6,737 | 5,561 | 57,456 |
| all | 12 | 317,932 | 0 | 45,316 | 35,565 | 363,248 |
