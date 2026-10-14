# Rule checks: describing how a rule is checked

A rule in `requirements.yaml` says what must hold and why. A **check** says how to tell that it
broke, in a form a machine can run. With a check, specdeck tells you on every run whether the
rule holds, which change broke it and where, and how often it breaks over time.

```yaml
requirements:
  - id: money.decimal
    statement: Money amounts shall be Decimal values, with no float in the billing package.
    rationale: A float refund was one cent short in March and the accounts would not reconcile.
    type: design-constraint
    check:
      forbid_pattern: '\bfloat\('
      paths: [billing/**]
```

Why a small vocabulary and not code: most rules people write are of a few kinds that need only a
pattern or a list of paths to check (in 555 real rules, about half were checkable from the code or
the change alone; see `research_rules/rule_taxonomy.md`). A rule described that way can be read by
people and agents, shown in AGENTS.md, and shared without running anyone's code. Where a check
needs code, the rule points at a test, or at an existing linter, instead of inventing one.

## What a check looks at

- **The change**: what differs from a base revision (by default `HEAD`, so the uncommitted work —
  usually what an agent just did; in CI, the branch point, e.g. `origin/main`). Changed files,
  added lines, and files as they were at the base. Untracked files count as added.
- **The files**: the project as it is now.
- **The last test run**: which tests passed.

A check either **holds** or is **broken** (with evidence: file, line, what matched), or ends in an
**error** (no git repository, ruff missing, a bad pattern), which is reported and never counted as
a break.

## Checks

| Check | Broken when | Options |
|---|---|---|
| `forbid_pattern: REGEX` | a line matches (by default only added lines) | `paths`, `except`, `scope: added \| files` |
| `forbid_import: [module, …]` | a Python file imports one of them, including through `importlib.import_module("x")` and `__import__("x")` | `paths`, `except`, `scope` (default `files`) |
| `protect: [glob, …]` | the change touches a matching file | — |
| `append_only: [glob, …]` | the change edits or deletes an existing matching file (adding new files is fine: an archive) | — |
| `co_change: {changed: [glob], requires: [glob]}` | the change touches `changed` but nothing in `requires` | — |
| `no_new_dependencies: true` or `{allow: [name, …]}` | a dependency is added (pyproject.toml, requirements*.txt, package.json) | — |
| `ruff: [CODE, …]` | ruff reports one of those rule codes, e.g. `DTZ005` (naive `datetime.now()`), `T201` (`print`) | `paths`, `except`, `scope` |
| `test: [node id or scenario id, …]` | one of those tests did not pass in the last run | — |
| `command: "shell command"` | the command exits non-zero (local only: never shared) | — |

`paths` and `except` are globs relative to the project root (`**` crosses directories).
`scope: added` looks only at lines the change added; `scope: files` looks at whole files.

## Composing checks

```yaml
check:                       # a list means all of them must hold
  - forbid_import: [requests, httpx, urllib3]
    except: [shop/gateway.py]
  - no_new_dependencies: true
```

| Form | Holds when |
|---|---|
| `[a, b]` or `{all: [a, b]}` | every check holds |
| `{any: [a, b]}` | at least one holds |
| `{not: a}` | `a` is broken |
| any check with `when: {changed: [glob]}` | the change does not touch those paths, or the check holds |

`when` also decides whether the rule **applied** to a change, which is the denominator of its break
rate: a rule about migrations applies only to changes that touch migrations.

## Break detection

- On each pytest run, specdeck evaluates every check and merges the result into the requirement's
  status. A broken check makes the requirement **failing**, named in the run summary
  (`1 failing (money.decimal)`), printed with its evidence, and put first in AGENTS.md.
- Each run appends one line to `.specdeck/breaks.jsonl`: the commit, the base, and for each
  requirement whether it applied and held.
- `specdeck breaks` reads that log: per rule, how many runs it applied to, how many broke it, on
  how many commits, and when it last broke (rules with a check or a break; `--all` for every
  requirement). A rule that breaks often is badly worded, badly
  placed, or too strict; a rule that never applies may be dead.
- `specdeck check --base REF` evaluates the checks once, without running tests: for CI, a
  pre-commit hook, or a coding agent's hook after each edit.

## Design notes

- **Grammar**: a check is one primitive (one of the keys above) plus options, or a composition.
  Unknown keys, a wrong type, or a regex that does not compile are reported by
  `specdeck validate` and the check is skipped.
- **Existing languages**: patterns are Python regular expressions; code-level rules go to ruff by
  rule code rather than being re-implemented. Other checkers (import-linter, semgrep) fit the same
  slot and can be added the same way.
- **Sharing**: every check except `command` is data, so a rule with its check can be shared and
  read without running untrusted code. `command` exists for local rules that need it.
- **Not covered yet**: rules about what the agent *did* rather than what it changed (commands it
  ran, whether it asked first) need the agent's transcript; about a fifth of real-project rules.
