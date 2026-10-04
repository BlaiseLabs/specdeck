# specdeck

**The spec is generated from the tests. It cannot drift, and it cannot lie.**

A pytest plugin that turns an ordinary test suite into a *test-failure-DX* spec —
`feature → groups → scenarios → Given/When/Then`, with a scenario dependency DAG
and every scenario stamped with its real outcome. The YAML it writes renders in
its own viewer, and `specdeck validate` checks it with zero warnings.
It also writes a minimal sphinx-needs `needs.json`, so the same graph is reachable
by requirements/traceability tooling and by coding agents.

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

## Requirements: what the tests should show

The generated spec says what the tests do. `requirements.yaml`, written by
people, says what they *should* show: one sentence of intent per entry, plus
concrete examples.

```yaml
requirements:
  - id: pricing.bulk-tier
    says: Orders of 100 units or more get 15% off; 10 or more get 5%.
    why: Agreed with sales, 2026-09.     # optional
    examples:
      - given: {qty: 100}
        expect: 15
      - given: {qty: 99}
        expect: 5
```

Link tests to entries, and take expected values from the examples so they come
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

Using an example counts as covering its requirement. Each requirement is
**met** (every covering scenario passes), **failing**, **blocked**, **not run**,
or **untested** (nothing covers it). Every run writes
`.specdeck/requirements.json`, prints `specdeck: requirements: 1 of 3 met, ...`,
warns about links to ids that don't exist and about problems in the file, and
tags scenarios `req:<id>` in the spec. The viewer leads with "requirements met"
and has a Requirements tab showing each entry, its examples (and which ones a
passing test used), and the scenarios behind it.

`example()` with inputs the requirement has no example for raises
`RequirementError`, listing the examples it does have. An explicit scenario id
on a parametrized test becomes one id per case (`tiers-qty-100`); depending on
the id depends on every case. Config: `specdeck_requirements` (default
`requirements.yaml`), `specdeck_requirements_out`.

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
specdeck context --out AGENTS.md    # one document describing the suite
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
specdeck context --for specdeck/ci.py,specdeck/trust.py --out AGENTS.md
```

`--for` takes the files a change touches. Scenarios that run none of them are kept in
brief — id, name, dependencies, node id, no steps — because `depends_on` reaches across
the whole suite and an invisible prerequisite is worse than a terse one. For a change in
a leaf module that is about a third of the full document; for a change in the module every
test exercises it is most of it, which is the honest answer.

### What a test is for

```bash
specdeck context --task why --out WHY.md   # the requirements alone: says, why, and the tests linked to each
```

The reasons behind a test are not in the test. With comments and docs stripped from
specdeck's own repo (`bench/purpose`), an agent asked why tests exist scored 10/20 from
the tests alone and 14/20 with a prose requirements document. With the document's earlier
version pasted in — requirement ids as tags, but not what they say or why — it scored 6/10.
So the document now carries a **Requirements** section: each requirement's says, why and
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
specdeck validate               # lint spec.yaml + requirements.yaml; non-zero on error
specdeck requirements           # the requirements report, --fail-on-unmet for CI
specdeck trust proof.json       # met, or just passing? joins requirements with proofdeck's JSON
specdeck report --proofdeck proof.json --out report.md   # Markdown for a PR comment
specdeck context --out AGENTS.md   # the suite described for a coding agent
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

specdeck stands alone: it generates the spec, validates it, and renders it.

- **tapedeck** records real SDK traffic so tests replay ground truth instead of a
  guess. The first suite specified in specdeck.
- **proofdeck** asks whether the tests would notice if the code were wrong, and
  flags tests that only prove the code agrees with itself. specdeck says what is
  covered; proofdeck says whether that coverage means anything; `specdeck trust`
  joins the two so a requirement can be *met* without being *proven* — and say so.
- **test-failure-dx** is where the YAML format came from; specdeck emits it, but
  *pytest is the runner*, so `logic` is the node id to re-run.
- **dx-spec-viewer** is superseded — `specdeck serve` replaces it.
