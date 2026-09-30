"""Requirements: what the code should do, written by people, independent of the tests.

The generated spec says what the tests do. This file says what they *should*
check. Each entry is short: an id, one sentence of intent, and concrete
examples. Tests link to entries with ``@scenario(covers=[...])`` and can take
their expected values from the examples with ``example()``, so an expected
value comes from the requirement rather than from running the code.

    # requirements.yaml
    requirements:
      - id: pricing.bulk-tier
        says: Orders of 100 units or more get 15% off; 10 or more get 5%.
        why: Agreed with sales, 2026-09.            # optional
        examples:
          - given: {qty: 100}
            expect: 15
          - given: {qty: 99}
            expect: 5

    @scenario(covers=["pricing.bulk-tier"])
    def test_tiers():
        assert bulk_discount(100) == example("pricing.bulk-tier", qty=100)

    @pytest.mark.parametrize("given, expected", examples("pricing.bulk-tier"))
    def test_every_example(given, expected):
        assert bulk_discount(**given) == expected
"""
from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_FILE = "requirements.yaml"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RequirementError(LookupError):
    """An id or example that isn't in the requirements file."""


@dataclass
class Example:
    given: dict
    expect: Any
    name: Optional[str] = None


@dataclass
class Requirement:
    id: str
    says: str
    why: Optional[str] = None
    examples: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    line: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "says": self.says}
        if self.why:
            d["why"] = self.why
        if self.tags:
            d["tags"] = list(self.tags)
        d["examples"] = [{k: v for k, v in (("name", e.name), ("given", e.given), ("expect", e.expect))
                          if v is not None} for e in self.examples]
        if self.line:
            d["line"] = self.line
        return d


@dataclass
class Requirements:
    path: Optional[Path] = None
    items: dict = field(default_factory=dict)       # id -> Requirement, in file order
    problems: list = field(default_factory=list)    # human-readable problems with the file

    def __bool__(self) -> bool:
        return bool(self.items)

    def get(self, rid: str) -> Requirement:
        req = self.items.get(rid)
        if req is None:
            near = [i for i in self.items if rid.split(".")[0] in i][:5]
            hint = f" Did you mean: {', '.join(near)}?" if near else ""
            where = f" in {self.path}" if self.path else ""
            raise RequirementError(f"no requirement {rid!r}{where}.{hint}")
        return req


def _line_numbers(text: str) -> dict:
    """id -> line of its `id:` key, for pointing people at the right place."""
    out = {}
    for i, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"\s*-?\s*id:\s*['\"]?([^'\"#\s]+)", line)
        if m:
            out.setdefault(m.group(1), i)
    return out


def load(path) -> Requirements:
    """Parse a requirements file. Problems are collected, not raised: a bad entry is skipped."""
    path = Path(path)
    reqs = Requirements(path=path)
    if not path.is_file():
        return reqs
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        reqs.problems.append(f"{path.name} is not valid YAML: {exc}")
        return reqs
    entries = data.get("requirements") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        reqs.problems.append(f"{path.name}: expected a list under `requirements:`")
        return reqs
    lines = _line_numbers(text)
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            reqs.problems.append(f"{path.name}: entry {i} is not a mapping")
            continue
        rid, says = str(entry.get("id") or "").strip(), str(entry.get("says") or "").strip()
        if not rid or not _ID.match(rid):
            reqs.problems.append(f"{path.name}: entry {i} needs an `id` made of letters, digits, '.', '-' or '_'")
            continue
        if not says:
            reqs.problems.append(f"{path.name}: {rid} needs `says:`, one sentence of what should happen")
            continue
        if rid in reqs.items:
            reqs.problems.append(f"{path.name}: {rid} is defined twice; the second one is ignored")
            continue
        examples = []
        for j, ex in enumerate(entry.get("examples") or [], start=1):
            if not isinstance(ex, dict) or "expect" not in ex:
                reqs.problems.append(f"{path.name}: {rid} example {j} needs `expect:`")
                continue
            given = ex.get("given") or {}
            if not isinstance(given, dict):
                reqs.problems.append(f"{path.name}: {rid} example {j}: `given:` must map names to values")
                continue
            examples.append(Example(given=dict(given), expect=ex["expect"], name=ex.get("name")))
        tags = entry.get("tags") or []
        reqs.items[rid] = Requirement(id=rid, says=says, why=(str(entry["why"]).strip() if entry.get("why") else None),
                                      examples=examples, tags=[str(t) for t in tags] if isinstance(tags, list) else [],
                                      line=lines.get(rid))
    return reqs


