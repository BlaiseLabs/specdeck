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

# Round 3: smaller jobs for gpt-4o-mini, with a no-specdeck control (2026-10-04, atomic.py)

Same quiet bugs, terra instructor, gpt-4o-mini workers. Arms change one thing at a time:
A = run.py's format (the earlier plain trials are this arm); B = A with numbered lines and
line-range splices; C0 = one function per job (verdict, then a separate fix call),
functions from the files the instructor guesses, no specdeck; C1 = C0 with the functions
the chosen tests run, from specdeck's coverage map of the last green run. C1 vs C0 is
specdeck's contribution. Harness versions: v1 asked verdict+fix in one call (calibration,
excluded); v2 split them; v3 removed the example line number workers had copied and sends
back a fix outside the flagged function. Each change applied to C0 and C1 alike.

| arm | trials | green | bugs whose tests pass | bug functions flagged in round 1 | function checks / trial | wrong flags / trial | tokens / trial |
|---|---|---|---|---|---|---|---|
| A (current format) | 3 (1 + 2 earlier) | 0 | — | — | — | — | 51k–72k |
| B (line splice) | 1 | 0 | 2 of 3 | — | — | — | 64k |
| C0 (no specdeck), v2+v3 | 4 | **0** | 4 of 12 | **1 of 12** | 75–104 | 2–11 | 99k–146k |
| C1 (specdeck coverage), v2+v3 | 4 | **3** | 9 of 12 | **12 of 12** | 7–9 | 1 | 24k–46k |

- With the map, the instructor picked the failing test that runs the least code for each
  cause (4, 1 and 2 functions), and a worker flagged each buggy function in round 1 in
  every trial (v1 included). Without it, the guessed files mostly missed, or the buggy
  function was checked against a test whose failure was indirect and the worker said "no".
- Smaller jobs alone did not help: C0 did no better than B at twice the cost. Breaking the
  work down paid off only when the coverage map said which pieces mattered.
- C1's one miss (v2 seed 2): two fix workers returned the template's example line (12);
  the patch put `return` at module level. Fixed in v3; both v3 C1 trials went green.
- Every arm, every trial: gpt-4o-mini never restored q0 correctly. It writes `return value`
  for `return redactor.scrub(value)`, which passes the suite and puts secrets on the tape
  unredacted (checked). Terra workers restored it every time. tapedeck has no test for
  redacting a plain string value.

Caveats: one repo, three bugs; n=4 per C arm across v2/v3; the instructor is strong
(terra); the map assumes a green run exists to record it from. "leak" in v2 C1 seed 2 is an
import failure, not a leak. Ledger atomic.jsonl, 1,074,705 tokens (matches the OpenAI usage API).

# Round 4: replication on proofdeck (2026-10-04, atomic.py with DIST_SUBJECT=proofdeck)

New codebase, harness v3 frozen, same arms C0 (no specdeck) / C1 (coverage map). Bugs from
quiet_cov.py (450 sampled mutants, covering tests only; 108 quiet): p0 _source.py:97
is_docstring `return ...` -> `pass` (10 tests, 5 files), p1 arid.py:76 is_arid `return True`
-> `return False` (4 tests), p2 schemata.py:65 switch_line `+ delta` -> `- delta` (3 tests).
17 failures, all assertions. 2 trials per arm (cap reached; the two parallel runs overshot
the 520k cap by 11k).

| arm | green | bugs whose tests pass | buggy fn flagged | functions checked | tokens |
|---|---|---|---|---|---|
| C0 | 0 of 2 | p0+p1, p0+p1 | 1 of 3 each (is_arid) | 88, 84 | 136k, 144k |
| C1 | 0 of 2 | p0+p1, p1 | 1 of 3 each (is_arid) | 82, 57 | 148k, 103k |

Verdicts on the buggy functions whenever a worker saw them:
C0: is_docstring never checked; is_arid bug x4; switch_line no x1.
C1: is_docstring no x6; is_arid bug x3 (its 4 "no" were in investigations of other bugs,
correctly); switch_line no x3, unsure x2.

- The map routed: C1's instructor again picked the narrowest failing tests, and the buggy
  functions reached workers far more often (switch_line 5x vs 1x, is_docstring 6x vs 0x).
- The bottleneck moved to recognition: gpt-4o-mini never said "bug" for is_docstring or
  switch_line. The failing tests assert on a mutation tool's output about sample code
  (`bulk_discount`), and workers judged the tool's functions "unrelated to bulk_discount".
- C0 did as well as C1 here because proofdeck's test names say where to look
  (test_noise_lines_are_arid -> arid.py); tapedeck's did not.
- p0's tests passed in 3 of 4 trials without is_docstring being restored: patches to
  is_arid made docstrings arid another way. Restored correctly: 0 bugs in 4 trials.

So the coverage map's advantage did not replicate here. It helps when the hard part is
finding where to look; it cannot help when the worker cannot recognise the bug once shown
it. Ledger atomic_proofdeck.jsonl, 531,323 tokens.

## Correction and probes (why the workers missed the bugs)

Correction to round 4: is_docstring was NOT a recognition failure. Every time a worker saw it,
it was paired with another bug's tests, so its 6 "no" verdicts were right. The hub's one narrow
test has a parametrize id containing a docstring with a newline; pytest's summary cut the id at
the newline, so the instructor named an id that matched nothing (no test source, no failure
output, 0 functions in the map). Every other p0 test runs 69-87 functions, over the 25 cap.
Routing broke on an id artifact plus the cap.

