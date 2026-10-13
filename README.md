# specdeck

**An executable requirements specification for pytest projects, aligned with
ISO/IEC/IEEE 29148:2018.**

ISO/IEC/IEEE 29148:2018, *Requirements engineering*, is the standard that replaced
IEEE 830 as the definition of a software requirements specification. It says what a
requirement is (§5.2.4), what makes one well formed (§5.2.5–5.2.7), what it records
about itself (§5.2.8), how it is verified (§6.5.2) and what a specification contains
(§9.6). It assumes people keep the specification and the evidence in step by hand.

specdeck keeps them in one place:

| The standard | specdeck |
|---|---|
| Requirement statement, "shall" (§5.2.4) | `statement:` in `requirements.yaml` |
| Attributes: identification, rationale, type, priority, owner, risk, difficulty (§5.2.8) | the same names in `requirements.yaml` |
| Verification by test, inspection, analysis/simulation, demonstration (§6.5.2) | `verification:`; *test* is automated: tests link with `covers=` and their last run is the verification status |
| Characteristics of a requirement and of a set (§5.2.5, §5.2.6), language to avoid (§5.2.7) | `specdeck validate`, each finding citing its clause |
| Traceability (§6.6) | requirement → tests → status, both directions, in every run and in the run history |
| Software requirements specification (§9.6) | `specdeck srs`, generated from the requirements and the last run |

The tests are the evidence, the specification is generated from them on each run, so it
reports which requirements are verified *now* and cannot drift. specdeck's own
specification, written this way, is [SRS.md](SRS.md): 67 requirements, each traced to
the tests that verify it or declaring how else it is verified.

**What is claimed.** specdeck is *aligned with* 29148:2018, not fully conformant (§4.2
also covers life-cycle processes a tool cannot carry out). The generated SRS targets
*tailored conformance* (§4.5.2) to the content of §9.6 and declares its tailoring in the
document. [docs/ISO-29148.md](docs/ISO-29148.md) maps each clause to what specdeck does.
Clause numbers were checked against the published 2018 text; the descriptions here are
our own words, not the standard's.

**Who it's for and how it's used day to day:** [docs/SCENARIO.md](docs/SCENARIO.md) follows a
team whose coding agents write most of the code, from install to the pull request.

Under the requirements sits the scenario spec: a pytest plugin turns an ordinary suite
into `feature → groups → scenarios → Given/When/Then`, with a scenario dependency graph
and every scenario stamped with its real outcome. The YAML renders in its own viewer,
`specdeck validate` checks it with zero warnings, and a minimal sphinx-needs `needs.json`
makes the same graph reachable by traceability tooling and by coding agents.

## Why this direction (and not Gherkin)

Research before building (2026-09) was blunt: classic BDD fails because it's a
*second language nobody maintains*, step definitions explode, and `.feature`
files drift from the code — and product people never read them anyway.
Generating the spec from pytest removes the first three. What survives is the
warning that a derived spec "just restates the implementation"; specdeck answers
that with what Gherkin-first never had: **runtime-recorded steps** (a step is in
the spec only if it executed), **expected vs observed + decision context** on
failure, and a **pass/fail stamp** per scenario — the thing reverse-engineered
specs are criticized for lacking.

## Install & run

```bash
pip install -e .
pytest            # writes .specdeck/spec.yaml and .specdeck/needs.json
```

Zero configuration: every test becomes a scenario. Id from the function name,
group from the module, name from the docstring's first line, `logic` = the pytest
node id, `tags` include the group and `status:<outcome>`.

## Annotate for real steps

```python
from specdeck import scenario, given, when, then

@scenario("replay-offline", group="replay", depends_on=["capture-toy"], tags=["wedge"])
def test_replay_matches_recording(tape):
    with given("a tape captured from the toy gateway SDK"):
        player = TapePlayer(tape)
    with when("the handler is replayed with the network blocked"):
        out = replay(player)
    with then("the reply is the recorded one", expected="pong: !ping",
              decision_context=[("tape", "recorded_reply"), ("handler arg", "ignored")]):
        assert out.content == "pong: !ping"
```

