"""Three routes from a description to a test suite. The only difference is what happens
in between.

A direct    description -> tests
B plan      description -> free-form plan -> tests
C spec      description -> a specdeck-shaped spec -> tests

B exists because C gets two calls and a chance to think twice. Without B, "C beats A"
would only show that an intermediate step helps, not that *this* intermediate step does.
That confound cost 160k tokens to untangle earlier in this project.

Every arm must emit plain pytest, so the comparison is the representation and not the
library the tests are written against.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path("/workspaces/python/tapedeck")

FORMAT = """\
Reply with one fenced Python block and nothing else:

```python
<the complete test file>
```
"""

RULES = """\
Rules:
- Plain pytest. Import the module under test; do not import any test framework other than
  pytest. No fixtures from files you cannot see.
- The tests are graded two ways: they must pass against the correct implementation, and
  then deliberate bugs are injected into it and we count how many your tests catch. So
  assert on specific values and edge cases, not merely that a call returns something.
- Do not use `assert True`, bare `assert obj is not None`, or `try/except` around the
  behaviour you are checking. Those pass whatever the code does.
- Self-contained: build your own inputs inline.
"""


def description(module: str) -> str:
    """The public surface and the prose that documents it — what a developer would have
    before writing any tests. Identical for all three arms."""
    src = (REPO / module).read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    out = [f"Module `{module}` of the `tapedeck` library.", ""]
    doc = ast.get_docstring(tree)
    if doc:
        out += ["What it is for:", "", doc, ""]
    out.append("Its public surface, with the documentation of each part:")
    out.append("")
    out.append("```python")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(node, "lineno", None):
            text = "\n".join(lines[node.lineno - 1:node.end_lineno])
            if not text.lstrip().startswith("_"):
                out.append(text)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue
            header = lines[min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1: node.body[0].lineno - 1]
            out += header
            d = ast.get_docstring(node)
            if d:
                out.append('    """' + d + '"""')
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("__"):
                        if sub.name.startswith("_"):
                            continue
                        out += ["    " + l.strip() for l in lines[sub.lineno - 1:sub.body[0].lineno - 1]]
                        sd = ast.get_docstring(sub)
                        if sd:
                            out.append('        """' + sd + '"""')
            out.append("")
    out.append("```")
    return "\n".join(out)


def direct(module: str) -> str:
    return "\n\n".join([
        "Write a pytest test suite for the module described below. You cannot see its "
        "implementation or any existing tests.",
        description(module), RULES, FORMAT])


def plan_step(module: str) -> str:
    return "\n\n".join([
        "Before writing any tests for the module below, write a plan: what behaviours a test "
        "suite must cover, and the edge cases that would catch a subtly wrong implementation. "
        "Prose or a list, whatever serves you. Do not write test code yet.",
        description(module)])


def tests_from_plan(module: str, plan: str) -> str:
    return "\n\n".join([
        "Write a pytest test suite for the module below, following the plan you made.",
        description(module), "## Your plan\n\n" + plan, RULES, FORMAT])


SPEC_STEP = """\
Before writing any tests, write a specification of the behaviour in specdeck's format: YAML
with a `scenarios` list. Each scenario has

  id            kebab-case
  name          one sentence of what must be true
  steps         Given / When / Then, in the order they happen. A `Then` carries an
                `assertion` with `expected_label`: the exact value or condition that must
                hold, taken from the requirement rather than from imagined code.
  depends_on    ids of scenarios that must hold first, if any

Cover the edge cases a subtly wrong implementation would get away with. Output only YAML.
"""


def spec_step(module: str) -> str:
    return "\n\n".join([SPEC_STEP, description(module)])


def tests_from_spec(module: str, spec: str) -> str:
    return "\n\n".join([
        "Write a pytest test suite for the module below. Implement every scenario of the "
        "specification you wrote: one test per scenario, asserting the `expected_label` value.",
        description(module), "## Your specification\n\n```yaml\n" + spec.strip() + "\n```",
        RULES, FORMAT])


# --------------------------------------------------------------------------- #
# The spec arm, with the ceiling removed.
#
# Measured cause: the spec arm wrote exactly as many tests as its spec listed scenarios —
# a ratio of 1.00 on all three modules, every run — while free-form planning wrote 3x more
# and caught 4 points more bugs. Two instructions were doing that: the spec step never asked
# for exhaustiveness, and the generation step said "one test per scenario". Both are lifted
# here, and nothing else changes.
# --------------------------------------------------------------------------- #
SPEC_STEP_OPEN = """\
Before writing any tests, write a specification of the behaviour in specdeck's format: YAML
with a `scenarios` list. Each scenario has

  id            kebab-case
  name          one sentence of what must be true
  steps         Given / When / Then, in the order they happen. A `Then` carries an
                `assertion` with `expected_label`: the exact value or condition that must
                hold, taken from the requirement rather than from imagined code.
  depends_on    ids of scenarios that must hold first, if any

Be exhaustive. Write a scenario for every behaviour, every boundary, every error case, and
every way a subtly wrong implementation could slip past a careless reader: empty and missing
inputs, wrong types, nesting limits, ordering, values that look like other values. Do not
ration yourself — a thorough specification of even a small module usually runs to several
dozen scenarios. Output only YAML.
"""


def spec_step_open(module: str) -> str:
    return "\n\n".join([SPEC_STEP_OPEN, description(module)])


def tests_from_spec_open(module: str, spec: str) -> str:
    return "\n\n".join([
        "Write a pytest test suite for the module below, covering the specification you wrote.",
        description(module), "## Your specification\n\n```yaml\n" + spec.strip() + "\n```",
        "Cover every scenario. A scenario with several cases or boundaries needs several tests — "
        "write as many as the behaviour requires, there is no one-to-one rule. If while writing "
        "you notice an edge case your specification missed, test that too.",
        RULES, FORMAT])