def find(start) -> Optional[Path]:
    """requirements.yaml in `start` or the nearest parent that has one."""
    here = Path(start).resolve()
    for d in (here, *here.parents):
        p = d / DEFAULT_FILE
        if p.is_file():
            return p
    return None


# --------------------------------------------------------------------------- #
# Using examples from tests
# --------------------------------------------------------------------------- #
_LOADED: Optional[Requirements] = None
# (requirement id, given) pairs a running test took expected values from
_USED: contextvars.ContextVar = contextvars.ContextVar("specdeck_examples_used", default=None)


def set_loaded(reqs: Optional[Requirements]) -> None:
    global _LOADED
    _LOADED = reqs


def _current() -> Requirements:
    global _LOADED
    if _LOADED is None:  # outside pytest, or before the plugin configured
        path = find(Path.cwd())
        _LOADED = load(path) if path else Requirements()
    return _LOADED


def example(rid: str, **given) -> Any:
    """The expected value of the example of requirement `rid` whose `given` equals these arguments."""
    req = _current().get(rid)
    for ex in req.examples:
        if ex.given == given:
            used = _USED.get()
            if used is not None and (rid, given) not in used:
                used.append((rid, dict(given)))
            return ex.expect
    shown = "; ".join(", ".join(f"{k}={v!r}" for k, v in e.given.items()) or "(no inputs)" for e in req.examples)
    raise RequirementError(f"{rid} has no example with {given or '(no inputs)'}. It has: {shown or 'no examples'}.")


def examples(rid: str) -> list:
    """[(given, expected), ...] for every example of `rid`, ready for pytest.mark.parametrize."""
    import pytest

    req = _current().get(rid)
    params = []
    for i, ex in enumerate(req.examples, start=1):
        label = ex.name or ",".join(f"{k}={v}" for k, v in ex.given.items()) or f"example{i}"
        params.append(pytest.param(dict(ex.given), ex.expect, id=label,
                                   marks=pytest.mark.specdeck_example(rid, dict(ex.given))))
    return params


# --------------------------------------------------------------------------- #
# Status per requirement
# --------------------------------------------------------------------------- #
BAD = ("failed", "error")


def status_of(statuses: list) -> str:
    """met | failing | blocked | not-run | untested, from the statuses of the scenarios covering it."""
    if not statuses:
        return "untested"
    if any(s in BAD for s in statuses):
        return "failing"
    if any(s == "blocked" for s in statuses):
        return "blocked"
    if all(s == "passed" for s in statuses):
        return "met"
    return "not-run"


def _key(given) -> str:
    return json.dumps(given or {}, sort_keys=True, default=str)


def report(reqs: Requirements, scenarios: list) -> dict:
    """Join requirements with the scenarios that cover them.

    `scenarios`: dicts with id, name, status, covers (list of ids) and
    examples_used (list of [id, given]).
    """
    return report_dicts([r.to_dict() for r in reqs.items.values()], scenarios,
                        file=str(reqs.path) if reqs.path else None, problems=reqs.problems)


def snapshot(reqs: Requirements) -> dict:
    """What a run records about the requirements file, so history shows them as they were."""
    return {"file": str(reqs.path) if reqs.path else None, "requirements": [r.to_dict() for r in reqs.items.values()],
            "problems": list(reqs.problems)}


def report_dicts(req_dicts: list, scenarios: list, *, file: Optional[str] = None, problems: list = ()) -> dict:
    covering: dict = {r["id"]: [] for r in req_dicts}
    unknown: dict = {}
    for s in scenarios:
        for rid in s.get("covers") or []:
            if rid in covering:
                covering[rid].append(s)
            else:
                unknown.setdefault(rid, []).append(s["id"])
    out = []
    for req in req_dicts:
        rid = req["id"]
        sc = covering[rid]
        used = {_key(g) for s in sc for (r, g) in (s.get("examples_used") or []) if r == rid and s["status"] == "passed"}
        d = dict(req)
        d["examples"] = [dict(e, checked=_key(e.get("given")) in used) for e in req.get("examples", [])]
        d.update({
            "status": status_of([s["status"] for s in sc]),
            "scenarios": [{"id": s["id"], "name": s.get("name"), "status": s["status"]} for s in sc],
            "examples_checked": sum(1 for e in d["examples"] if e["checked"]),
        })
        out.append(d)
    counts = {k: 0 for k in ("met", "failing", "blocked", "not-run", "untested")}
    for d in out:
        counts[d["status"]] += 1
    return {
        "file": file,
        "counts": {**counts, "total": len(out)},
        "requirements": out,
        "unknown": [{"id": rid, "scenarios": ids} for rid, ids in unknown.items()],
        "problems": list(problems),
    }