- Steps record **as they execute** (context managers), so they're truthful.
- A failing `then` captures `expected_label` (yours) and `observed_label` (from
  pytest's assertion message) into the spec's `assertion` block, plus any
  `decision_context` — the DX failure format of test-failure-dx.
- `depends_on` accepts scenario ids or bare test function names.

## `depends_on` semantics — read this

`depends_on` is **documentation order** ("read/build these first; if every
scenario passes, the vision is delivered") plus **skip-if-a-prerequisite-failed**
(the pytest-dependency convention). It **never reorders execution** — runtime
coupling between tests is an anti-pattern, and specdeck won't help you create it.

Blocking cascades: if A fails, B (depends on A) is skipped as *blocked*, and so
is C (depends on B). The viewer shows all of them as blocked by A.

## Configuration (`pyproject.toml` / `pytest.ini`)

```toml
[tool.pytest.ini_options]
specdeck_feature = "tapedeck"
specdeck_description = "Record real SDK sessions to a tape; replay them offline."
specdeck_out = ".specdeck/spec.yaml"      # default
specdeck_needs = ".specdeck/needs.json"   # default
```

CLI: `--no-specdeck` (don't write), `--specdeck-out PATH`.

A partial run (a file, a node id, `-k`, `-m`, `--lf`) does not rewrite the spec,
so running one test never shrinks it to one scenario.

## Requirements (ISO/IEC/IEEE 29148:2018)

The generated spec says what the tests do. `requirements.yaml`, written by people, says
what they *should* show. Each entry is a requirement in the sense of 29148: one
"shall" statement (§5.2.4) with its attributes (§5.2.8), and concrete examples that make
it verifiable (§5.2.5).

```yaml
requirements:
  - id: pricing.bulk-tier                   # identification (§5.2.8.2)
    statement: An order of 100 units or more shall get 15% off.
    rationale: Agreed with sales, 2026-09.  # why it is necessary (§5.2.5)
    type: functional                        # §5.2.8.3; places it in the SRS (§9.6.11–9.6.18)
    priority: high                          # optional: priority, owner, risk, difficulty
    verification: test                      # §6.5.2: test, inspection, analysis, simulation, demonstration
    examples:
      - given: {qty: 100}
        expect: 15
      - given: {qty: 99}
        expect: 5
```

`type` is one of `interface`, `functional`, `usability`, `performance`,
`logical-database`, `design-constraint`, `standards-compliance`, `software-attribute`
(the sections of §9.6.11–9.6.18), or `process` and `human-factors` (§5.2.8.3).
`standard:` records the clause a requirement implements. The older keys `says` and `why`
are still read as `statement` and `rationale`.

Link tests to requirements, and take expected values from the examples so they come
from the requirement rather than from running the code:

```python
from specdeck import scenario, example, examples

@scenario(covers=["pricing.bulk-tier"])
def test_tiers():
    assert bulk_discount(100) == example("pricing.bulk-tier", qty=100)

@pytest.mark.parametrize("given, expected", examples("pricing.bulk-tier"))
def test_every_example(given, expected):      # one case per example
    assert bulk_discount(**given) == expected
```

Using an example counts as covering its requirement. Each requirement's verification
status is **met** (every covering scenario passes), **failing**, **blocked**, **not
run**, or **untested** (nothing covers it). Every run writes
`.specdeck/requirements.json`, prints `specdeck: requirements: 1 of 3 met, ...`, warns
about links to ids that don't exist and about problems in the file, and tags scenarios
`req:<id>` in the spec. The viewer leads with "requirements met" and has a Requirements
tab showing each entry, its examples (and which ones a passing test used), and the
scenarios behind it.

`example()` with inputs the requirement has no example for raises `RequirementError`,
listing the examples it does have. An explicit scenario id on a parametrized test
becomes one id per case (`tiers-qty-100`); depending on the id depends on every case.
Config: `specdeck_requirements` (default `requirements.yaml`),
`specdeck_requirements_out`.

### Quality, checked against the standard

`specdeck validate` checks each requirement and the set against what a tool can check of
29148, and cites the clause in each finding:

| Check | Clause |
|---|---|
| a binding requirement uses "shall"; positive, not "shall not"; active voice; not "shall be able to" | §5.2.4 |
| one capability per requirement (one "shall"); a rationale is recorded; a test or other method verifies it | §5.2.5, §5.2.8.2, §6.5.2 |
| no TBD/TBS/TBR; every test traces to a requirement; no links to requirements that do not exist | §5.2.6 |
| consistent: no requirement gives two expected values for the same inputs, and no linked test asserts a literal that differs from an example (reported at the test's line) | §5.2.6 |
| no superlatives, subjective or ambiguous terms, vague pronouns, open-ended terms, comparatives, loopholes or totality terms | §5.2.7 |
| attribute names, types and verification methods are the standard's | §5.2.8, §6.5.2 |

The word lists for §5.2.7 are specdeck's; the categories are the standard's. Findings
are warnings; `--strict` makes them errors. *Appropriate*, *feasible* and *correct*
(§5.2.5) need judgement and are listed for review in the SRS rather than checked.

### The adherence system: rules coding agents keep

specdeck's answer to "agents don't follow my CLAUDE.md" is a system of four parts, each
measured in `bench/rules_ab`:

| Part | What it is | What it did in the A/B |
|---|---|---|
| **Rules** | requirements typed `design-constraint`, `standards-compliance` or `process`, each with its reason | written down at all, rules cut breaks under pressure from 4 of 6 to 1 of 6 |
| **Checks** | the tests linked to each rule with `covers=` | caught every break they could see; under pressure agents got around them (2 of 2) until the policy below |
| **Adherence policy** | what keeping a rule means (`intent`, `conflicts` clauses) | no check got around in 6 of 6 pressured trials; agents said "Not done as asked:" in 5 of 6 |
| **Status** | which rules hold in the last run, in AGENTS.md and the run summary | a broken rule is named first, with its reason and the test to make pass |


```bash
specdeck agents            # updates AGENTS.md; --out CLAUDE.md, or - for stdout
```

An agent instructions file is mostly rules. In specdeck they are requirements like any other,
typed as the standard types them: `design-constraint` (§9.6.16, decisions that limit how the
code may be built — the rationale is the decision record, the test is the check that fails when
it is broken), `standards-compliance` (§9.6.17) and `process` (§5.2.8.3 — the Definition of
Done). `specdeck agents` writes each into the instructions file with its rationale, the tests
that verify it and whether it held in the last run, names any broken rule again under
"Before you finish", and tells the agent to check by hand the rules verified by inspection or
demonstration. Only a marked block is replaced, so the hand-written part of the file is kept.
specdeck's own [AGENTS.md](AGENTS.md) is generated this way.

Above the rules goes the **adherence policy**, the part of the system that says what keeping a
rule means. specdeck ships two default clauses, each measured in `bench/rules_ab`. `intent` says that getting a rule's test to
pass without keeping the rule breaks the rule; under pressure, agents had been importing pandas
through `importlib` to get past a stdlib-only test. `conflicts` says that when a request asks for
something a rule forbids, the agent keeps the rule and starts its summary with "Not done as asked:". A project rewords, drops or adds clauses:

```yaml
adherence:
  conflicts: If a request conflicts with a rule, stop and ask before going on.   # reword
  intent: false                                                                     # drop
  review: Leave a TODO naming the rule wherever you were unsure.                   # add
```

`adherence: false` turns it off. The defaults live in `specdeck/adherence.py`, with the reason
and the measurement behind each clause.

### The specification

```bash
specdeck srs --out SRS.md
```

writes the software requirements specification with the content of §9.6, in the
standard's order: the introduction (§9.6.1–9.6.9), the specified requirements grouped by
type (§9.6.11–9.6.18) with their attributes and verification status, verification
(§9.6.19) as a traceability matrix from requirement to tests to the last result, the
quality findings, and supporting information (§9.6.20). Purpose, scope, users,
limitations and the other sections only people can write come from an `srs:` block in
`requirements.yaml`; a section left empty is marked *to be supplied (TBS)* rather than
hidden, so an incomplete set shows (§5.2.6). See specdeck's own `requirements.yaml` for
an example of the block.

## The live viewer

```bash
pytest --specdeck-live          # record this run and watch it at http://127.0.0.1:7357/p/<project>
```

The first live run starts the viewer in the background; later runs, from any
project, join the same viewer and get their own page. To record every run
without the flag, set `specdeck_live = true` (or `record` to keep history
without starting the viewer). `python -m specdeck serve` runs it by hand.

What it shows:

- **Summary.** How many scenarios pass, as a bar split by status, with what
  changed since the previous run (newly passing, regressions, new scenarios)
  and a trend over recent complete runs.
- **Overview.** Scenarios grouped by feature (module), each with its status,
  its error and how much work it holds up. The side panel answers "where is it
  stuck": *Stuck at* lists root-cause failures (failing, with every prerequisite
  passing), *Goals* shows each end of a dependency branch with progress over
  everything it relies on, *Load-bearing* lists what most scenarios depend on.
- **Graph.** The `depends_on` DAG, coloured by status. Selecting a node
  highlights everything it needs and everything that needs it. Built to stay
  readable on big suites:
  - scenarios with no dependencies aren't drawn (they're in the Overview; one
    click shows them), and the cases of a parametrized test are one node;
  - each chain is laid out on its own and chains are packed under the feature
    they belong to; chains that mix features sit under *Across features*;
  - above 150 nodes it opens on a **feature map**, one node per feature, with
    arrows counting the dependencies between features; click one to open it;
  - **Problems** draws only failing and blocked scenarios, **Selected branch**
    only what the selected scenario needs and what needs it;
  - a searchable feature picker, a minimap, scroll to move and Ctrl+scroll to
    zoom; zoomed far out, nodes turn into status tiles.
- **History.** Every run with its source (terminal or viewer), branch and
  commit, result and change. Click a run to see the suite as it was then.
- **Live.** During a run the page updates by itself: the running test spins,
  tests not reached yet keep their last result, faded.
- **Scenario drawer.** Steps with expected and observed values, where a failure
  stopped, dependencies both ways, the scenario's own pass/fail history, its
  source, and a command to run it.
- **Run from the page.** *Run all*, *Run failing* (failing and blocked) or one
  scenario. Runs of a subset update those scenarios and keep the rest.
- **Light editing.** Edit a scenario's title, details, tags, `depends_on`,
  step text and expected labels. Only string literals are rewritten, you review
  a diff before saving, and an edit made against a test that changed on disk is
  refused. Text built with f-strings stays read-only.

History lives in `~/.specdeck/history.db` (SQLite; `SPECDECK_HOME` moves it).
The viewer uses only the standard library, listens on 127.0.0.1 only, refuses
requests whose Host isn't loopback, and needs a per-page token for anything that
writes. Keyboard: `/` search, `j`/`k` move, `1` `2` `3` tabs, `Esc` close.

| Option | Meaning |
|---|---|
| `--specdeck-live` | record and make sure a viewer is running |
| `--specdeck-record` | record only |
| `--no-specdeck-live` | override `specdeck_live` from config |
| `--specdeck-port N` / `specdeck_port` / `SPECDECK_PORT` | viewer port, default 7357 |

## Output

```yaml
# GENERATED by specdeck 0.1.0 from pytest on 2026-09-23T… — do not edit.
feature: tapedeck
groups:
- {id: replay, name: Replay}
scenarios:
- id: replay-offline
  name: Replay matches recording
  group: replay
  tags: [wedge, replay, 'status:passed']
  depends_on: [capture-toy]
  steps:
  - {keyword: Given, text: a tape captured from the toy gateway SDK}
  - {keyword: When, text: the handler is replayed with the network blocked}
  - keyword: Then
    text: the reply is the recorded one
    assertion: {expected_label: 'pong: !ping', observed_label: 'pong: !ping'}
  logic: [pytest tests/test_toy_roundtrip.py::test_replay_matches_recording]
```

Every string goes through `yaml.safe_dump`, so `key: value` text inside a step can
never produce an unparseable file (the bug that silently broke the hand-written
specs this replaces).

## For a coding agent

```bash
pytest --specdeck-coverage          # also records which scenarios run which source functions
specdeck context --out SUITE.md     # one document describing the suite
```

What goes in that document was decided by measurement, not taste. `bench/agent_ab`
puts the generated spec against the raw test files, same model, same questions,
ground truth from applying each change and running the suite. Two findings shaped it:

- **It states what `depends_on` means.** Asked to sort real failures from ones merely
  blocked by a failed prerequisite, every arm scored 40% exact — what the *obvious*
  rule scores. The obvious rule is wrong: specdeck never reorders tests, so a
  prerequisite listed later cannot block anything. Saying so, in about 100 tokens,
  took both the spec and the raw test files to **100%**.
- **It names the code.** The spec describes behaviour without naming a single
  function, so an agent holding a diff cannot connect `file.py:31` to any scenario.
  With a coverage map — which scenarios execute which functions, import-time lines
  excluded — "which tests does this change break" went from 0.80 to 0.88 F1 and from
  25 missed failures to 4 (the raw test files: 0.93 and 2), for fewer tokens than
  those files cost.

What it deliberately does *not* claim is whether a test would **notice** a change;
running a line is not checking it. That is `proofdeck check`, and it is the one
question no amount of reading material settles.

Coverage recording is off by default and needs `coverage.py`; it costs about a second
on a suite of 38 tests. Without it `specdeck context` still writes the rest.

### On a large suite

Two things in the document exist only because a bigger suite (88 tests) was measured
and a naive version of it made an agent *worse*:

- **A function most of the suite runs is reported as `most (47 of 88)`, not as a list.**
  Handed that list, the agent answered "which tests does this change break" with 87 test
  names where the true answer was none; the same agent without the map named 9. The list
  is identical for every change in that function — noise shaped like evidence. Collapsing
  it cut the wrong names across the whole benchmark from 97 to 18, matching what the raw
  test files produce, with no loss of correct answers.
- **The map is a bound, and the document says so.** The scenarios listed against a
  function are the only ones that can fail when you change it; a function absent from the
  map is run by nobody. Stating that cut impossible answers — tests named that do not even
  execute the changed function — from 43 to 5.

```bash
specdeck context --for specdeck/ci.py,specdeck/trust.py --out SUITE.md
```

`--for` takes the files a change touches. Scenarios that run none of them are kept in
brief — id, name, dependencies, node id, no steps — because `depends_on` reaches across
the whole suite and an invisible prerequisite is worse than a terse one. For a change in
a leaf module that is about a third of the full document; for a change in the module every
test exercises it is most of it, which is the honest answer.

### What a test is for

```bash
specdeck context --task why --out WHY.md   # the requirements alone: statement, rationale, and the tests linked to each
```

The reasons behind a test are not in the test. With comments and docs stripped from
specdeck's own repo (`bench/purpose`), an agent asked why tests exist scored 10/20 from
the tests alone and 14/20 with a prose requirements document. With the document's earlier
version pasted in — requirement ids as tags, but not what they say or why — it scored 6/10.
So the document now carries a **Requirements** section: each requirement's statement, rationale and
status, the tests linked to it, and beside each test any other requirement it also covers.
It is part of the default document; `--task why` sends it alone (about 2k tokens here) and
scored 29/30 over three sessions, the full document on disk 27/30.

## In the pull request

The viewer is for the desk. For the review, `specdeck report` writes one
Markdown document from the files a run leaves behind: how many scenarios pass,
the root causes to fix first, who is blocked by them, and every requirement
with its status.

```bash
pytest -q                                   # writes .specdeck/spec.yaml + requirements.json
proofdeck check --base main --format json --no-gate > proof.json
specdeck report --proofdeck proof.json --out .specdeck/report.md
```

With proofdeck's JSON, the report answers the question neither tool can alone:
is a *met* requirement **proven, or just passing?** specdeck knows what each
test is for (`covers=`) and that it passes; proofdeck knows which tests
executed each changed line, whether any of them noticed the line being wrong,
and what each test replaces with fakes. Joined on the pytest node id, every met
requirement gets a verdict for this change:

| verdict | meaning |
|---|---|
| ✔ checked | every changed line its tests ran was noticed, by a test that fakes nothing |
| ✖ unproven | a covering test ran a changed line and did **not** notice its mutant |
| ≈ mock-only | changed lines were only noticed through fakes |
| · untouched | the change didn't reach anything the covering tests run |

A requirement whose every covering test fakes a collaborator is flagged
regardless of the change. `specdeck trust proof.json` prints just this join;
`--fail-on-untrusted` gates on it, and `specdeck report` takes the same flag
plus `--fail-on-failing` and `--fail-on-unmet`. Nothing fails until asked.

```yaml
# .github/workflows/specdeck.yml
- run: pip install -e . specdeck proofdeck
- run: pytest -q
- run: proofdeck check --base origin/${{ github.base_ref }} --format json --no-gate > proof.json
- run: specdeck report --proofdeck proof.json --out report.md --fail-on-untrusted
- if: always()
  run: gh pr comment ${{ github.event.pull_request.number }} --body-file report.md
  env: { GH_TOKEN: "${{ github.token }}" }
```

## Commands

```bash
specdeck serve                  # the viewer, on 127.0.0.1
specdeck validate               # spec.yaml + requirements.yaml, quality against 29148; non-zero on error
specdeck srs --out SRS.md       # the software requirements specification (29148 §9.6)
specdeck requirements           # the requirements report, --fail-on-unmet for CI
specdeck trust proof.json       # met, or just passing? joins requirements with proofdeck's JSON
specdeck report --proofdeck proof.json --out report.md   # Markdown for a PR comment
specdeck context --out SUITE.md    # the suite described for a coding agent
specdeck agents                 # the project's rules into AGENTS.md, with their status
specdeck watch -- -q            # re-run the suite when a file changes
specdeck runs                   # recorded runs, newest first
specdeck diff 7 12              # what moved between two runs
specdeck prune --keep 200       # bound the shared history
specdeck forget <project>       # drop a project entirely
specdeck where                  # the history database path
```

### Gates (opt-in)

Nothing changes a suite's exit code until you ask. By flag or in config:

```toml
[tool.pytest.ini_options]
specdeck_fail_on_unmet = "true"            # a requirement nothing proves
specdeck_fail_on_unknown_covers = "true"   # covers= names a requirement that doesn't exist
specdeck_fail_on_invalid = "true"          # the generated spec has validation errors
```

specdeck runs these on itself.

## Relation to the other projects

specdeck stands alone: it generates the spec and the requirements specification,
validates them, and renders them.

- **tapedeck** records real SDK traffic so tests replay ground truth instead of a
  guess. The first suite specified in specdeck.
- **proofdeck** asks whether the tests would notice if the code were wrong, and
  flags tests that only prove the code agrees with itself. specdeck says what is
  covered; proofdeck says whether that coverage means anything; `specdeck trust`
  joins the two so a requirement can be *met* without being *proven* — and say so.
- **test-failure-dx** is where the YAML format came from; specdeck emits it, but
  *pytest is the runner*, so `logic` is the node id to re-run.
- **dx-spec-viewer** is superseded — `specdeck serve` replaces it.
