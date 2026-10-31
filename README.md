# specdeck

Enforce your CLAUDE.md and AGENTS.md rules with tests.

A requirements-based tool for enforcing AGENTS.md and CLAUDE.md rules.

Take a small invoice tool built by Claude Code. One of its rules says money has to be Decimal, and another says an issued invoice never changes. In a scratch copy, floats were put in the money code on purpose and an issued invoice was edited. Every test still passed.

```
specdeck: rule money.decimal broken: invoicing/money.py:15: return to_cents(Decimal(str(float(quantity) * float(unit_price))))
specdeck: rule issued.append-only broken: invoices/issued/INV-0005.json changed
139 passed in 1.44s
```

That gap is why specdeck exists. All 139 tests were green and two rules were broken, and those two lines are specdeck reporting it.

CLAUDE.md and AGENTS.md are just text that gets pasted into the agent's context. Agents follow them some of the time, and nothing tells you when they didn't. With specdeck each rule gets a reason and a small check. Every pytest run checks your rules against the change, names the one that broke with its file and line, and puts it at the top of CLAUDE.md so the next session sees it first.

It's Python and pytest only for now, and it isn't on PyPI yet, so you install it from a clone.

```bash
pip install -e .
pytest                          # every rule checked against your change
specdeck breaks                 # how often each rule has broken
specdeck agents                 # the rules and their status, written into CLAUDE.md and AGENTS.md
```

## What a rule looks like

Rules live in `requirements.yaml`.

```yaml
requirements:
  - id: money.decimal
    statement: Money amounts shall be Decimal values.
    rationale: A float refund came out a cent short and the accounts would not reconcile.
    type: design-constraint
    check: {forbid_pattern: '\bfloat\(', paths: ['shop/**']}
```

Most rules only need a pattern or a list of paths. 555 real rules were sampled from public projects and curated lists, and about half could be checked from the code or the change alone. About 30% can't be checked at all, like "follow best practices". specdeck won't help with those.

Here's what you can use as a check.

| Check | Breaks when |
|---|---|
| `forbid_pattern` | a line matches, by default only lines the change added |
| `forbid_import` | a Python file imports a banned module, including through `importlib.import_module("x")` |
| `protect` | the change touches a matching file |
| `append_only` | the change edits or deletes an existing file, while adding new ones is fine |
| `co_change` | the change touches A but not B |
| `no_new_dependencies` | a dependency is added to pyproject.toml, a requirements file or package.json |
| `ruff` | a ruff rule fires, such as `DTZ005` for a naive `datetime.now()` |
| `test` | a test you point at didn't pass |
| `command` | a shell command fails, for local rules only and never shared |

You can combine them with `all`, `any`, `not` and `when`. A check that can't run, like no git repo or ruff missing, gets reported as an error and never counts as a break. The details are in [docs/RULE-CHECKS.md](docs/RULE-CHECKS.md).

By default the checks look at your uncommitted work, which is usually what an agent just did. In CI you point them at a branch with `--specdeck-check-base origin/main`. `specdeck check --base origin/main --fail` runs the checks alone without the tests.

A line in CLAUDE.md only has the first row of this table.

| | A line in CLAUDE.md | A rule in specdeck |
|---|---|---|
| What must hold | yes | `statement` |
| Why | rarely, 12% of those 555 rules | `rationale` |
| How you'd know it broke | no | a `check:` or a linked test |
| Whether it holds now | no | from the last run |
| Where it broke, and how often | no | file and line, and `specdeck breaks` |

## Why not just use hooks?

Hooks are great and you should keep them. They do a different job.

| | Hooks | specdeck |
|---|---|---|
| When | While the agent works, and they can block it | On each test run, in CI, or when you ask |
| Who it covers | Mainly one agent's tool calls | Any change, from any agent or person |
| What it sees | Each action as it happens | The whole change, like A changing without B, or a new dependency |
| What it leaves behind | A block or a warning | The rule's reason, its status and its break history |

Hooks stop the action. specdeck tells you whether the rule still holds. `specdeck check --fail` should be callable from a hook too, though that setup hasn't been tested yet.

To be clear about what this doesn't do, specdeck doesn't stop an agent from breaking a rule. It catches the break afterwards. An agent that never runs the tests won't see anything, so CI running `pytest` is what makes it stick.

## Start with the purpose

A project's rules make more sense next to what the project is for, so `requirements.yaml` can open with that too.

```yaml
srs:
  purpose: Take orders for the shop and charge them correctly, in English and Japanese.
```

