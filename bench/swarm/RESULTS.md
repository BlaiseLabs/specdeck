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

---

# Round 2: code designed for the swarm (2026-10-12, `designed.py`)

The user's correction: a swarm prompt should be a succinct functional spec, not a whole file.
Each function should be pure, so it depends on nothing but its inputs.

**The setup:**
- terra designs a new program in the `stateless` effects style. Pure functions work over frozen
  data; side effects go only through declared abilities, with in-memory fakes for tests.
- A worker sees only `domain.py`, `abilities.py`, its own spec (input shapes, output, numbered
  logic rules, examples) and its callees' signatures and contracts. That's about 1k tokens.
- **Hidden tests:** terra writes per-function tests and 6 acceptance tests from the specs,
  before any code exists.
- **Grading:** a function is graded on its own tests with terra's reference for everything
  else; then the whole program is assembled and run against the acceptance tests.

**Two programs:**
- `srs`, a flashcard scheduler: 20 functions, 16 pure.
- `ledger`, shared expenses: 19 functions, 15 pure.

| | srs functions | srs program built from the worker's code | ledger functions | ledger program built from the worker's code |
|---|---|---|---|---|
| terra (reference, same prompt) | 20/20 (18 first try) | 6/6 | 19/19 (16 first try) | 6/6 |
| gpt-4o-mini | **18/20** | 2/6 → **6/6** with terra for its 2 failures | **13/19** | 2/6 → **6/6** with terra for its 6 |
| qwen3:8b (Mac, free) | **16/20** | 4/6 → **6/6** with terra for its 4 | **9/19** | 2/6 → **6/6** with terra for its 10 |

Compare round 1, which filled holes in existing code: gpt-4o-mini 9/22, qwen 8/22.

**Services** use the clearer worker note (`DESIGNED_STYLE=2`); pure functions use the first
note.

## What it says

1. **The format was the problem.** Succinct, self-contained specs roughly double the small
   models' success. On the flashcard program gpt-4o-mini lands within 2 functions of terra.
2. **Service failures were the worker kit's fault, not the model's.**
   - With the first style note, every service failure was one `stateless` misunderstanding:
     `yield from` on plain method calls, plus Qwen's `return yield from` syntax error.
   - One explicit rule plus a 9-line worked example fixed gpt-4o-mini 8/8 and Qwen 6/8.
   - The kit (style rules + example) is a product component, and cheap to get right once.
3. **What small models still miss:**
   - exact validation edge cases: a bool passed as an amount, an infinite exchange rate, which
     error message wins
   - real algorithms: settle-up with the fewest transfers, cent allocation, statements

   The ledger (money, Decimal, rounding) is much harder for them than the scheduler.
4. **Escalation is essential, and it works.** Built only from small-model code, the programs
   pass 2-4 of 6 acceptance tests. With terra redoing the failures, every program passes 6/6.
5. **The strong model's specs have bugs too, and its own tests catch them.** Fixed by hand
   before any worker ran (each fix is noted inline in `tests.json`):
   - an ambiguous formula (EASY interval: old or new ease?)
   - a worked example contradicting its own payment rule
   - callee contracts missing signatures (a harness fix)
   - test typos (`dt`, a 4-tuple compared with a 5-tuple)
   - acceptance tests using the wrong `stateless.run` call (a prompt fix)

   The step "terra implements against its own tests" is the spec's check and must come before
   the swarm.
6. **The economics are the catch.** terra output tokens per program:

   | | srs | ledger |
   |---|---|---|
   | design (specs) | 7.4k | 8.5k |
   | tests + acceptance | 42.7k | 43.9k |
   | implementing everything | 8.5k | 15.8k |

   - The implementation is only 15-25% of the strong model's output.
   - Workers take the easy functions; the hard, reasoning-heavy ones escalate. So the terra
     implementation output avoided was 49% (srs) / 28% (ledger) for gpt-4o-mini, and 44% / 20%
     for Qwen.
   - That's under 10% of terra's total output per program.
   - Unless the tests get cheaper (fewer, or written by a cheaper model and checked), the swarm
     is a quality/locality story, not a cost one.

**Cost:** 339k OpenAI tokens of the 750k grant (account one); Qwen ran locally for free.
`combine.py` rebuilds the tables.

---

# Round 3: terra says WHAT to test, a cheap model writes HOW (2026-10-13, `casetests.py`)

**The split:**
- **terra:** a terse case list per function, giving the inputs and the exact expected result.
- **Cheap model:** turns each case into pytest code.

**Checks:**
- The cheap model's tests must pass on terra's reference; one fix-up retry is allowed, then any
  test still failing is dropped.
- **Strength:** proofdeck plants one-line mutants (up to 12 per function) in the reference; we
  compare the mutants caught against terra's original tests.
- **Agreement:** the new tests must accept and reject the same round-2 worker functions as the
  originals.

## Cost: no saving

| terra output tokens | original full tests | case lists only |
|---|---|---|
| srs | 36.7k | 39.7k |
| ledger | 40.2k | 32.9k |

- Visible test code was only ~15k per program; the rest of terra's output is reasoning about
  what to check and the exact expected values.
- The case lists are no shorter than the test code (~20k / 14k visible), because they spell
  every object out in full.
- The boilerplate a cheap model can take over was never the cost.

## Quality: the cheap models' tests match terra's

This holds once the expansion prompt is right.

| tests by | functions with usable tests | mutants caught (theirs vs terra's originals on the same functions) | verdicts on worker code same as originals |
|---|---|---|---|
| gpt-4o-mini, srs | 15/20 written, all usable (the grant ran out) | 90 vs 90 of 94 | 15/15 + 15/15 |
| qwen3:8b, srs | 16/20 | 101 vs 101 of 105 | 16/16 + 16/16 |
| qwen3:8b, ledger | 15/19 (approx.) | 103 vs 105 of 112 | 15/15 + 15/15 |

In the last column, the two numbers are gpt-4o-mini's functions + qwen's functions.

**About the first expansion prompt:** it was broken. It described `run(supply(...))` for
services, and gpt-4o-mini applied it to pure functions too ("'bool' object is not an
iterator"), so about half the functions ended up with no usable tests. Saying plainly "a pure
function: call it directly, never run/supply" plus a service example fixed it. That's the same
lesson as round 2: the worker kit matters as much as the model.

## Conclusion

Cheap models write correct, equally strong tests from exact cases. But that doesn't make the
strong model cheaper: its cost is deciding what to check and computing the answers, and that
stays with it. The swarm's case is locality and openness:
- a local model writes most of the code and the test code
- terra designs, decides the cases and handles escalations

It is not a token saving on the strong model.

**Spend:**
- This round: terra case lists 121k; gpt-4o-mini expansion 310k, of which 244k was the broken
  prompt.
- The 750k grant ended at **770.6k**, an overrun of 20.6k from parallel calls in flight when
  the cap hit.
- qwen ran locally for free.
