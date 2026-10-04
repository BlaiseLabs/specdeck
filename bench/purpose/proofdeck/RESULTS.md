# Replication on proofdeck: does specdeck's requirements section help an agent understand why tests exist?

The subject is a repo nothing was tuned on: proofdeck. `subject/` is a snapshot of its working
tree (69 tests, all pass). specdeck was frozen at e7a06fc and not changed. Hashes of the
subject, requirements, links and questions, taken before any session, are in FREEZE.md.

## Material (frozen before questions were written)

- **requirements.yaml: 16 entries, every one with `says` and `why`.** Each why is a paraphrase
  or quote of proofdeck's own rationale; SOURCES.md gives the source per entry. By kind (an
  entry can have several): module docstrings 10, test docstrings 9, README 10, commit
  messages 2, a code message string 1.
- **links.yaml: 56 of the 60 test functions linked by `covers=`** on their existing
  `@scenario` decorators.
  - 9 tests cover two requirements; the 4 tests whose checks no requirement states
    (diff scoping, hanging mutants) stay unlinked.
  - A real run's requirements report matches links.yaml exactly, and all 16 requirements
    are met.
- **questions_pd.py: 10 questions,** written after the freeze, hash `d20c5a70…`:
  - 4 "why" questions (Q1–Q4);
  - 2 "which need does test X serve" (Q5, Q6);
  - 2 "which tests guard Y", graded on recall only (Q7, Q8);
  - 2 "which tests serve only Y", where shared tests must be left out (Q9, Q10).

## Copies (built like v2: every comment, docstring and prose doc stripped; every suite 69/69)

| Arm | Business material |
|---|---|
| plain | test names and code only |
| separate_doc | + REQUIREMENTS.md, the says and why as prose, no ids, no test names (1,319 tokens) |
| specdeck_prompt | `@scenario` (ids, tags, depends_on, covers=), step text, requirements.yaml, `.specdeck/` from a real run, AGENTS.md pointer; `specdeck context --task why` (4,244 tokens) pasted into the prompt and also on disk as SPEC.md |
| specdeck (on disk) | default SPEC.md (23,138 tokens), **not run**: the budget ran out (see below) |

## Results (gpt-5.6-terra; answer-now at 40k, abort at 60k; 3 sessions per arm, order rotated per round)

| Arm | Right, strict | Right, hand | Tokens per session | Total tokens | Turns | Commands | Soft limit fired |
|---|---|---|---|---|---|---|---|
| plain | 7, 8, 7 = **22/30** | 22 | 70,211 / 68,984 / 74,772 | 213,967 | 10, 8, 8 | 24, 16, 19 | 3 of 3 |
| separate_doc | 9, 9, 9 = **27/30** | 27 | 60,446 / 55,744 / 69,513 | 185,703 | 14, 9, 11 | 13, 16, 19 | 3 of 3 |
| specdeck_prompt | 10, 9, 10 = **29/30** | 30 | 5,821 / 5,784 / 5,708 | 17,313 | 1, 1, 1 | 0, 0, 0 | 0 of 3 |

Per question (strict, right of 3):

| Q | plain | separate_doc | specdeck_prompt | asks |
|---|---|---|---|---|
| 1 | 3 | 3 | 3 | why a branch-written "confirmed" comment doesn't confirm |
| 2 | 3 | 3 | 3 | why no mutation score |
| 3 | 1 | 3 | 3 | why default argument values are never mutated |
| 4 | 3 | 3 | 3 | why editing an approved requirement fails the gate |
| 5 | 3 | 3 | 2 (hand 3) | which need the schemata test serves (fast fork mode) |
| 6 | **0** | 3 | 3 | who uses the JSON evidence and what it tells apart (requirement met by a real check vs through fakes) |
| 7 | 3 | 3 | 3 | tests guarding "unconfirmed never fails the gate" (recall) |
| 8 | 3 | 3 | 3 | tests guarding "mock-only" (recall) |
| 9 | 3 | 3 | 3 | tests serving only "unproven lines fail" |
| 10 | **0** | **0** | 3 | tests serving only "independent source", shared ones left out |

