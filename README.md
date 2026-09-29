# specdeck

**The spec is generated from the tests. It cannot drift, and it cannot lie.**

A pytest plugin that turns an ordinary test suite into a *test-failure-DX* spec —
`feature → groups → scenarios → Given/When/Then`, with a scenario dependency DAG
and every scenario stamped with its real outcome. The YAML it writes renders in
[dx-spec-viewer](../dx-spec-viewer) unchanged and validates with zero warnings.
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
  highlights everything it needs and everything that needs it.
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

## Relation to the other projects

- **test-failure-dx** defines the format and a runner; specdeck emits the format
  but *pytest is the runner*, so `logic` is the node id to re-run.
- **dx-spec-viewer** renders the output; point its `SPECS_DIR` at `.specdeck/` or
  copy `spec.yaml` into its `specs/`. The `status:*` tags are filterable in the
  Workspace Atlas.
- **tapedeck** is the first suite specified this way.
