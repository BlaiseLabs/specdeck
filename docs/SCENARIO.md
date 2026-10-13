# Who specdeck is for, and how they use it

## The team

Dana leads a team of four that builds the order and billing backend for an online shop: a
FastAPI service on Postgres that takes orders, charges cards through Stripe, issues refunds and
sends receipts in English and Japanese. It is a few years old, has about 600 pytest tests, lives
on GitHub, and runs CI on GitHub Actions.

Most new code is now written by coding agents: Claude Code in the terminal, Codex on some
tickets, Copilot in the editor. A developer writes the ticket, the agent writes the change and
the tests, a person reviews the pull request.

The team already has a `CLAUDE.md` with a dozen "never" lines, each added after an agent did
something costly once: never use float for money, never call Stripe outside `payments/gateway.py`,
never add a dependency without asking, timestamps in UTC, every customer-facing string in both
languages. Two of the lines have a script in CI that checks them. Nobody is sure the rest still
hold.

## What hurts

- **Review is the bottleneck.** Pull requests are large, and the reviewer cannot read every
  line. They approve on "the tests pass".
- **The tests pass, and the code is still wrong.** Last month an agent wrote a refund with float
  arithmetic, and its tests expected the float result. Both were green, and one cent was missing.
- **The rules drift.** `CLAUDE.md` says one thing, the scripts check another, and when a rule
  breaks nobody can tell from the file.
- **Agents quietly do something other than what was asked.** A developer asked for pandas in an
  export; the agent used `csv` without saying so, and the developer found out in review. The
  opposite happens too: an agent did what was asked and broke a rule nobody had told the
  developer about.
- **"Is partial refund implemented?"** The product owner asks. The answer means reading code.

## Setting it up (an afternoon)

specdeck is a pytest plugin and a command line. It needs Python 3.10+, pytest and PyYAML.

```bash
pip install specdeck            # today: pip install -e <path to a clone>; not on PyPI yet
pytest                          # zero config: every test becomes a scenario in .specdeck/spec.yaml
```

Then Dana turns the "never" lines into requirements, each with the reason it exists:

```yaml
# requirements.yaml
requirements:
  - id: money.decimal
    statement: Money amounts shall be Decimal values, with no float in the billing package.
    rationale: A float refund was one cent short in March and the accounts would not reconcile.
    type: design-constraint
  - id: payments.gateway-only
    statement: Only payments.gateway shall call Stripe.
    rationale: Tests replace the gateway with recorded traffic; a direct call charged a test card.
    type: design-constraint
  - id: refunds.partial
    statement: A partial refund shall return the refunded amount less the processing fee, rounded to cents.
    rationale: Agreed with finance, 2026-09.
    type: functional
    examples:
      - {given: {total: "19.99", fee_percent: 2.5}, expect: "19.49"}
```

Each rule gets a test that checks it (a fitness function), linked with `covers=`, and the
existing tests are linked to the functional requirements they verify. A test that checks a rule
should check what the rule is for, not one way of breaking it, because agents under pressure
get around narrow checks.

```bash
specdeck validate               # flags vague wording ("fast", no "shall") and tests linked to nothing,
                                #   citing ISO/IEC/IEEE 29148:2018 — the backlog for linking the rest
specdeck agents --out CLAUDE.md # writes the rules into a marked block; the hand-written part stays
```

`CLAUDE.md` now carries the adherence system: the adherence policy ("making the test pass
without keeping the rule is breaking the rule", "start your summary with 'Not done as asked:'"),
then each rule with its reason, the test that checks it, and whether it held in the last run.

## A normal day

A developer gives Claude Code a ticket: *"Add partial refunds. Just return a float, the UI rounds
it."* The agent reads `CLAUDE.md`, writes the refund with Decimal, runs `pytest`, and ends:

> Not done as asked: I did not return a float, because the repository rule `money.decimal`
> requires money to stay Decimal. The refund is rounded to cents with `money.to_cents`.

If the agent had used float anyway, the rule's test would fail and the run would say which rule
broke:

```
specdeck: requirements: 41 of 43 met, 1 failing (money.decimal), 1 verified another way
```

and the next `specdeck agents` would put **Broken in the last run: `money.decimal`** at the top
of `CLAUDE.md`, where the next session reads it first.

While working, a developer can keep `specdeck watch` or `pytest --specdeck-live` open: a local
viewer that shows which failures are root causes and which are only blocked behind them.

## The pull request

CI runs the suite and posts one comment:

```yaml
- run: pytest -q                           # gates are opt-in: fail on an unmet requirement, a bad link
- run: proofdeck check --base origin/main --format json --no-gate > proof.json
- run: specdeck report --proofdeck proof.json --out report.md
- run: gh pr comment $PR --body-file report.md
```

The reviewer reads the requirements the change touched, whether each is met, and whether it is
**proven** or only passing (proofdeck: did the tests notice when the changed lines were broken?).
The agent's "Not done as asked" lines say where it departed from the ticket. The diff is still
there, but the reviewer knows where to look.

## When someone asks

- *"Is partial refund implemented?"* `specdeck requirements` says met, failing or untested, and
  which tests verify it.
- *"What does the system do?"* `specdeck srs --out SRS.md` writes a software requirements
  specification in the form of ISO/IEC/IEEE 29148:2018, with the traceability matrix filled in
  from the last run. For a client, an auditor, or a new hire.
- *"Why does this test exist?"* `specdeck context --task why` gives an agent the requirements
  and their reasons, for exactly that question.

## When an agent gets something wrong

The team does what it already did, one step further: a bad session becomes a requirement with
its reason and a test, not just a line in `CLAUDE.md`. The rule is then checked on every run and
its status shows up where agents and reviewers read.

## Tools alongside

| Tool | Role |
|---|---|
| pytest | runs everything; specdeck is a plugin |
| Claude Code, Codex, Cursor, Copilot | read `CLAUDE.md` / `AGENTS.md`, where the rules block lives |
| GitHub Actions | runs the suite, posts the report, enforces the gates the team switches on |
| proofdeck | mutation evidence: is a met requirement proven or just passing? |
| tapedeck | recorded SDK traffic (Stripe, OpenAI) so tests replay real responses instead of hand-written fakes |

## Is it for you?

**A good fit:** a Python service with a pytest suite; agents writing a real share of the code;
rules that matter (money, time, privacy, languages, architecture); review as the bottleneck;
someone outside the team asking what the system does.

**Not a fit, or not yet:**
- JavaScript or TypeScript projects: specdeck is pytest only.
- A weekend prototype with no rules worth keeping.
- A team that wants agents prevented from breaking rules without CI: an agent that never runs
  the tests still breaks them; CI running `pytest` is what makes the rules stick.

**What it costs:** an afternoon to write the first requirements and rule tests, then the habit
of linking new tests. The generated block costs agents more tokens than plain bullets (1.3–1.7×
in the A/B). What the system buys for that is measured in `bench/rules_ab`: broken rules
named, checks not gamed, and departures said out loud. Plain bullets do as well at preventing
breaks; what they don't do is tell anyone when one happened.
