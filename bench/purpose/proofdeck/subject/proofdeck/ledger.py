"""Requirements (specdeck's requirements.yaml), as proofdeck sees them.

An entry is **approved** when it exists on the base branch: a person reviewed it
when it was merged. Entries added on the branch are **drafts**. This is the
same rule as proofdeck's own configuration, and for the same reason: the code
and the requirement it's checked against can't both come from the branch.

What follows from that:
- changing or deleting an approved entry on the branch is reported and fails the gate;
- a test linked to a requirement (``covers=`` or ``example()``) whose literal
  expected value contradicts an approved example fails the gate;
- an expected value taken from an approved example, or equal to one, is confirmed;
- a draft entry is listed for the reviewer, and expected values taken from it
  stay unconfirmed until it is merged.
"""
from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass, field
from typing import Optional

from .git import base_text, branch_text, merge_base
from .model import Finding, FindingKind

DEFAULT_FILE = "requirements.yaml"

MSG_CHANGED = ("The approved requirement {rid} was {how} on this branch. Requirements are what the code is "
               "checked against, so they change in their own reviewed change, not alongside the code.")
MSG_DRAFT = ("New requirement {rid}, added on this branch: \"{says}\". Read it against what was asked for; "
             "expected values taken from it count as confirmed once it is merged.")
MSG_CONTRADICTS = ("This test says {actual}, but the approved requirement {rid} says {given} gives {expect}. "
                   "Either the code and this test are wrong, or the requirement needs its own reviewed change.")


@dataclass
class Entry:
    id: str
    says: str
    examples: list = field(default_factory=list)   # [(given: dict, expect)]
    line: int = 1


@dataclass
class Ledger:
    path: str = DEFAULT_FILE
    approved: dict = field(default_factory=dict)   # id -> Entry, from the base branch
    branch: dict = field(default_factory=dict)     # id -> Entry, from the working tree
    note: str = ""

    def approved_expect(self, rid: str, given: dict):
        """(True, expect) if the approved entry `rid` has an example with exactly `given`."""
        entry = self.approved.get(rid)
        if entry is not None:
            for g, expect in entry.examples:
                if g == given:
                    return True, expect
        return False, None


def _config_path(repo, rev: str) -> str:
    """specdeck_requirements from the base branch's pytest config, else requirements.yaml."""
    text = base_text(repo, rev, "pyproject.toml")
    if text:
        try:
            ini = tomllib.loads(text).get("tool", {}).get("pytest", {}).get("ini_options", {})
            if ini.get("specdeck_requirements"):
                return str(ini["specdeck_requirements"])
        except tomllib.TOMLDecodeError:
            pass
    for name in ("pytest.ini", "setup.cfg", "tox.ini"):
        text = base_text(repo, rev, name) or ""
        m = re.search(r"^\s*specdeck_requirements\s*=\s*(\S+)", text, re.M)
        if m:
            return m.group(1)
    return DEFAULT_FILE


def parse(text: Optional[str]) -> dict:
    if not text:
        return {}
    import yaml  # specdeck depends on PyYAML; without it there are no requirements to read

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    entries = data.get("requirements") if isinstance(data, dict) else data
    lines = {}
    for i, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"\s*-?\s*id:\s*['\"]?([^'\"#\s]+)", line)
        if m:
            lines.setdefault(m.group(1), i)
    out = {}
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or not e.get("id") or not e.get("says"):
            continue
        rid = str(e["id"]).strip()
        if rid in out:
            continue
        examples = [(dict(x.get("given") or {}), x["expect"]) for x in (e.get("examples") or [])
                    if isinstance(x, dict) and "expect" in x and isinstance(x.get("given") or {}, dict)]
        out[rid] = Entry(rid, str(e["says"]).strip(), examples, lines.get(rid, 1))
    return out


def load(repo, base: str = "main") -> Ledger:
    rev = merge_base(repo, base)
    path = _config_path(repo, rev)
    ledger = Ledger(path=path)
    try:
        ledger.approved = parse(base_text(repo, rev, path))
        ledger.branch = parse(branch_text(repo, path))
    except ImportError:
        ledger.note = "PyYAML isn't installed, so the requirements file was not read"
    return ledger


def entry_findings(ledger: Ledger) -> list:
    """Approved entries changed or deleted on the branch, and new draft entries."""
    out = []
    for rid, old in ledger.approved.items():
        new = ledger.branch.get(rid)
        if new is None:
            out.append(Finding(FindingKind.REQUIREMENT_CHANGED, ledger.path, 1,
                               MSG_CHANGED.format(rid=rid, how="deleted"), snippet=f"id: {rid}"))
        elif (new.says, new.examples) != (old.says, old.examples):
            how = "reworded" if new.examples == old.examples else "given different examples"
            out.append(Finding(FindingKind.REQUIREMENT_CHANGED, ledger.path, new.line,
                               MSG_CHANGED.format(rid=rid, how=how), snippet=f"id: {rid}"))
    for rid, new in ledger.branch.items():
        if rid not in ledger.approved:
            out.append(Finding(FindingKind.DRAFT_REQUIREMENT, ledger.path, new.line,
                               MSG_DRAFT.format(rid=rid, says=new.says), snippet=f"{rid}: {new.says}"))
    return out


# --------------------------------------------------------------------------- #
# Reading tests: which requirements a test claims, and how a call lines up with an example
# --------------------------------------------------------------------------- #
def _callee(node: ast.AST) -> str:
    f = node.func if isinstance(node, ast.Call) else node
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _const(node: ast.AST):
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def example_call(node: ast.AST):
    """(rid, given) for `example("rid", k=v, ...)` with literal arguments, else None."""
    if not (isinstance(node, ast.Call) and _callee(node) == "example" and node.args):
        return None
    ok, rid = _const(node.args[0])
    if not ok or not isinstance(rid, str):
        return None
    given = {}
    for kw in node.keywords:
        ok, value = _const(kw.value)
        if not kw.arg or not ok:
            return None
        given[kw.arg] = value
    return rid, given


def linked(fn: ast.AST) -> set:
    """Requirement ids a test function claims: @scenario(covers=[...]) and example()/examples() calls."""
    ids = set()
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and _callee(d) == "scenario":
            for kw in d.keywords:
                if kw.arg == "covers":
                    ok, value = _const(kw.value)
                    if ok and isinstance(value, (list, tuple)):
                        ids.update(str(v) for v in value)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _callee(node) in ("example", "examples") and node.args:
            ok, rid = _const(node.args[0])
            if ok and isinstance(rid, str):
                ids.add(rid)
    return ids


def call_matches(call: ast.Call, given: dict) -> bool:
    """Does this call pass exactly the example's inputs (positionally in order, or by name)?"""
    pos = []
    for a in call.args:
        ok, v = _const(a)
        if not ok:
            return False
        pos.append(v)
    kw = {}
    for k in call.keywords:
        ok, v = _const(k.value)
        if not k.arg or not ok:
            return False
        kw[k.arg] = v
    keys = list(given)
    if len(pos) + len(kw) != len(keys):
        return False
    if any(given[keys[i]] != v or type(given[keys[i]]) is not type(v) for i, v in enumerate(pos)):
        return False
    rest = keys[len(pos):]
    return set(kw) == set(rest) and all(given[k] == kw[k] for k in rest)
