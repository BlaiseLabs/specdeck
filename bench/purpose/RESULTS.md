# Does a spec help an agent understand why the tests exist?

Subject: specdeck's own repo (16 requirements, 44 real `covers=` links). Agent: gpt-5.6-terra
through `navigate/agent.py` (read-only shell), 10 questions with mechanical answers
(`questions.py`, ground truth verified against `covers=`). Copies built by `build.py`, every
suite 91/91.

## v1 — comments and docstrings left in the source (ledger `trials.jsonl`, 420,704 tok)

All three copies 8/10 with identical misses (Q6, Q7). The source comments carry most of the
"why", so the plain copy was a documented one. Ceiling; superseded by v2.

## v2 — no comment, docstring or prose doc in any copy

Every copy: comments removed by tokenize, docstrings by AST position (code otherwise
identical; AST checked), README/*.md/pyproject comments and description removed. Each copy's
only business material is its own:

- **plain**: test names and code only.
- **separate_doc**: plus REQUIREMENTS.md, the says+why of every requirement as prose, no ids,
  no test names (859 tokens).
- **specdeck**: `@scenario` decorators (ids, group, tags, `depends_on`, `covers=`), the
  given/when/then step text, requirements.yaml, `.specdeck/`, SPEC.md (23,378 tokens), and an
  AGENTS.md holding only a one-line pointer to SPEC.md and requirements.yaml.
- **separate_doc_prompt / specdeck_prompt**: the same copies on disk, with REQUIREMENTS.md /
  SPEC.md also pasted into the first message ahead of the questions.

The limits are the same for every session: past 40k tokens the next call must answer; there is
a hard abort at 60k. The document's own tokens count inside that budget.

| Arm | Sessions | Right, strict | Right, hand | Commands | Calls | Tokens | Soft limit fired | Opened requirements.yaml |
|---|---|---|---|---|---|---|---|---|
| plain | 2 | 4, 6 = **10/20** | 6, 7 = 13 | 12, 28 | 10, 7 | 58,867 + 76,599 | yes, no | — |
| separate_doc | 2 | 8, 6 = **14/20** | 8, 8 = 16 | 17, 22 | 7, 10 | 55,333 + 67,574 | yes, yes | — |
| specdeck | 2 | 10, 7 = **17/20** | 10, 7 = 17 | 24, 11 | 7, 10 | 75,938 + 57,715 | yes, yes | yes, no |
| separate_doc_prompt | 1 (+1 aborted) | **8/10** | 8 | 11 | 7 | 54,320 (+42,008 aborted) | yes | — |
| specdeck_prompt | 1 | **6/10** | 7 | 4 | 3 | 81,122 | yes, after call 2 | no |

The hand check accepts "a CI report" for Q5 and "won't turn a green suite red" for Q9.

Per question, strict (right / sessions):

| Q | plain | separate_doc | specdeck | separate_doc_prompt | specdeck_prompt | asks |
|---|---|---|---|---|---|---|
| 1 | 2/2 | 2/2 | 2/2 | 1/1 | 1/1 | why partial runs must not shrink the spec |
| 2 | 0/2 | 2/2 | 2/2 | 1/1 | 0/1 | why depends_on never orders execution |
| 3 | 2/2 | 2/2 | 2/2 | 1/1 | 1/1 | why hub functions are shown as a count |
| 4 | 2/2 | 2/2 | 2/2 | 1/1 | 1/1 | which guarantee the alias test protects |
| 5 | 0/2 | 1/2 | 2/2 | 1/1 | 0/1 | which need the statuses test serves (PR report) |
| 6 | 1/2 | 0/2 | 1/2 | 0/1 | 0/1 | tests guarding the exit-code guarantee (link) |
| 7 | 0/2 | 0/2 | 1/2 | 0/1 | 0/1 | tests serving only the enforcement guarantee (link) |
| 8 | 1/2 | 2/2 | 2/2 | 1/1 | 1/1 | tests serving only the partial-run guarantee |
| 9 | 1/2 | 1/2 | 2/2 | 1/1 | 1/1 | why gates are opt-in |
| 10 | 1/2 | 2/2 | 1/2 | 1/1 | 1/1 | why the agent document states the blocking rule |

**Reading it.**
- **specdeck leads at n=2.** Its gain over the prose doc is Q5, Q6, Q7 and Q9, each a
  single-session difference.
- **Pasting SPEC.md into the prompt made things worse, not better.** At 23k tokens, the
  first two calls cost 52k, so the agent got two exploring turns (4 commands) before it had to
  answer.
- **SPEC.md doesn't carry the "why" or the links.** It has no requirement text and no
  `covers=`, so pasting it cannot answer Q2 or Q6/Q7.
- **Pasting the prose doc** (859 tokens) left the agent free to explore (11 commands) and
  scored like separate_doc.
- **The only perfect session** (specdeck s0, 10/10) is the one that opened requirements.yaml.

## Biases

- **n is tiny:** 2, 2, 2, 1, 1. One session swings by 3–4 answers (specdeck went 10 then 7).
- **The forced stop fired in 7 of 8 completed sessions.** This measures understanding on a
  ~40–60k reading budget, which favours concise material and penalises a 23k-token preface.
- **Lost sessions:**
  - specdeck_prompt s2 could not start: it needs about 81k (its call sizes barely vary) and
    68.9k of its 150k cap was left.
  - separate_doc_prompt s2 was aborted by the cap guard before its forced-answer call. The
    guard's estimate of that call would have crossed the 120k cap, so 42,008 tokens were spent
    with no answers.
- **Some "why" survives in code in every copy:** agent-document string constants in
  `context.py` and user messages in `plugin.py`. This is why Q1, Q3 and Q4 sit at the ceiling
  everywhere. JS/CSS comments in `specdeck/web/` were not stripped (not Python); no agent
  opened them.
- **Run order:**
  - v2 s0: specdeck, plain, separate_doc.
  - v2 s1: plain, separate_doc, specdeck.
  - specdeck_prompt s0, then separate_doc_prompt s0 and s1.

## v3: specdeck's context fix (2026-10-04)

The fix (specdeck/context.py, profiles.py, cli.py): the agent document gets a
**Requirements** section — each requirement's says, why and status, the tests linked to
it with `covers=`, and beside each test any other requirement it also covers. It is in
the default `balanced` profile and in a new `intent` profile (`specdeck context --task why`)
that carries the requirements alone. Built from /tmp/specdeck_src_v3: the fixed code with
the original requirements.yaml and tests, so the subject is the v2 subject. Same questions,
graders and limits (answer at 40k, abort at 60k).

| Arm | Document | Size (tokens) | Sessions | Right, strict | Tokens per session |
|---|---|---|---|---|---|
| specdeck (on disk) | default, now with requirements | 26,305 | 3 | 10 + 10 + 7 = 27/30 (hand 28: Q9 "won't turn a passing suite failing") | 61k, 85k, 57k |
| specdeck_prompt | `--task why` pasted | 2,344 | 3 | 10 + 10 + 9 = 29/30 | 56k, 38k, 28k |

Against v2: plain 10/20, separate_doc 14/20, specdeck 17/20, separate_doc_prompt 8/10,
specdeck_prompt (old 23k document) 6/10.

- Link questions Q6/Q7: specdeck_prompt 3/3 and 3/3; specdeck on disk 2/3 and 2/3 (v2: 1/2, 1/2).
- The one specdeck_prompt miss was Q5 (described what the statuses report computes, not that it feeds the PR comment).
- specdeck seed 2 never opened SPEC.md (grepped `covers` in the tests instead) and missed Q6, Q7, Q9.
- The soft limit fired in all 3 on-disk sessions and 1 of 3 prompt sessions.

Biases: the "also covers" annotation was designed after seeing Q6/Q7 fail, on these same
questions, so it is fitted to them; a fresh question set would test it honestly. n=3. The
copy's own specdeck/context.py now contains the fix's code (an agent reading it sees the
new section described). Ledger: trials_v3.jsonl, 325,972 tokens.

## Replication on proofdeck (2026-10-04)

A fresh subject (proofdeck) with 16 requirements whose whys come from its own rationale,
56 tests linked by covers=, and 10 new questions; all of them were frozen before any session. 3
sessions per arm, same limits. specdeck was frozen at e7a06fc.

| Arm | Right, strict | Hand | Tokens per session | Q10 (exclusive tests) |
|---|---|---|---|---|
| plain | 22/30 | 22 | ~71k | 0/3 |
| separate_doc | 27/30 | 27 | ~62k | 0/3 |
| specdeck_prompt (`--task why`, 4,244 tokens) | 29/30 | 30 | ~5.8k | 3/3 |

Details, sources, hashes, harness changes and biases: `proofdeck/RESULTS.md`.
The ledger is `proofdeck/trials_proofdeck.jsonl` (479,398 tokens). The on-disk specdeck
arm was not run because the budget ran out.
