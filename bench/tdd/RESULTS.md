# Phase 1: does a generated contract help distributed test-passing work?

**Verdict: no. The hypothesis is not supported, and the stopping rule says stop.**

## The question

Take `tapedeck`, replace all 11 implementation modules with bare signatures (1,621 lines,
0 of 38 tests passing), and ask `gpt-5.6-terra` to write the code until the tests pass.
Three ways of asking, each with one retry on the failure output (the Aider Polyglot
protocol), each graded after the test files are restored from a pristine copy:

- **mono** — everything in one prompt.
- **split** — one worker per dependency stage, in topological order, each seeing its own
  stage's tests plus the code earlier stages wrote.
- **spec** — identical to `split`, plus `specdeck context --for <stage>`: the contract
  (expected values, dependencies, build order) for that slice. **The only variable.**

The hypothesis came from "The Specification Gap" (arXiv:2603.24284), which measured a
25–39 point penalty for splitting implementation work between agents and found that what
recovers it is each slice carrying the full interface contract — not conflict detection,
which added nothing. specdeck's document is that contract, and it is produced even when
no code exists yet.

## Results

| arm | n | visible tests | held-out tests | tokens |
|---|---|---|---|---|
| mono | 2 | 18.5/31 ±3.5 | 0.5/7 | 46,583 |
| split | 2 | 19.5/31 ±3.5 | 2.5/7 | 140,448 |
| spec | 2 | 17.0/31 ±0.0 | 0.0/7 | 192,406 |

Per run:

| arm | run | visible | held-out | regression | tokens |
|---|---|---|---|---|---|
| mono | 0 | 16/31 | 0/7 | 6/7 | 45,796 |
| mono | 1 | 21/31 | 1/7 | 6/7 | 47,370 |
| split | 0 | 22/31 | 5/7 | 6/7 | 141,095 |
| split | 1 | 17/31 | 0/7 | 6/7 | 139,802 |
| spec | 0 | 17/31 | 0/7 | 6/7 | 193,679 |
| spec | 1 | 17/31 | 0/7 | 6/7 | 191,133 |

## What it says

1. **The document did not help.** `spec` scored lowest on both metrics and cost 4× `mono`
   and 1.4× `split`. Whatever the coordination gap costs here, handing each worker the
   generated contract did not recover it; the extra 7–8k tokens per call may have crowded
   out the test detail the worker actually needed.
2. **Splitting did not help either.** 19.5 against 18.5 visible, inside a ±3.5 standard
   deviation, for three times the tokens.
3. **The one promising result was noise.** `split` run 0 passed 5 of 7 held-out tests;
   run 1 passed 0. That is the whole reason for running more than once.
4. **Everything overfits, badly.** Every arm passed 55–71% of the tests it could see and
   **0–16% of the tests it could not**. Visible pass rate is not a measure of correctness
   on this task; without the held-out split this experiment would have reported success.
5. **Every arm broke a regression test** (6/7), i.e. made something that previously worked
   stop working — which no visible-pass-rate metric would have shown either.
6. No cheating was detected in any final run: no test edits, no added skips, no swallowed
   exceptions. The guards fired once during sizing on a false positive (`sys.exit` in a
   CLI module) and were corrected.

## Why this is consistent with the literature

- Dan Luu (zstd decoder, 26 prompt conditions, hidden-test grading): the **"no special
  instructions" baseline performed above average**, and the TDD condition produced more
  iteration but **fewer runs passing every hidden test**.
- Spec Kit Agents: a full spec-driven pipeline bought **+1.7 points** on SWE-bench Lite.
- Cognition, "Don't Build Multi-Agents", and Anthropic's own guidance: coding work has
  fewer genuinely parallel slices than research, and shared-context/high-dependency
  domains are a poor fit.

## Cost and what was learned for the money

868,664 tokens total: 109,789 on sizing probes, 758,875 on the six trials.

The sizing probes were the most useful part and are worth recording. The first target —
three modules with docstrings intact — was **saturated**: one prompt reimplemented all of
it perfectly, 38/38, for 32k tokens. Stripping the docstrings changed nothing; still
38/38. Only scaling to all 11 modules produced headroom. An experiment that had skipped
that calibration would have compared three arms all scoring 100%.