How exact the lists were (recall is what Q7/Q8 were frozen to grade):
- **Q7 exact:** plain 0/3, separate_doc 1/3, specdeck_prompt 3/3.
- **Q8 exact:** plain 0/3, separate_doc 0/3, specdeck_prompt 3/3.
- **Padding:** plain and separate_doc added 1–6 unlinked tests every time. On Q8 they always
  added the JSON-evidence test.

The hand check differs from strict in one place. specdeck_prompt seed 1's Q5 answer, "users
get the same mutation answers as real mutants ... correct line numbers", states the
requirement's `says` (fork mode with the same answers). The frozen regex missed it. Two other
calls stay as strict grading had them:
- plain Q3 (twice) only restates that signatures are classified arid, with no reason.
- plain Q6 (three times) never says who uses the evidence beyond the line level.

**Did the specdeck arm open anything?** No. In all three sessions specdeck_prompt answered in
its first call with no commands, so it never opened requirements.yaml or SPEC.md on disk. The
pasted `--task why` document was the whole source.

**Reading it:**
- **The v3 result replicates on fresh material.** The requirements section beats the same
  facts as unlinked prose, 29–30 against 27, at about a tenth of the tokens (5.8k against
  62k per session).
- **The prose doc does almost as well on the why questions.** Everything prose can carry,
  it carries.
- **The links decide the rest.** Q10 (which tests exist only for this requirement, with
  three tests that also serve another requirement) is 3/3 with the section and 0/3 without
  it, prose included. The exact lists for Q7/Q8 are 6/6 against 1/12.
- **The "also covers" note was designed on specdeck's questions** and is unchanged here;
  it answered a new exclusivity question on a new repo.

## Harness changes (applied to every arm)

1. **`run_pd.py` keeps each session's raw final text, and re-parses it if agent.py found no
   answers** (`json.loads(..., strict=False)`, which allows raw newlines in strings).
   - **Why:** the first separate_doc session (seed 0) came back with no answers. agent.py
     drops a final answer that won't parse and keeps no copy, so the cause can't be seen.
   - **Fallout:** that session (62,415 tokens) is marked invalid in the ledger and was rerun
     with the fix.
   - **Effect:** the fallback parse never fired afterwards, so the fix changed no grade.
2. **`build_pd.py` handles a trailing comma when adding `covers=`** to a multi-line
   `@scenario(...)`. This was a build error caught before any session.

## Budget

- **Ledger:** `trials_proofdeck.jsonl`, 479,398 tokens of the 520,000 cap, including the
  invalid 62,415.
- **The specdeck on-disk arm did not run.** A session needs about 85k of headroom, and
  40,602 was left after the three core arms.
- **Provider snapshots:** `purpose-proofdeck-start` and `purpose-proofdeck-end`. The key is
  shared with other runs.

## Biases

- **I wrote the requirements, the links and the questions.** They were frozen in that
  order, before any session (hashes in FREEZE.md).
  - The whys are sourced, not invented, but which rationale became a requirement was my
    choice.
  - The links are my reading of what each test checks. For example, the schemata test is
    linked only to fork mode's speed. The Q5 and Q10 truths rest on those choices.
- **The specdeck arm carries more than requirements.**
  - The step text and `tags=["speed"]` on the fork tests are specdeck's format, and they
    help Q5.
  - In the `--task why` document the requirements' why sits next to their tests. That is
    the feature being measured.
- **Prompt against disk is not a pure comparison of formats.**
  - specdeck_prompt had its document in the prompt. separate_doc had to find REQUIREMENTS.md
    and hit the forced stop every time.
  - A separate_doc_prompt arm was not run here. In v2 it scored 8/10 against
    separate_doc's 8+6, so pasting prose did not change much.
- **Most why answers are in the code for a careful reader too.** That is why plain gets
  Q1, Q2, Q4 and Q5 right; Q3 and Q6 need the stated reason.
- **Graders:**
  - Q7/Q8 are recall-only by the frozen design, so padded lists pass. Exactness is
    reported separately.
  - Q5's regex was narrower than its requirement's `says`; this produced the one hand
    correction.
- **The subject's README and docstrings are the source of the whys.** Stripping them is
  what makes plain realistic, but every why exists in proofdeck's real repo. A developer
  with the original repo would have them.
- **n = 3 per arm, one model.**