The purpose goes at the top of the block specdeck writes into CLAUDE.md and AGENTS.md, and each rule's reason sits next to the rule. An agent that knows why a rule or a test exists is better placed to tell a real failure from an outdated test, and to make the call you would have made when a request and a rule disagree.

In the invoice tool's sessions, the owner's request "just use floats" came back like this.

> Not done as asked: I didn't switch to floats. The project rule `money.decimal` says "The tool shall hold and compute money amounts as Decimal values," and `float` is blocked in `invoicing/**` by a pattern check and by `tests/test_money.py`.

## What gets written for the agent

`specdeck agents` writes into whichever of CLAUDE.md and AGENTS.md exist, into both if both do, and into a new AGENTS.md if neither does. `--out FILE` picks the file yourself. Only a marked block gets replaced, so whatever you wrote by hand stays.

Claude Code only reads AGENTS.md when there's no CLAUDE.md, which is why it writes to both.

The rules in that block are the requirements typed `design-constraint`, `standards-compliance` or `process`. Each one comes with its reason, how it's checked and whether it held in the last run. Rules that nothing can check tell the agent to check them by hand.

Above the rules sits the adherence policy, which says what keeping a rule means. It has two default clauses.

The first is `intent`. Under pressure, agents in testing read a stdlib-only rule test and then loaded pandas through `importlib` so the test wouldn't see it. The test stayed green while the rule was broken. This clause says that getting a test to pass without keeping the rule is breaking the rule. Without it 2 of 2 agents got around the check, and with it all 8 kept the rule.

The second is `conflicts`. When a request asks for something a rule forbids, the agent keeps the rule and starts its summary with "Not done as asked". That wording got 5 of 6 agents to say plainly what they didn't do, against 0 of 6 with no clause.

These are small numbers, so read them as a direction. You can change the policy.

```yaml
adherence:
  conflicts: If a request conflicts with a rule, stop and ask before going on.   # reword
  intent: false                                                                     # drop
  review: Leave a TODO naming the rule wherever you were unsure.                   # add
```

`adherence: false` turns it off.

specdeck's file was also tested against a hand-written one, to see which keeps agents to the rules better, and it doesn't win. Under pressure, breaks fell from 4 of 6 with no rules to 1 of 6 with plain bullets, and specdeck did no better while using 1.3 to 1.7 times the tokens. Writing rules down is what prevents breaks. What specdeck adds is knowing when one happened. The experiments are in `bench/rules_ab`.

## Sharing rules

A rule with its check can be shared through a registry, which is just a git repo with one YAML file per rule. Hosting one means hosting a repo.

```bash
specdeck rules init ~/rules-registry
specdeck rules publish money.decimal --registry ~/rules-registry    # commits it, then you push
specdeck rules search money --registry <folder or git URL>
specdeck rules add money.decimal --registry <folder or git URL>     # into requirements.yaml, with its check and source
```

Only rules with a reason and a check made of plain data can be published, so adding someone's rule never runs their code. The rule's text does end up in your agent's instructions though, so read it before you commit it. There's more in [docs/REGISTRY.md](docs/REGISTRY.md), and seven starter rules in `examples/registry`.

## Built on requirements engineering

If you're wondering why rules are called requirements here, it's because a rule really is one. Requirements engineering has spent decades working out what a requirement needs before you can rely on it, so specdeck builds on that instead of inventing a new format.

The model follows ISO/IEC/IEEE 29148:2018, the international standard that replaced IEEE 830. specdeck is aligned with it, not fully conformant, because full conformance also covers life-cycle processes a tool can't carry out. The clause numbers were checked against the published text, and the descriptions aren't taken from the standard's text.

| The standard | specdeck |
|---|---|
| Requirement statement using "shall" (§5.2.4) | `statement` |
| Attributes like rationale, type, priority, owner, risk and difficulty (§5.2.8) | the same names in `requirements.yaml` |
| Verification by test, inspection, analysis or simulation, and demonstration (§6.5.2) | `verification`, where test is automated and the others are recorded as declared |
| Quality of a requirement and of a set, and language to avoid (§5.2.5 to §5.2.7) | `specdeck validate`, with each finding citing its clause |
| Traceability (§6.6) | requirement to tests to status, on every run |
| Software requirements specification (§9.6) | `specdeck srs` |

