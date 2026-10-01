"""Validate a spec file — specdeck's own, with no outside dependency.

Two layers, the same split the format has always had:

1. **Structural** — shapes, types, required fields, unknown keys.
2. **Semantic** — unique ids, resolvable ``depends_on``, no cycles, known groups.

Errors mean the file is wrong; warnings mean it is suspicious but usable. A
spec specdeck generated should produce neither, which is what
``specdeck validate`` checks in CI.

Line numbers are best-effort: the YAML is composed a second time to map each
scenario index back to a source line.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

KEYWORDS = {"Given", "When", "Then", "And"}
TOP_KEYS = {"feature", "description", "groups", "scenarios"}
SCENARIO_KEYS = {"id", "name", "description", "group", "tags", "depends_on", "steps", "logic"}
STEP_KEYS = {"keyword", "text", "assertion", "decision_context"}
ASSERTION_KEYS = {"expected_label", "observed_label"}
GROUP_KEYS = {"id", "name"}


@dataclass
class Diagnostic:
    severity: str  # "error" | "warning"
    code: str
    message: str
    scenario: Optional[str] = None
    path: Optional[str] = None
    line: Optional[int] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def format(self) -> str:
        loc = f":{self.line}" if self.line else ""
        where = f' "{self.scenario}"' if self.scenario else ""
        return f"  {self.severity:7} {self.code:14}{loc}{where}  {self.message}"


def is_valid(diags: list) -> bool:
    return not any(d.severity == "error" for d in diags)


def _scenario_lines(text: str) -> dict:
    """scenario index -> 1-based source line, best effort."""
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(node, yaml.MappingNode):
        return {}
    for key_node, val_node in node.value:
        if getattr(key_node, "value", None) == "scenarios" and isinstance(val_node, yaml.SequenceNode):
            return {i: item.start_mark.line + 1 for i, item in enumerate(val_node.value)}
    return {}


def validate_text(text: str, source: str = "<spec>") -> list:
    """Diagnostics for one spec document."""
    diags: list = []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [Diagnostic("error", "YAML_PARSE", f"{source} is not valid YAML: {e}",
                           line=(mark.line + 1) if mark else None)]
    if data is None:
        return [Diagnostic("error", "EMPTY", f"{source} is empty.")]
    if not isinstance(data, dict):
        return [Diagnostic("error", "NOT_A_MAPPING", f"{source} must be a mapping, found {type(data).__name__}.")]

    lines = _scenario_lines(text)
    _check_top(data, diags)
    group_ids = _check_groups(data, diags)
    _check_scenarios(data, diags, lines, group_ids)
    return diags


def _check_top(data: dict, diags: list) -> None:
    if not str(data.get("feature") or "").strip():
        diags.append(Diagnostic("error", "NO_FEATURE", "Missing `feature` (the spec's title)."))
    for key in data:
        if key not in TOP_KEYS:
            diags.append(Diagnostic("warning", "UNKNOWN_KEY", f"Unknown top-level key `{key}`.", path=str(key)))
    if "scenarios" not in data:
        diags.append(Diagnostic("error", "NO_SCENARIOS", "Missing `scenarios`."))
    elif not isinstance(data["scenarios"], list):
        diags.append(Diagnostic("error", "BAD_SCENARIOS",
                                f"`scenarios` must be a list, found {type(data['scenarios']).__name__}."))


def _check_groups(data: dict, diags: list) -> set:
    groups = data.get("groups")
    ids: set = set()
    if groups is None:
        return ids
    if not isinstance(groups, list):
        diags.append(Diagnostic("error", "BAD_GROUPS", f"`groups` must be a list, found {type(groups).__name__}."))
        return ids
    for i, g in enumerate(groups):
        path = f"groups[{i}]"
        if not isinstance(g, dict):
            diags.append(Diagnostic("error", "BAD_GROUP", f"Group must be a mapping, found {type(g).__name__}.", path=path))
            continue
        gid = str(g.get("id") or "")
        if not gid:
            diags.append(Diagnostic("error", "GROUP_NO_ID", "Group has no `id`.", path=path))
        elif gid in ids:
            diags.append(Diagnostic("error", "DUP_GROUP", f"Duplicate group id `{gid}`.", path=path))
        else:
            ids.add(gid)
        if not str(g.get("name") or "").strip():
            diags.append(Diagnostic("warning", "GROUP_NO_NAME", f"Group `{gid}` has no `name`.", path=path))
        for key in g:
            if key not in GROUP_KEYS:
                diags.append(Diagnostic("warning", "UNKNOWN_KEY", f"Unknown group key `{key}`.", path=path))
    return ids


def _check_scenarios(data: dict, diags: list, lines: dict, group_ids: set) -> None:
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list):
        return
    seen_ids: dict = {}
    seen_names: dict = {}
    declared: dict = {}  # id -> depends_on, for the cycle pass

    for i, sc in enumerate(scenarios):
        ln = lines.get(i)
        path = f"scenarios[{i}]"
        if not isinstance(sc, dict):
            diags.append(Diagnostic("error", "BAD_SCENARIO",
                                    f"Scenario must be a mapping, found {type(sc).__name__}.", path=path, line=ln))
            continue
        name = str(sc.get("name") or "")
        sid = str(sc.get("id") or "")
        label = sid or name or path

        if not name.strip():
            diags.append(Diagnostic("error", "NO_NAME", "Scenario has no `name`.", scenario=label, path=path, line=ln))
        elif name in seen_names:
            diags.append(Diagnostic("warning", "DUP_NAME", f"Duplicate scenario name `{name}`.",
                                    scenario=label, path=path, line=ln))
        else:
            seen_names[name] = i

        if sid:
            if sid in seen_ids:
                diags.append(Diagnostic("error", "DUP_ID", f"Duplicate scenario id `{sid}`.",
                                        scenario=label, path=path, line=ln))
            else:
                seen_ids[sid] = i

        grp = sc.get("group")
        if grp is not None and group_ids and str(grp) not in group_ids:
            diags.append(Diagnostic("error", "BAD_GROUP_REF",
                                    f"`group: {grp}` is not declared in `groups`.", scenario=label, path=path, line=ln))

        for key in sc:
            if key not in SCENARIO_KEYS:
                diags.append(Diagnostic("warning", "UNKNOWN_KEY", f"Unknown scenario key `{key}`.",
                                        scenario=label, path=f"{path}.{key}", line=ln))

        for field in ("tags", "depends_on", "logic"):
            val = sc.get(field)
            if val is not None and not isinstance(val, list):
                diags.append(Diagnostic("error", "BAD_LIST",
                                        f"`{field}` must be a list, found {type(val).__name__}.",
                                        scenario=label, path=f"{path}.{field}", line=ln))

        _check_steps(sc, diags, label, path, ln)
        if sid:
            deps = sc.get("depends_on")
            declared[sid] = [str(d) for d in deps] if isinstance(deps, list) else []

    _check_refs(scenarios, declared, seen_ids, diags, lines)
    _check_cycles(declared, diags)


def _check_steps(sc: dict, diags: list, label: str, path: str, ln) -> None:
    steps = sc.get("steps")
    if steps is None:
        diags.append(Diagnostic("warning", "NO_STEPS", "Scenario has no `steps`.", scenario=label, path=path, line=ln))
        return
    if not isinstance(steps, list):
        diags.append(Diagnostic("error", "BAD_STEPS", f"`steps` must be a list, found {type(steps).__name__}.",
                                scenario=label, path=f"{path}.steps", line=ln))
        return
    for j, st in enumerate(steps):
        spath = f"{path}.steps[{j}]"
        if not isinstance(st, dict):
            diags.append(Diagnostic("error", "BAD_STEP", f"Step must be a mapping, found {type(st).__name__}.",
                                    scenario=label, path=spath, line=ln))
            continue
        kw = st.get("keyword")
        if kw not in KEYWORDS:
            diags.append(Diagnostic("error", "BAD_KEYWORD",
                                    f"Step keyword `{kw}` is not one of {', '.join(sorted(KEYWORDS))}.",
                                    scenario=label, path=spath, line=ln))
        if not str(st.get("text") or "").strip():
            diags.append(Diagnostic("warning", "EMPTY_STEP", "Step has no `text`.",
                                    scenario=label, path=spath, line=ln))
        for key in st:
            if key not in STEP_KEYS:
                diags.append(Diagnostic("warning", "UNKNOWN_KEY", f"Unknown step key `{key}`.",
                                        scenario=label, path=f"{spath}.{key}", line=ln))
        a = st.get("assertion")
        if a is not None:
            if not isinstance(a, dict):
                diags.append(Diagnostic("error", "BAD_ASSERTION",
                                        f"`assertion` must be a mapping, found {type(a).__name__}.",
                                        scenario=label, path=f"{spath}.assertion", line=ln))
            else:
                for key in a:
                    if key not in ASSERTION_KEYS:
                        diags.append(Diagnostic("warning", "UNKNOWN_KEY", f"Unknown assertion key `{key}`.",
                                                scenario=label, path=f"{spath}.assertion.{key}", line=ln))
        dc = st.get("decision_context")
        if dc is not None:
            if not isinstance(dc, list):
                diags.append(Diagnostic("error", "BAD_DECISION_CONTEXT",
                                        f"`decision_context` must be a list, found {type(dc).__name__}.",
                                        scenario=label, path=f"{spath}.decision_context", line=ln))
            else:
                for k, entry in enumerate(dc):
                    if not isinstance(entry, dict) or not str(entry.get("source") or "").strip():
                        diags.append(Diagnostic("warning", "BAD_DECISION_ENTRY",
                                                "Each decision_context entry needs a `source` (and usually a `key`).",
                                                scenario=label, path=f"{spath}.decision_context[{k}]", line=ln))


def _check_refs(scenarios: list, declared: dict, seen_ids: dict, diags: list, lines: dict) -> None:
    for i, sc in enumerate(scenarios):
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id") or "")
        label = sid or str(sc.get("name") or "") or f"scenarios[{i}]"
        deps = sc.get("depends_on")
        if not isinstance(deps, list):
            continue
        for d in deps:
            ref = str(d)
            if ref == sid:
                diags.append(Diagnostic("error", "SELF_DEP", f"`{sid}` depends on itself.",
                                        scenario=label, path=f"scenarios[{i}].depends_on", line=lines.get(i)))
            elif ref not in seen_ids:
                diags.append(Diagnostic("error", "BAD_REF",
                                        f"`depends_on` references unknown scenario `{ref}`.",
                                        scenario=label, path=f"scenarios[{i}].depends_on", line=lines.get(i)))


def _check_cycles(declared: dict, diags: list) -> None:
    state: dict = {}
    reported: set = set()

    def visit(node: str, stack: list) -> None:
        if state.get(node) == 1:
            cycle = stack[stack.index(node):] + [node]
            key = frozenset(cycle)
            if key not in reported:
                reported.add(key)
                diags.append(Diagnostic("error", "CYCLE", "Dependency cycle: " + " -> ".join(cycle), scenario=node))
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in declared.get(node, []):
            if dep in declared:
                visit(dep, stack + [node])
        state[node] = 2

    for sid in declared:
        if state.get(sid) is None:
            visit(sid, [])


def validate_file(path) -> list:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return [Diagnostic("error", "UNREADABLE", f"Cannot read {p}: {e}")]
    return validate_text(text, source=p.name)


def validate_requirements_file(path) -> list:
    """Diagnostics for a hand-written requirements file.

    ``requirements.load`` already collects its own problems rather than raising;
    this turns them into the same Diagnostic shape so one command reports both.
    """
    from .requirements import load

    p = Path(path)
    if not p.exists():
        return [Diagnostic("error", "NO_FILE", f"No requirements file at {p}.")]
    reqs = load(p)
    diags = [Diagnostic("warning", "REQUIREMENT", prob) for prob in reqs.problems]
    if not reqs.items:
        diags.append(Diagnostic("warning", "NO_REQUIREMENTS", f"{p} declares no requirements."))
    return diags