switch_line is the real recognition failure. The bug is three steps from the failing assert:
`mut[a:b - delta]` cuts the mutated expression one character short (`qty >= 1`), the rebuilt
line doesn't match, switch_line returns None, build silently falls back to the next mutant
(100 -> 101), and the test sees 5 instead of 15. probe2-4.py, 3 gpt-4o-mini calls each, same
prompt as the experiment plus one addition (probe_trace.json, 31.9k tokens):

| added to the worker prompt | says "bug" | fix correct (`b + delta`, line 65) |
|---|---|---|
| nothing (as in the experiment) | 1/3 | - |
| call trace: returned None for the `>=` mutant | 0/3 | - |
| trace + one sentence: the test needs the `>=` mutant switched in | 3/3 | 0/3 (edits line 51) |
| a walk-through question with the inputs | 2/3 | 0/3 (edits line 66) |
| expectation + the actual intermediate values | 3/3 | 0/3 (edits line 66 / rewrite keeps the bug) |

Facts alone get rationalised ("None means it can't be switched, working as intended"); the
expectation flips the verdict; nothing tried moved the fix from where the mismatch is detected
(line 66) to where the wrong value is made (line 65).

# Round 5: who writes the fix, rigged narrow tests, and what predicts weak-model trouble (2026-10-04)

Arms (atomic.py v4): S and W both use the coverage map, test ids resolved (my parser had cut
ids at spaces), and an instructor-written `expectation` per investigation shown to workers.
S: terra writes each fix. W: gpt-4o-mini writes it. Rigged = DIST_NARROW=1 (narrow/*.py:
direct unit tests of the planted bugs' functions, written from the original code).

| repo | arm | trials | green | notes |
|---|---|---|---|---|
| tapedeck | S | 2 | 2 | seed 1 restored all three exactly; seed 2 terra wrote `return value` (leak) |
| tapedeck | W | 2 | 1 | seed 1 leak; seed 2 serialize bug unfixed |
| tapedeck | W + rigged tests | 2 | 2 | no leak; seed 2 restored all three exactly |
| proofdeck | S | 2 | 1 | switch_line fixed exactly (`b + delta`) in both trials |
| proofdeck | W | 1 | 0 | switch_line "fixed" with `if mut == 'if qty >= 100:'` (special-cases the test input) |

- Strong fixer: solves the step weak workers could not (switch_line, 2/2 exact; probes had 0/9).
- The redaction leak is a test gap more than a model gap: terra wrote `return value` too (1/2);
  with a direct redaction test, gpt-4o-mini fixed it correctly 2/2.
- Cost: expectations made workers flag far more (proofdeck S seed 2: 34 flags in 107 checks;
  W: 45 in 110), and in S every flag paid for a terra fix call (130k fixer tokens in one trial).
  The per-trial cap was checked only between rounds; single proofdeck rounds cost 150-250k.
  S should have the instructor pick which flags to fix before calling terra.

Identifier study (identifiers.py, tapedeck, 40 quiet bugs, perfect routing: the bug function
with its narrowest failing test; 55k tokens):

| calls from test to bug function | n | recognised | tests pass after fix | restored exactly |
|---|---|---|---|---|
| 1 (test calls it directly) | 13 | 11 | 6 | 1 |
| 2-3 | 16 | 8 | 3 | 2 |
| 4+ | 9 | 4 | 1 | 1 |
| not traced | 2 | 0 | 0 | 0 |

Breadth (functions the test runs: 1-9 -> 10/17 recognised, 50+ -> 9/17) and test length did
not predict. Call depth did. One repo, n=40: a lead, not a law. Not run (budget): rigged tests
on proofdeck, and the identifier study on proofdeck.

## Identifier study, replicated on proofdeck (identifiers_proofdeck.jsonl, 40 bugs, 57.5k tokens)

| calls from test to bug function | tapedeck recognised | proofdeck recognised | both |
|---|---|---|---|
| 1 (direct) | 11/13 | 12/16 | 23/29 (79%) |
| 2-3 | 8/16 | 4/21 | 12/37 (32%) |
| 4+ | 4/9 | 1/3 | 5/12 (42%) |

Depth vs breadth, both repos (78 traced bugs), recognised:

| | test runs <=9 functions | test runs >=10 |
|---|---|---|
| test calls the function directly | 16/18 (89%) | 7/11 (64%) |
| function 2+ calls deep | 7/17 (41%) | 10/32 (31%) |

Depth 1 vs 2+: odds ratio 7.2, Fisher p = 0.00016. Breadth <=9 vs >=10: odds ratio 2.9,
p = 0.025. Depth is the main signal, breadth secondary, test length none. Fixes are another
matter: tests passed after gpt-4o-mini's fix in only 1/16 direct proofdeck cases.

Built into proofdeck (commit b53fb27): both tracers record the calls from each test to each
function it enters; `--format json` carries `reach`; `proofdeck check --profile distribute`
lists changed functions no test calls directly.

## Rigged narrow tests on proofdeck (v4n_proofdeck_W.jsonl, W arm, 1 trial, 105.9k tokens)

With direct unit tests of the three bug functions added, gpt-4o-mini workers (map routing +
instructor expectations, weak model writes fixes) flagged all three buggy functions in round 1
(un-rigged: one of three per trial). It also flagged 9 helper functions the tiny coverage sets
contain (Source.tree, cached, Source.line...). Final: p0 and p2 tests pass, p1 not; nothing
restored exactly. Round 2 was cut by the per-trial cap (65k). The second trial did not fit.

Together with tapedeck (rigged W 2/2 green, no leak): direct tests make the weak model find the
bug; fixing stays the weak step on proofdeck, which the strong fixer (arm S) handled.
