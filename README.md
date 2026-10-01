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

## Commands

```bash
specdeck serve                  # the viewer, on 127.0.0.1
specdeck validate               # lint spec.yaml + requirements.yaml; non-zero on error
specdeck requirements           # the requirements report, --fail-on-unmet for CI
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
  covered; proofdeck says whether that coverage means anything.
- **test-failure-dx** is where the YAML format came from; specdeck emits it, but
  *pytest is the runner*, so `logic` is the node id to re-run.
- **dx-spec-viewer** is superseded — `specdeck serve` replaces it.
