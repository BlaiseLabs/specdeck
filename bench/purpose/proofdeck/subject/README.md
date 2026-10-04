# proofdeck

For every changed line in a pull request, proofdeck shows whether the tests
would notice if that line were wrong. It also flags places where a passing test
only proves that the code agrees with itself.

It sits beside **tapedeck**, which records real SDK traffic, and **specdeck**,
which turns tests into a spec. proofdeck's own tests are written in specdeck.

## Use

```
proofdeck check --base main                 # markdown, for a PR comment
proofdeck check --base main --format json   # for tools and agents (and `specdeck trust`)
proofdeck check --base main --no-gate       # report only, always exit 0
proofdeck check --base main --coverage .coverage   # reuse per-test coverage from CI
```

Run it from the repository, on the branch you want checked. The working tree is
compared with the commit where the branch left `--base`, so uncommitted work
counts too. Your files are never modified: tests run in throwaway copies.

How it works:

1. Find the non-blank lines the branch added or changed in source files.
2. Skip arid lines: logging, print, sleeps, imports, docstrings, signatures,
   bare annotations, `__main__` blocks.
3. Give every line one mutant, the most useful first: a comparison boundary,
   then a negated condition, arithmetic, boolean, constant, or deleting the
   statement. All of them are compiled into a copy of the code at once, each
   behind a switch, without moving any line.
4. Start pytest **once**. It collects the suite, then forks a child that runs
   the suite under a line tracer to learn which tests execute which changed
   line. Lines no test executes are *never-ran*.
5. For each mutant, fork a child (several at a time) with that mutant switched
   on, and run only the tests that execute its line: tests that fake nothing
   first, fastest first, stopping at the first failure. Forking shares
   everything already imported, so a mutant costs its tests' run time, not a
   pytest start. A mutant nobody notices makes the line *ran-only*; if only
   tests that replace collaborators with fakes notice it, *mock-only*; one
   that makes the tests hang is stopped at ten times their normal time and
   counts as noticed.
6. Run the detectors on the test lines the branch added, and scan for weakened
   tests and checker changes.

Configuration lives in `[tool.proofdeck]` and is **read from the base branch**,
so a pull request can't loosen its own checks:

```toml
[tool.proofdeck]
exclude = ["src/generated/*"]   # never mutated
max_mutants_per_file = 25       # mutation budget per file
test_command = "python -m pytest"
timeout = 60                    # most seconds a mutant's tests may take
workers = 0                     # mutants at a time; 0 = one per CPU, up to 8
mode = "auto"                   # auto | fork | subprocess
coverage = ""                   # coverage.py file from CI, instead of tracing
```

**Reusing coverage from CI.** If CI already runs the tests with per-test
coverage, proofdeck can skip its own traced run:

```
pytest --cov=src --cov-context=test          # pytest-cov
proofdeck check --base main --coverage .coverage
```

`coverage run` with `dynamic_context = "test_function"` works too. The file is
read directly (coverage.py doesn't need to be installed) and must come from
the same commit. A file without per-test contexts is refused with a note, and
proofdeck traces instead.

**Where fork isn't available** (Windows), or with `mode = "subprocess"`,
proofdeck starts one pytest process per mutant. The answers are identical; the
tests check that on every demo branch.

A `pragma: no mutate` can only appear on a changed line, and every changed line
is analysed, so pragmas never exempt anything; adding one is reported.

The test suite (`python -m pytest`, about a minute) is written in specdeck; each
run regenerates `../dx-spec-viewer/specs/proofdeck.yaml`.

## What it reports

| Per changed line | Meaning | Gate |
|---|---|---|
| checked | Breaking the line makes a test fail. | passes |
| ran-only | Tests execute the line but a mutant of it survives. | fails |
| never-ran | No test executes the line. | fails |
| mock-only | Only tests that fake its collaborators notice a change. | reported |
| import-time | Runs when its module is imported (a constant, a table). Fork mode can't switch a mutant on after the import; `--mode subprocess` checks these. | reported |

| Finding | Gate |
|---|---|
| surviving-mutant | fails |
| weakened-test (new skip or xfail, removed assertion, deleted test) | fails |
| checker-tampering (`pragma: no mutate`, proofdeck, coverage or pytest config edits) | fails |
| vacuous-assertion, recomputed-expectation, special-cased-input, mock-only | reported |
| contradicts-requirement (a linked test disagrees with an approved example) | fails |
| requirement-changed (an approved requirement edited or deleted on the branch) | fails |
| draft-requirement (a requirement added on the branch) | for review |
| unconfirmed-expectation | advisory only |

`--format json` carries the evidence behind each line, per test: `ran_by` (the
tests that execute it), `killed_by` (the ones that noticed its mutant) and a
`tests` list with what each of those tests replaces with fakes; `at_import` marks lines
every test "ran" only because importing the module ran them. `specdeck trust`
joins that on the node id with what each test is *for*, so a requirement can be
reported as met but unproven, or met only through fakes.

## Requirements

If the project keeps a specdeck `requirements.yaml` (or wherever the base
branch's `specdeck_requirements` setting points), proofdeck uses it as the
independent answer that mutation testing can't provide. An entry is
**approved** when it exists on the base branch, because a person reviewed it
when it was merged. Entries added on the branch are **drafts**.

- A test that claims a requirement, with `@scenario(covers=[...])` or by calling
  `example()`/`examples()`, and asserts a literal that disagrees with an approved
  example for the same inputs is a *contradiction* and fails the gate. That
  catches the pasted-from-buggy-output test that every mutant misses.
- An expected value taken from an approved example, or equal to one, is
  *confirmed* and drops out of the unconfirmed list.
- Changing or deleting an approved entry on the branch fails the gate:
  requirements change in their own reviewed change, not alongside the code.
- Draft entries are listed for the reviewer, and expected values taken from
  them stay unconfirmed until the entry is merged.

Only tests that claim a requirement are compared with it, so the same argument
values in an unrelated test never count.

There is no mutation score, only findings anchored to lines. Configuration is
read from the base branch, so a branch cannot exempt its own code.

## The case it exists for

The demo repos in `tests/conftest.py` ship the same off-by-one bug behind five
kinds of test. When expected values are pasted from the buggy output, every
mutant dies and existing tools report the code as fully tested. proofdeck cannot
know which value is right either. It marks expectations that were born on the
branch together with the code as unconfirmed, and points the reviewer at them.
Guidance always asks for the value the requirement gives, never the code's
current output, so an agent following it cannot lock the bug in.

## Limits

- Python and pytest only. Tests must run with `python -m pytest` (or your
  `test_command`) from the repository root.
- One mutant per line: a line can be *checked* and still hide other bugs.
- Unconfirmed expectations can't be told apart from correct new ones; the tool
  lists them for a person to check against the requirement.
- Tracing uses `sys.settrace`, so coverage plugins are turned off for
  proofdeck's runs (`-p no:cov`, or `--no-cov` when your addopts use `--cov`).
- Fork mode runs each mutant's tests directly, bypassing plugins that wrap the
  whole test protocol (pytest-rerunfailures, pytest-timeout); proofdeck applies
  its own time limit.
- The detectors are syntactic. They catch the common shapes, not every one.

**Code that installs its own tracer is invisible here.** proofdeck learns which test
runs which line by tracing, so a test that starts `coverage.py`, a profiler or a
debugger displaces that tracer while it runs; those lines come back *never-ran* even
though a test exercises them. Mutants on them are not checked. `specdeck/tracing.py`
is the case that found this.