`specdeck validate` flags things like a requirement with no "shall", a vague word like "fast", two requirements in one, a missing reason, or a test linked to nothing. It also flags a requirement that gives two answers for the same input, and a linked test that asserts a different number from an example. Findings are warnings unless you pass `--strict`. Whether a requirement is appropriate, feasible and correct takes judgement, so those are listed for review and not checked.

`specdeck srs --out SRS.md` writes the full specification when a client or an auditor needs one, with a table from each requirement to its tests to the last result. Sections only people can write come from the `srs:` block, and an empty one is marked as to be supplied so the gap stays visible. The generated document aims at tailored conformance to §9.6 and says so. specdeck's own is [SRS.md](SRS.md), with 80 requirements, and [docs/ISO-29148.md](docs/ISO-29148.md) maps each clause to what the tool does.

## Linking tests to requirements

Business requirements work the same way, and examples are where they get useful.

```yaml
  - id: pricing.bulk-tier
    statement: An order of 100 units or more shall get 15% off.
    rationale: Agreed with sales, 2026-09.
    type: functional
    examples:
      - {given: {qty: 100}, expect: 15}
      - {given: {qty: 99}, expect: 5}
```

```python
from specdeck import scenario, example, examples

@scenario(covers=["pricing.bulk-tier"])
def test_tiers():
    assert bulk_discount(100) == example("pricing.bulk-tier", qty=100)

@pytest.mark.parametrize("given, expected", examples("pricing.bulk-tier"))
def test_every_example(given, expected):
    assert bulk_discount(**given) == expected
```

The expected value comes from the requirement, not from running the code. An expected number copied from what the code printed passes even when the code is wrong.

Each requirement ends up met, failing, blocked, not run or untested. `specdeck requirements` prints the list. `example()` with inputs the requirement has no example for raises `RequirementError` and lists the ones it does have. An id on a parametrized test becomes one id per case, and depending on that id means depending on every case.

## The spec it generates

With zero configuration, every test becomes a scenario in `.specdeck/spec.yaml`, with its id from the function name, its group from the module, and its name from the first sentence of the docstring. You also get `.specdeck/needs.json` for sphinx-needs tooling.

If you want real steps, you annotate them.

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

Steps are recorded as they run, so a step is only marked as done if it actually executed. A failing `then` keeps what you expected, what it saw, and any decision context.

specdeck went this way instead of Gherkin because classic BDD tends to fail for the same reason every time. It's a second language nobody maintains, and the `.feature` files drift from the code. Generating the spec from pytest removes that.

`depends_on` is about reading order. It says read these first, and it skips a test if a prerequisite failed. It never reorders execution, because tests that have to run in order pass for the wrong reasons. If A fails then B, which depends on A, is skipped as blocked, and so is C, which depends on B.

A partial run (a file, a node id, `-k`, `-m`, `--lf`) doesn't rewrite the spec or check the rules, so running one test never shrinks the spec down to one scenario. `--specdeck-merge` updates just the scenarios that ran, and `--no-specdeck` writes nothing.

```toml
[tool.pytest.ini_options]
specdeck_feature = "tapedeck"
specdeck_description = "Record real SDK sessions to a tape; replay them offline."
specdeck_out = ".specdeck/spec.yaml"            # default
specdeck_needs = ".specdeck/needs.json"         # default
specdeck_requirements = "requirements.yaml"     # default
specdeck_check_base = "HEAD"                    # default
specdeck_breaks_out = ".specdeck/breaks.jsonl"  # default
```

## The viewer

```bash
pytest --specdeck-live    # record this run and watch it at http://127.0.0.1:7357/p/<project>
```

The first live run starts the viewer in the background, and later runs from any project join it with their own page. `specdeck_live = true` records every run, `record` keeps history without starting the viewer, and `python -m specdeck serve` runs it by hand.

* The summary shows how many scenarios pass, what changed since the last run, and the trend.
* The overview groups scenarios by feature. A side panel lists the root-cause failures to fix first, each goal with its progress, and the scenarios most others depend on.
* The graph draws the `depends_on` links, colored by status. Above 150 nodes it opens on one node per feature, so big suites stay readable.
* The history lists every run with its branch and commit, and clicking one shows the suite as it was then.
* During a run the page updates by itself, and tests not reached yet keep their last result, faded.
* Each scenario opens a drawer with its steps, expected and observed values, dependencies, history and source.
* You can run all tests, the failing ones, or a single scenario from the page.
* You can edit a scenario's title, tags, `depends_on`, step text and expected labels. Only string literals get rewritten, you review a diff first, and an edit against a test that changed on disk is refused.

