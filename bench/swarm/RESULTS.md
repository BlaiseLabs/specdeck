# Can a small model write one function from its purpose and direct tests? (2026-10-12)

**Short answer: a strong model can (22/22); the small ones can't yet (gpt-4o-mini 9/22,
qwen3:8b 8/22).** The information in the skeleton is enough. What's missing is the small
models' ability to use it.

## Setup

- **Subject:** 24 functions, picked at random from tapedeck, proofdeck and specdeck among
  those that some test calls directly (traced with `direct_plugin.py`), each with a 3-40 line
  body.
- **Calibration:** 22 functions remain after dropping 2 whose tests still passed with the
  body blanked.
- **What each worker sees:**
  - the module with only that function's body removed, everything else final
  - the purpose: its docstring, or for 11 undocumented functions one sentence terra wrote from
    the original
  - the tests that call it directly
- **Retries:** one retry with the failure output.
- **Grading:** the direct tests first, then the repository's **whole suite**.

Two rounds:
- **v1:** at most 3 direct tests shown; the retry gets the direct tests' failure output.
- **v2:** every direct test shown; the whole suite decides, and its failure output feeds the
  retry. This is how a real pipeline would run.

## Results (whole suite green = pass)

| model | v1 | v2 | first try (v2) | where it ran |
|---|---|---|---|---|
| gpt-5.6-terra | 19/22 | **22/22** (its 3 v1 misses rerun) | 21/22 | OpenAI |
| gpt-4o-mini | 7/22 | **9/22** | 7/22 | OpenAI |
| qwen3:8b (thinking off) | 6/22 | **8/22** | 7/22 | Mac mini, free, ~44 s/call |

What the small models solved together: 7 functions both solved, 10 at least one. What they
solved is mostly small glue and self-contained helpers: `get`, `__call__`, `select`, `load`,
`watch`, `main`, `snapshot`.

## What it says

1. **The skeleton carries enough information, if it shows every test of the function.**
   - All three of terra's v1 misses broke a direct test the harness hadn't shown.
   - With all direct tests shown it got 22/22.
2. **The gap is model capability, not information.** Given the same prompt, terra solved 13
   functions that gpt-4o-mini did not. The small models' failures:
   - **Invented attributes and imports:** `Report.test_results`, `from specdeck.trust import
     analyze`.
   - **Guessed data shapes:** `diff_transcript` assumed an `event` key.
   - **Ignored helper classes the module already has:** `TapeCallable` in `__getattr__`.
   - **Off-by-one logic:** `vacuous_assertions` returned lines [3, 5] where [3, 4, 5] was
     expected.

   These are failures to use what's on the page, the same weakness seen in the debugging
   studies.
3. **A direct test is not a safe gate.** In v1, 6 of gpt-4o-mini's 13 direct-test passes
   broke a test it never saw (4 of qwen's 8). Accepting a cheap worker's code needs every test
   that reaches the function, or the whole suite. specdeck's coverage map can supply that list.
4. **Retries help little.** Most passes came on the first try: gpt-4o-mini 7 of 9, qwen 7 of 8.
5. **Output tokens:**
   - terra spent about 1,650 output tokens per function, mostly reasoning.
   - gpt-4o-mini spent about 250 and qwen about 280.
   - The cheap model's saving is real, but on this sample it applies to only about 40% of the
     functions. The rest escalate.

## What it does not say

- **This was existing code**, written for humans and full of cross-references. Code *designed*
  for a swarm (pure functions, data shapes spelled out in the signature, worked examples) is
  untested. The functions small models solved lean that way, which is a hint, not a result.
- **Sample size and runs:** n=22, one run per arm. The v1→v2 changes include run-to-run noise
  (gpt-4o-mini lost `exit_code` in v2 after passing it in v1).

## Cost

| model | tokens |
|---|---|
| gpt-4o-mini (both rounds) | 284,692 |
| gpt-5.6-terra (11 purpose sentences, v1, 3 v2 reruns) | 144,766 |
| **OpenAI total, account one** | **429,458 of the 500k grant** |
| qwen3:8b (local, free) | 243,085 |

Files:
- `swarm.py`: `calibrate`, `purpose`, `run`; `SWARM_V2=1`, `SWARM_ONLY=`
- `direct_plugin.py`
- `functions*.json`, `purposes.json`
- `results*.jsonl`
- `ledger.jsonl`
