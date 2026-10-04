"""The three ways of asking, and nothing else different between them.

A1 mono   one worker, every visible test file, all three stubs at once.
A2 split  one worker per stage, in dependency order, given only its stage's tests.
A3 spec   identical to A2 plus specdeck's document for that stage.

A2 and A3 share the same stages, the same order and the same retry protocol, so the only
difference between them is the document. That is deliberate: "The Specification Gap"
(arXiv:2603.24284) measured a 25-39 point penalty for splitting work and found that what
recovers it is each slice carrying the full interface contract — not conflict detection,
which added nothing. The document is that contract, so it is the one variable.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import os

import subject

INCLUDE_SUPPORT = bool(os.environ.get('TDD_SUPPORT'))

RULES = """\
Rules, which the grader enforces:
- Write ONLY the files you are asked for. Any other file you write is discarded.
- Do not modify, delete or add tests, conftest.py or pytest configuration. They are
  restored from a pristine copy before grading, so edits are wasted effort.
- Do not add `pytest.skip`, `xfail`, bare `except:` or `except Exception: pass` to make
  something pass. These are detected and fail the attempt.
- Tests you cannot see also grade you, so implement what the code should do, not the
  narrowest thing that satisfies the assertions you were shown.
"""

FORMAT = """\
Reply with one fenced block per file, and nothing else:

```python
# FILE: path/to/module.py
<the complete file>
```
"""


def visible_tests(tree: Path) -> list:
    return sorted(str(p.relative_to(tree)) for p in (tree / "tests").glob("test_*.py"))


def support_files(tree: Path) -> list:
    """Helper code under tests/ that is not a test: the fake SDKs the suite imports.

    These were never shown to any arm, and the post-mortem says one line in one of them —
    `class Bot(Client)`, which subclasses a class the library proxies — accounts for five of
    the thirteen failures. The contract lives in more places than the test functions.
    """
    out = []
    for p in sorted((tree / "tests").rglob("*.py")):
        rel = str(p.relative_to(tree))
        if p.name.startswith("test_") or p.name == "conftest.py" or "/_live/" in rel:
            continue
        out.append(rel)
    return out


def read(tree: Path, rels: list) -> str:
    out = []
    for rel in rels:
        p = tree / rel
        if p.exists():
            out.append(f"### {rel}\n\n```python\n{p.read_text(encoding='utf-8')}\n```")
    return "\n\n".join(out)


def conftest(tree: Path) -> str:
    p = tree / "tests" / "conftest.py"
    return f"### tests/conftest.py\n\n```python\n{p.read_text(encoding='utf-8')}\n```" if p.exists() else ""


def contract_block(targets: list) -> str:
    """The inbound-use contract, when asked for. Addresses the one failure mode seen on both
    a frontier model and an 8B local one: a component whose interface the surrounding,
    unseen code disagrees with."""
    if not os.environ.get("TDD_CONTRACT"):
        return ""
    import contract as _c

    others = [str(q.relative_to(_c.SRC)) for q in (_c.SRC / "tapedeck").rglob("*.py")
              if "__pycache__" not in str(q) and str(q.relative_to(_c.SRC)) not in targets]
    return _c.render(targets, others)


def mono_prompt(tree: Path) -> str:
    return "\n\n".join([
        "You are implementing three modules of the `tapedeck` library. Their function bodies "
        "have been replaced with `raise NotImplementedError`; every signature, docstring, import "
        "and module-level definition is intact and correct. Write the bodies so the test suite passes.",
        "## The modules to implement\n\n" + read(tree, subject.TARGETS),
        contract_block(subject.TARGETS),
        "## The tests\n\n" + conftest(tree) + "\n\n" + read(tree, visible_tests(tree))
        + ("\n\n## Support code the tests import (fake SDKs)\n\n" + read(tree, support_files(tree))
           if INCLUDE_SUPPORT else ""),
        RULES, FORMAT,
    ])


def stage_prompt(tree: Path, stage: list, done: list, doc: str = "") -> str:
    """One stage of the split. `done` is the code earlier stages already wrote."""
    parts = [
        f"You are implementing part of the `tapedeck` library, working as one of several people "
        f"on different parts. Your part is: {', '.join('`'+s+'`' for s in stage)}. Their function "
        f"bodies have been replaced with `raise NotImplementedError`; signatures, docstrings, "
        f"imports and module-level definitions are intact and correct. Write the bodies.",
        "## Your modules\n\n" + read(tree, stage),
        contract_block(stage),
    ]
    if done:
        parts.append("## Already written by others, do not rewrite it\n\n" + read(tree, done))
    if doc:
        parts.append("## What the suite requires — generated from the tests themselves\n\n" + doc)
    parts += ["## The tests that exercise your part\n\n" + conftest(tree) + "\n\n"
              + read(tree, tests_for(tree, stage)), RULES, FORMAT]
    return "\n\n".join(parts)


def tests_for(tree: Path, stage: list) -> list:
    """Visible test files that execute any module in this stage, from specdeck's coverage map.

    Falls back to every visible file when no map exists. On this subject the map is not much
    of a filter — tapedeck's tests are integration-style — which is itself a finding.
    """
    import json

    cov = Path("/workspaces/python/tapedeck/.specdeck/coverage.json")
    spec = Path("/workspaces/python/tapedeck/.specdeck/spec.yaml")
    if not (cov.exists() and spec.exists()):
        return visible_tests(tree)
    import yaml

    data = json.loads(cov.read_text())
    sc = yaml.safe_load(spec.read_text())
    node = {s["id"]: s["logic"][0][len("pytest "):] for s in sc["scenarios"]}
    want = set()
    for sid, places in data.items():
        if any(p.split(":")[0] in stage for p in places) and sid in node:
            want.add(node[sid].split("::")[0])
    vis = set(visible_tests(tree))
    return sorted(want & vis) or visible_tests(tree)


def document(stage: list) -> str:
    """`specdeck context --for <this stage's files>`: the contract for this slice."""
    r = subprocess.run([sys.executable, "-m", "specdeck", "context", "--for", ",".join(stage)],
                       cwd="/workspaces/python/tapedeck", capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


# --------------------------------------------------------------------------- #
# Added after Phase 1: the two conditions it had no arm for.
# --------------------------------------------------------------------------- #
def spec_only_prompt(tree: Path, targets: list, doc: str) -> str:
    """Everything at once, described ONLY by the generated document — no test code.

    The question Phase 1 could not answer: is the document a substitute for the tests, or
    only a summary of them? Every other measurement today says test code wins whenever the
    question is how something is wired, and writing an implementation is that question in
    its purest form — so this is expected to do badly. It is cheap and it is the one arm
    that decides whether the document could ever stand alone.
    """
    return "\n\n".join([
        "You are implementing the `tapedeck` library. Every function body has been replaced "
        "with `raise NotImplementedError`; signatures, imports and module-level definitions are "
        "intact and correct. Write the bodies so the library behaves as specified below. "
        "You are NOT shown the tests, only this specification.",
        "## The modules to implement\n\n" + read(tree, targets),
        "## What the suite requires — generated from the tests themselves\n\n" + doc,
        RULES, FORMAT,
    ])


def chunk_prompt(tree: Path, module: str, deps: list, tests: str = "", doc: str = "") -> str:
    """One module, with only what it needs: its own stub, the code of the modules it
    imports, and either the test functions that exercise it, the document for it, or both."""
    parts = [
        f"You are implementing one module of the `tapedeck` library, working as one of several "
        f"people on different modules. Your module is `{module}`. Its function bodies have been "
        f"replaced with `raise NotImplementedError`; signatures, imports and module-level "
        f"definitions are intact and correct. Write the bodies.",
        "## Your module\n\n" + read(tree, [module]),
    ]
    if deps:
        parts.append("## The modules yours imports, already written — do not rewrite them\n\n"
                     + read(tree, deps))
    if doc:
        parts.append("## What the suite requires of your module\n\n" + doc)
    if tests:
        parts.append("## The tests that exercise your module\n\n" + conftest(tree) + "\n\n" + tests)
    parts += [RULES, FORMAT]
    return "\n\n".join(parts)