History lives in `~/.specdeck/history.db`, and `SPECDECK_HOME` moves it. The viewer uses only the standard library, listens on 127.0.0.1 only, refuses requests whose Host isn't loopback, and needs a per-page token for anything that writes.

| Option | Meaning |
|---|---|
| `--specdeck-live` | record and make sure a viewer is running |
| `--specdeck-record` | record only |
| `--no-specdeck-live` | override `specdeck_live` from config |
| `--specdeck-port N`, `specdeck_port`, `SPECDECK_PORT` | viewer port, default 7357 |

## Describing the suite to an agent

```bash
pytest --specdeck-coverage         # also records which scenarios run which functions
specdeck context --out SUITE.md    # one document describing the suite
```

What goes in this document was decided by measurement. `bench/agent_ab` puts the generated spec against the raw test files, with the same model and questions, and the right answers come from applying each change and running the suite.

It states what `depends_on` means. Asked to sort real failures from ones merely blocked, everything scored 40% exact, because the obvious rule is wrong. Saying so in about 100 tokens took both materials to 100%.

It names the code. A spec describes behavior, so an agent holding a diff can't connect it to a scenario. With a map of which scenarios run which functions, "which tests does this change break" went from 0.80 to 0.88 F1 and from 25 missed failures to 4. The raw test files scored 0.93 and 2, for more tokens.

A function most of the suite runs is reported as a count and not a list. Handed the list, an agent named 87 tests where the true answer was none. Collapsing it cut wrong names across the benchmark from 97 to 18. Saying the map is a bound cut impossible answers from 43 to 5.

`--for specdeck/ci.py,specdeck/trust.py` writes it for the files a change touches. For a change in a leaf module that's about a third of the full document.

`specdeck context --task why` sends only the requirements with their reasons and tests, about 2k tokens here. With comments and docs stripped from the repo, an agent asked why tests exist scored 10 of 20 from the tests alone, and this document scored 29 of 30. `specdeck context --profiles` lists the other arrangements with what each one measured.

This doesn't say whether a test would notice a change, since running a line isn't checking it. That's what proofdeck is for.

Coverage recording is off by default and needs `coverage.py`.

## In the pull request

`specdeck report` writes one Markdown document for a PR comment, with how many scenarios pass, what to fix first, what's blocked, and every requirement with its status.

```bash
pytest -q
proofdeck check --base main --format json --no-gate > proof.json
specdeck report --proofdeck proof.json --out .specdeck/report.md
```

With proofdeck's JSON it can tell you whether a met requirement is proven or just passing.

| Verdict | Meaning |
|---|---|
| checked | every changed line its tests ran was noticed, by a test that fakes nothing |
| unproven | a covering test ran a changed line and didn't notice it being broken |
| mock-only | changed lines were only noticed through fakes |
| untouched | the change didn't reach anything its tests run |

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

Adding specdeck never changes your exit code until you ask it to.

```toml
[tool.pytest.ini_options]
specdeck_fail_on_unmet = "true"            # a requirement nothing proves
specdeck_fail_on_unknown_covers = "true"   # covers= names a requirement that doesn't exist
specdeck_fail_on_invalid = "true"          # the generated spec has validation errors
```

## Commands

```bash
specdeck check --fail           # the rules' checks against the change, no tests
specdeck breaks                 # how often each rule broke
specdeck agents                 # the rules into CLAUDE.md and AGENTS.md
specdeck validate               # the spec and requirements, checked against 29148
specdeck requirements           # the requirements report
specdeck srs --out SRS.md       # the full specification
specdeck rules search money     # find shared rules
specdeck rules add ID           # add one, with its check and source
specdeck context --out SUITE.md # the suite described for an agent
specdeck report --out report.md # Markdown for a PR comment
specdeck trust proof.json       # met, or just passing?
specdeck serve                  # the viewer
specdeck watch -- -q            # re-run the suite when a file changes
specdeck runs                   # recorded runs
specdeck diff 7 12              # what moved between two runs
specdeck prune --keep 200       # bound the shared history
specdeck forget <project>       # drop a project
specdeck where                  # the history database path
```

## The other two tools

specdeck works on its own, and it's one of three.

[tapedeck](../tapedeck) records real SDK traffic so tests replay real responses. [proofdeck](../proofdeck) asks whether your tests would notice if the code were wrong. `specdeck trust` joins proofdeck with specdeck, so a requirement can be met without being proven and say so.

For a longer look at how a team might use all this, there's [docs/SCENARIO.md](docs/SCENARIO.md).
