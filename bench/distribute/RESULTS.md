# Distribution with and without the spec — results 2026-10-03

Three bugs planted in tapedeck. An instructor (gpt-5.6-terra) that cannot read the code
sees the failing suite, dispatches diagnosis jobs to workers, then writes the patches
from their reports. Up to two rounds. Graded by the full suite with specdeck off.

- plain arm: pytest output with specdeck off (every affected test fails)
- spec arm: pytest with specdeck on (prerequisite-blocked tests are skipped) plus
  `specdeck context --task stuck`; workers also get the spec entries for their tests

Bug sets (bugs.py):
- easy  m007 m013 m016: 12 failures plain / 6 failed + 7 blocked with specdeck; each bug's tests in their own test file
- hub   m003 m000 m026: 21 failures across 8 test files plain / 10 failed + 11 blocked; the two small bugs hide in test_cli.py among the hub's failures

| part | workers | bugs | arm | seed | green | rounds | jobs | duplicate jobs | jobs covering 2 bugs | instructor tok | worker tok | total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | terra | easy | plain | 0 | yes | 1 | 4 | 1 | 0 | 8,001 | 23,797 | 31,798 |
| 1 | terra | easy | spec | 0 | yes | 2 | 4 | 1 | 0 | 37,400 | 21,035 | 58,435 |
| 1 | terra | hub | plain | 0 | yes | 1 | 3 | 0 | 0 | 11,904 | 15,277 | 27,181 |
| 1 | terra | hub | spec | 0 | yes | 1 | 3 | 0 | 0 | 17,333 | 15,913 | 33,246 |
| 2 | gpt-5.4-mini | easy | plain | 0 | yes | 1 | 3 | 0 | 0 | 9,361 | 15,152 | 24,513 |
| 2 | gpt-5.4-mini | easy | spec | 0 | yes | 2 | 6 | 3 | 0 | 56,213 | 37,944 | 94,157 |
| 2 | gpt-5.4-mini | hub | plain | 0 | yes | 2 | 4 | 2 | 1 | 34,737 | 25,691 | 60,428 |
| 2 | gpt-5.4-mini | hub | spec | 0 | yes | 1 | 3 | 0 | 0 | 18,203 | 15,668 | 33,871 |
| 2 | gpt-5.4-mini | hub | plain | 1 | yes | 1 | 3 | 0 | 0 | 12,931 | 16,612 | 29,543 |
| 2 | gpt-5.4-mini | hub | spec | 1 | yes | 1 | 3 | 0 | 0 | 17,684 | 16,440 | 34,124 |

## What happened

- Ceiling: 10/10 green. The instructor grouped 12 or 21 failures into exactly 3 root
  causes from the traceback text alone in 9 of 10 trials. Statement-deletion bugs leave a
  distinctive error (`AttributeError: _module_path`, `NameError: raw`) in every affected
  test, so the blocked/direct split the spec adds is not needed to cluster.
- The spec's prompt is ~3x larger, so its instructor costs ~5-6k more tokens per round.
- Second rounds came from patch mechanics, not diagnosis: a non-unique `old` string
  (part 1 easy spec), and a mini worker's wrong fix for m016 (part 2 easy spec).
- The one decomposition mistake: part 2 hub plain seed 0 bundled two bugs into one job
  for a mini worker, which found only one of them; the second needed another round.
  This is the Minions pattern (multi-part jobs hurt weak workers), seen once, n=1.
- Workers sized at one job per root cause did the job with both terra and mini.

## Caveats

- tapedeck's tests carry `@scenario` decorators, so any worker shown a test sees its
  scenario name in both arms. The arms differ in run output and the spec document.
- Bugs are single-line mutants with loud errors. Silent semantic bugs (assertion-only
  failures with no shared error text) are where clustering by traceback would break,
  and were not tested.

# Round 2: quiet bugs (2026-10-03, later)

Bug set `quiet` (quiet.py searched all 717 tapedeck mutants; 83 fail only at a test `assert`):
q0 serialize.py:50 `return redactor.scrub(value)` -> `pass` (9 tests, 6 files, every message a different wrong value),
q1 proxies.py:44 (1 test), q2 services.py:115 `!=` -> `==` (3 tests, drift). Plain run: 13 failures;
specdeck run: 8 failed + 10 skipped. Weak workers are now gpt-4o-mini.

| part | workers | arm | seed | green | bugs fixed | rounds | jobs | total tok |
|---|---|---|---|---|---|---|---|---|
| 1 | terra | plain | 0 | no | 2/3 | 2 | 5 | 48,389 |
| 1 | terra | spec | 0 | yes | 3/3 | 2 | 6 | 68,463 |
| 1 | terra | plain | 1 | yes | 3/3 | 2 | 4 | 38,904 |
| 1 | terra | spec | 1 | no | 2/3 | 2 | 6 | 70,096 |
| 2 | gpt-4o-mini | plain | 0 | no | 1/3 | 2 | 8 | 64,777 |
| 2 | gpt-4o-mini | spec | 0 | no | 0/3 | 2 | 6 | 76,088 |
| 2 | gpt-4o-mini | plain | 1 | no | 1/3 | 2 | 6 | 50,959 |

(part 2 spec seed 1 not run: cap reached. One crashed and one killed trial are missing from
these ledgers; see the usage API.)

- The ceiling broke, but not on clustering: the instructor grouped q0's 9 scattered failures
  correctly every time. With terra workers every unfixed bug was q2, and every miss was routing: the code-blind
  instructor sent the drift job to tape.py/cli.py/serialize.py, guessing from names; `diff_tapes`
  lives in gateway/services.py. Workers said "the diff implementation is not among the provided
  files". The spec as given (statuses, steps, depends_on) says nothing about which source file
  a test runs, so it could not help; specdeck's coverage map (`--specdeck-coverage`) does, and
  was not included.
- gpt-4o-mini workers: located the bug in 5 of 20 jobs, and most patches failed to apply because
  their quoted `old` text did not match the file exactly. 0 of 3 trials green, both arms.
- The spec arm's instructor costs ~2x tokens (40k vs 20k) for no measured gain.
