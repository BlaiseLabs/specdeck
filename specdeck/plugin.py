"""specdeck pytest plugin: tests are the source of truth; the spec is generated.

On every run the plugin records, per test: scenario metadata (declared via the
``scenario`` marker or derived from module / function name / docstring), the
Given/When/Then steps that actually executed, the outcome, and — for a failing
``then`` — the expected/observed labels and decision context. At session end it
writes a test-failure-DX YAML (warning-free under :mod:`specdeck.validation`)
plus a minimal sphinx-needs ``needs.json``.

Design rules, deliberately:
- ``depends_on`` is *documentation order* plus skip-if-a-prerequisite-failed.
  It never reorders execution (runtime coupling between tests is an anti-pattern).
- Output is stamped with pass/fail per scenario (``status:<outcome>`` tag), which is
  what distinguishes a generated spec from a reverse-engineered guess.
- Every string is emitted via ``yaml.safe_dump`` so ``key: value`` text inside a
  step can never produce an unparseable file.
"""
from __future__ import annotations

import ast
import contextvars
import json
import inspect
import textwrap
import datetime as _dt
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import pytest
import yaml

from .editing import step_calls
from .needs import write_needs

__version__ = "0.1.0"

# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    keyword: str
    text: str
    assertion: Optional[dict] = None
    decision_context: Optional[list] = None
    failed: bool = False  # this step is where the test failed

    def to_dict(self) -> dict:
        d: dict = {"keyword": self.keyword, "text": self.text}
        if self.assertion:
            a = {k: v for k, v in self.assertion.items() if v is not None}
            if a:
                d["assertion"] = a
        if self.decision_context:
            d["decision_context"] = list(self.decision_context)
        return d


@dataclass
class ScenarioRecord:
    id: str
    name: str
    group: str
    group_name: str
    nodeid: str
    description: Optional[str] = None
    tags: list = field(default_factory=list)
    depends_on: list = field(default_factory=list)
    covers: list = field(default_factory=list)          # requirement ids declared with covers=
    examples_used: list = field(default_factory=list)   # [(requirement id, given)] expected values came from
    steps: list = field(default_factory=list)
    # Steps read from the test's source at collection time: (keyword, text, expected).
    # They narrate scenarios that never ran, and the steps a failure never reached.
    planned: list = field(default_factory=list)
    status: str = "pending"  # pending | passed | failed | skipped | error
    duration: float = 0.0
    message: Optional[str] = None


class _Store:
    def __init__(self) -> None:
        self.records: dict[str, ScenarioRecord] = {}  # nodeid -> record
        self.order: list[str] = []
        self.groups: dict[str, str] = {}  # group id -> name (insertion ordered)
        self.warnings: list[str] = []
        # test function name -> scenario ids, so depends_on=["test_base"] resolves
        # even when test_base declares an explicit id. Parametrized tests map one
        # function name to every instance's id (depend on one = depend on all).
        self.fn_index: dict[tuple[str, str], list[str]] = {}  # (group, fn) -> ids
        self.fn_global: dict[str, list[str]] = {}  # fn -> ids across groups
        self.aliases: dict[str, list[str]] = {}  # explicit id of a parametrized test -> its cases' ids
        self.tracer = None  # tracing.Recorder, when --specdeck-coverage is on

    def by_id(self) -> dict[str, ScenarioRecord]:
        return {r.id: r for r in self.records.values()}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _slug(value: Any) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s or "x"


def _humanize(slug: str) -> str:
    words = [w for w in re.split(r"[-_]+", slug) if w]
    return " ".join(words).capitalize() if words else slug


def _first_line(text: Optional[str]) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def _first_sentence(text: Optional[str]) -> str:
    """Scenario name from a docstring: the first sentence of the first paragraph,
    collapsed to one line — so a wrapped first line never yields a name that
    ends mid-sentence with a comma."""
    if not text:
        return ""
    para = text.strip().split("\n\n", 1)[0]
    one = " ".join(para.split())
    m = re.search(r"[.!?](\s|$)", one)
    return one[: m.end()].strip() if m else one


def _rest_lines(text: Optional[str]) -> Optional[str]:
    """Docstring body after the first paragraph, or None."""
    if not text:
        return None
    parts = text.strip().split("\n\n", 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    return body or None


def _module_slug(item: pytest.Item) -> str:
    modname = getattr(getattr(item, "module", None), "__name__", "") or item.nodeid.split("::")[0]
    base = modname.rsplit(".", 1)[-1]
    base = re.sub(r"^test_", "", base)
    base = re.sub(r"_test$", "", base)
    base = re.sub(r"\.py$", "", base)
    return _slug(base)


def _norm_decision_context(dc: Any) -> Optional[list]:
    """Accept [("CLI --output", "cli_output"), {"source":..,"key":..}, "Default"]."""
    if not dc:
        return None
    out = []
    for entry in dc:
        if isinstance(entry, dict):
            out.append({"source": str(entry.get("source", "")), "key": str(entry.get("key", ""))})
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            out.append({"source": str(entry[0]), "key": str(entry[1])})
        else:
            out.append({"source": str(entry), "key": _slug(entry)})
    return out


# --------------------------------------------------------------------------- #
# Public API: marker + steps
# --------------------------------------------------------------------------- #
def scenario(
    id: Optional[str] = None,
    *,
    name: Optional[str] = None,
    group: Optional[str] = None,
    tags: Iterable[str] = (),
    depends_on: Iterable[str] = (),
    description: Optional[str] = None,
    covers: Iterable[str] = (),
):
    """Declare spec metadata for a test. Everything is optional — omitted fields
    are derived (id from the function name, group from the module, name from the
    docstring's first line). `covers` lists the requirement ids (requirements.yaml)
    this test is evidence for."""
    return pytest.mark.scenario(
        id=id,
        name=name,
        group=group,
        tags=tuple(tags),
        depends_on=tuple(depends_on),
        description=description,
        covers=tuple(covers),
    )


_ACTIVE: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar("specdeck_steps", default=None)


@contextmanager
def step(
    keyword: str,
    text: str,
    *,
    expected: Optional[str] = None,
    observed: Optional[str] = None,
    decision_context: Any = None,
):
    """Record a step as it executes. Outside a test this is a harmless no-op."""
    st = Step(keyword=keyword, text=text, decision_context=_norm_decision_context(decision_context))
    steps = _ACTIVE.get()
    if steps is not None:
        steps.append(st)
    try:
        yield st
    except AssertionError as exc:  # capture the failure labels, then re-raise
        first = _first_line(str(exc))
        if not getattr(exc, "_specdeck_pinned", False):
            st.failed = True
            try:
                exc._specdeck_pinned = True  # type: ignore[attr-defined]
            except Exception:
                pass
        st.assertion = {
            "expected_label": expected,
            "observed_label": observed if observed is not None else (first or "assertion failed"),
        }
        raise
    except Exception as exc:  # e.g. NotImplementedError: pin it to this step only
        if not getattr(exc, "_specdeck_pinned", False):
            st.failed = True
            first = _first_line(str(exc))
            st.assertion = {
                "expected_label": expected,
                "observed_label": f"{type(exc).__name__}: {first}" if first else type(exc).__name__,
            }
            try:
                exc._specdeck_pinned = True  # type: ignore[attr-defined]
            except Exception:
                pass
        raise
    else:
        if expected is not None or observed is not None:
            st.assertion = {
                "expected_label": expected,
                "observed_label": observed if observed is not None else expected,
            }


def given(text: str, **kw):
    return step("Given", text, **kw)


def when(text: str, **kw):
    return step("When", text, **kw)


def then(text: str, **kw):
    return step("Then", text, **kw)


def and_(text: str, **kw):
    return step("And", text, **kw)


# --------------------------------------------------------------------------- #
# Static steps: read given/when/then/and_ calls from the test source
# --------------------------------------------------------------------------- #
def _render(expr: ast.expr, ns: dict) -> Optional[str]:
    """Evaluate a step's text / expected expression without running test code.

    Only literals, names, attribute access, subscripts and f-strings are
    evaluated (never calls); anything else, or a name that only exists at
    runtime, falls back to the expression's source."""
    if isinstance(expr, ast.Constant):
        return None if expr.value is None else str(expr.value)
    if not any(isinstance(n, (ast.Call, ast.Lambda, ast.NamedExpr, ast.Await)) for n in ast.walk(expr)):
        try:
            value = eval(compile(ast.Expression(expr), "<specdeck>", "eval"), {"__builtins__": {}}, dict(ns))
            return str(value)
        except Exception:
            pass
    if isinstance(expr, ast.JoinedStr):  # keep the template readable: "a line containing {needle!r}"
        parts = []
        for v in expr.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            else:
                parts.append("{" + ast.unparse(v.value) + "}")
        return "".join(parts)
    return ast.unparse(expr)


def _planned_steps(item: pytest.Item) -> list:
    fn = getattr(item, "function", None)
    if fn is None:
        return []
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(fn))))
    except (OSError, TypeError, SyntaxError):
        return []
    ns = dict(getattr(fn, "__globals__", {}))
    callspec = getattr(item, "callspec", None)
    if callspec is not None:
        ns.update(callspec.params)
    out: list = []
    for keyword, _call, text_node, exp_kw in step_calls(tree):
        out.append((keyword, _render(text_node, ns) or "",
                    _render(exp_kw.value, ns) if exp_kw is not None else None))
    return out


# --------------------------------------------------------------------------- #
# Plugin hooks
# --------------------------------------------------------------------------- #
def pytest_addoption(parser: pytest.Parser) -> None:
    g = parser.getgroup("specdeck", "specdeck: generate a spec from the tests")
    g.addoption("--no-specdeck", action="store_true", default=False, help="do not write the spec")
    g.addoption("--specdeck-out", default=None, help="override the spec output path")
    parser.addini("specdeck_feature", "feature name for the generated spec", default="")
    parser.addini("specdeck_description", "feature description for the generated spec", default="")
    parser.addini("specdeck_out", "spec output path (relative to rootdir)", default=".specdeck/spec.yaml")
    parser.addini("specdeck_needs", "needs.json output path (relative to rootdir)", default=".specdeck/needs.json")
    parser.addini("specdeck_requirements", "hand-written requirements file (relative to rootdir)",
                  default="requirements.yaml")
    parser.addini("specdeck_requirements_out", "requirements report output path (relative to rootdir)",
                  default=".specdeck/requirements.json")
    g.addoption("--specdeck-live", action="store_true", default=False,
                help="record this run in the specdeck viewer and show it live (starts the viewer if needed)")
    g.addoption("--specdeck-record", action="store_true", default=False,
                help="record this run in the viewer's history without starting the viewer")
    g.addoption("--no-specdeck-live", action="store_true", default=False,
                help="do not record this run, even if specdeck_live is set in the config")
    g.addoption("--specdeck-port", type=int, default=None, help="viewer port (default 7357)")
    parser.addini("specdeck_live", "record runs for the viewer: true (and start it), record, or false", default="false")
    parser.addini("specdeck_port", "viewer port", default="7357")
    # CI gates — all off by default; adding specdeck must not change an exit code.
    g.addoption("--specdeck-fail-on-unmet", action="store_true", default=False,
                help="exit non-zero if any requirement is not met")
    g.addoption("--specdeck-fail-on-unknown-covers", action="store_true", default=False,
                help="exit non-zero if a covers= names a requirement that doesn't exist")
    g.addoption("--specdeck-fail-on-invalid", action="store_true", default=False,
                help="exit non-zero if the generated spec has validation errors")
    parser.addini("specdeck_fail_on_unmet", "fail the run if a requirement is not met", default="false")
    parser.addini("specdeck_fail_on_unknown_covers", "fail the run on an unknown covers= id", default="false")
    parser.addini("specdeck_fail_on_invalid", "fail the run if the generated spec is invalid", default="false")
    g.addoption("--specdeck-coverage", action="store_true", default=False,
                help="record which source functions each scenario executes (needs coverage.py), for "
                     "`specdeck context`")
    parser.addini("specdeck_coverage", "record per-scenario source coverage: true or false", default="false")
    parser.addini("specdeck_coverage_out", "coverage map output path (relative to rootdir)",
                  default=".specdeck/coverage.json")
    g.addoption("--specdeck-merge", action="store_true", default=False,
                help="on a partial run, merge the scenarios that ran into the existing spec instead of skipping the write")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "scenario(id=None, name=None, group=None, tags=(), depends_on=(), description=None): "
        "specdeck scenario metadata for this test",
    )
    config.addinivalue_line("markers", "specdeck_example(requirement, given): a case built by specdeck.examples()")
    config._specdeck = _Store()  # type: ignore[attr-defined]
    if (config.getoption("--specdeck-coverage") or _ini_bool(config, "specdeck_coverage")) \
            and not hasattr(config, "workerinput"):
        from . import tracing

        recorder = tracing.Recorder([str(config.rootpath)])
        try:
            recorder.start()  # before collection, so module-level lines land in the import context
            config._specdeck.tracer = recorder
        except tracing.Unavailable as exc:
            config._specdeck.warnings.append(f"coverage not recorded: {exc}")
    from . import requirements as _reqs

    req_path = Path(config.getini("specdeck_requirements") or _reqs.DEFAULT_FILE)
    req_path = req_path if req_path.is_absolute() else Path(str(config.rootpath)) / req_path
    config._specdeck_reqs = _reqs.load(req_path)  # type: ignore[attr-defined]
    _reqs.set_loaded(config._specdeck_reqs)
    if not hasattr(config, "workerinput"):  # xdist workers don't record
        from .recorder import LiveRecorder, live_mode

        mode = live_mode(config)
        if mode:
            config.pluginmanager.register(LiveRecorder(config, mode), "specdeck-live")


def _finish_coverage(config: pytest.Config, store: "_Store") -> Optional[dict]:
    """Stop the tracer and map the lines each test ran to the functions that own them."""
    if store.tracer is None:
        return None
    from . import tracing

    per_test = store.tracer.stop()
    store.tracer = None
    if not per_test:
        return None
    # A suite's own test files are not what a reviewer holds a diff of; leave them out.
    test_dirs = tuple({str(Path(n.split("::")[0]).parent) + "/" for n in per_test} | {"/test_", "conftest.py"})
    return tracing.to_functions(per_test, config.rootpath, skip=test_dirs)


def _store(config: pytest.Config) -> Optional[_Store]:
    return getattr(config, "_specdeck", None)


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list) -> None:
    store = _store(config)
    if store is None:
        return
    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, pytest.Function):
            continue
        marker = item.get_closest_marker("scenario")
        kw = dict(marker.kwargs) if marker else {}

        group = _slug(kw.get("group")) if kw.get("group") else _module_slug(item)
        mod_doc = getattr(getattr(item, "module", None), "__doc__", None)
        group_name = _first_line(mod_doc) if group == _module_slug(item) and mod_doc else _humanize(group)
        store.groups.setdefault(group, group_name)

        fn_slug = _slug(re.sub(r"^test_", "", item.name))  # keeps [param] ids -> -param
        parametrized = bool(item.originalname) and item.name != item.originalname
        alias_for = None
        if kw.get("id") and parametrized:  # one id per case: tier-examples-qty-100
            sid = _slug(f"{kw['id']}-{item.name[len(item.originalname):].strip('[]')}")
            alias_for = _slug(kw["id"])
        else:
            sid = _slug(kw["id"]) if kw.get("id") else f"{group}--{fn_slug}"
        base = sid
        n = 2
        while sid in seen_ids:
            sid, n = f"{base}-{n}", n + 1
        if sid != base:
            store.warnings.append(f"duplicate scenario id {base!r} -> {sid!r} ({item.nodeid})")
        seen_ids.add(sid)
        if alias_for is not None:
            # Register AFTER de-duplication: `sid` may have just been renamed, and
            # an alias pointing at the pre-rename id would resolve to a scenario
            # that does not exist — a dangling depends_on in the emitted spec.
            store.aliases.setdefault(alias_for, []).append(sid)
        fn_name = item.originalname or item.name
        store.fn_index.setdefault((group, fn_name), []).append(sid)
        store.fn_global.setdefault(fn_name, []).append(sid)

        doc = getattr(item.obj, "__doc__", None)
        name = kw.get("name") or _first_sentence(doc) or _humanize(re.sub(r"^test_", "", item.originalname or item.name))
        if item.originalname and item.name != item.originalname:  # parametrized
            name = f"{name} [{item.name[len(item.originalname):].strip('[]')}]"

        store.records[item.nodeid] = ScenarioRecord(
            id=sid,
            name=name,
            group=group,
            group_name=group_name,
            nodeid=item.nodeid,
            description=kw.get("description") or _rest_lines(doc),
            tags=[t for t in kw.get("tags", ()) if t],
            depends_on=list(kw.get("depends_on", ())),
            planned=_planned_steps(item),
            covers=[str(c) for c in kw.get("covers", ()) if c],
            examples_used=[(m.args[0], dict(m.args[1])) for m in item.iter_markers("specdeck_example")
                           if len(m.args) >= 2],
        )
        store.order.append(item.nodeid)

    _resolve_dependencies(store)


def _resolve_dependencies(store: _Store) -> None:
    """Turn each depends_on entry into concrete scenario ids.

    Accepted forms, tried in order: an exact scenario id; a test function name
    (with or without the ``test_`` prefix) in the same group, then anywhere if
    unambiguous; a derived slug in the same group; a unique ``--<slug>`` suffix.
    Anything unresolved is dropped with a warning rather than emitted as a
    dangling reference (which the viewer's validator would flag as an error).
    """
    by_id = store.by_id()
    for rec in store.records.values():
        resolved: list[str] = []
        for dep in rec.depends_on:
            dep_s = str(dep)
            # Every hit is checked against the real scenario ids: whatever a
            # resolution route returns, a reference that is not a scenario must
            # never reach the spec (the validator rightly calls that an error).
            hits = [h for h in _resolve_one(store, by_id, rec, dep_s) if h in by_id]
            if hits:
                resolved.extend(h for h in hits if h not in resolved and h != rec.id)
            else:
                store.warnings.append(f"{rec.id}: depends_on {dep_s!r} does not match any scenario — dropped")
        rec.depends_on = resolved
    _warn_cycles(store)


def _resolve_one(store: _Store, by_id: dict, rec: ScenarioRecord, dep_s: str) -> list[str]:
    if dep_s in by_id:
        return [dep_s]
    if _slug(dep_s) in store.aliases:
        return list(store.aliases[_slug(dep_s)])
    fn_variants = [dep_s] if dep_s.startswith("test_") else [f"test_{dep_s}", dep_s]
    for fn in fn_variants:
        if (rec.group, fn) in store.fn_index:
            return list(store.fn_index[(rec.group, fn)])
    for fn in fn_variants:
        ids = store.fn_global.get(fn, [])
        groups = {by_id[i].group for i in ids if i in by_id}
        if ids and len(groups) == 1:
            return list(ids)
    bare = _slug(re.sub(r"^test_", "", dep_s))
    same = f"{rec.group}--{bare}"
    if same in by_id:
        return [same]
    anywhere = [i for i in by_id if i.endswith(f"--{bare}")]
    if len(anywhere) == 1:
        return anywhere
    return []


def _warn_cycles(store: _Store) -> None:
    by_id = store.by_id()
    state: dict[str, int] = {}

    def visit(n: str, path: list) -> None:
        if state.get(n) == 1:
            cycle = path[path.index(n):] + [n]
            store.warnings.append("dependency cycle: " + " -> ".join(cycle))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in by_id[n].depends_on:
            if d in by_id:
                visit(d, path + [n])
        state[n] = 2

    for sid in by_id:
        if state.get(sid) is None:
            visit(sid, [])


def pytest_runtest_setup(item: pytest.Item) -> None:
    store = _store(item.config)
    if store is None:
        return
    if store.tracer is not None:
        store.tracer.switch(item.nodeid)
    if item.nodeid not in store.records:
        return
    rec = store.records[item.nodeid]
    by_id = store.by_id()
    for dep in rec.depends_on:
        d = by_id.get(dep)
        if d is None:
            continue
        if d.status in ("failed", "error"):
            pytest.skip(f"specdeck: prerequisite scenario {dep!r} {d.status}")
        if d.status == "skipped" and (d.message or "").startswith("specdeck: prerequisite scenario"):
            pytest.skip(f"specdeck: prerequisite scenario {dep!r} blocked")  # blocking cascades


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    store = _store(item.config)
    rec = store.records.get(item.nodeid) if store else None
    from .requirements import _USED

    token = _ACTIVE.set(rec.steps if rec is not None else None)
    used = _USED.set(rec.examples_used if rec is not None else None)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        _USED.reset(used)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    rep = outcome.get_result()
    store = _store(item.config)
    rec = store.records.get(item.nodeid) if store else None
    if rec is None:
        return
    if rep.when == "setup" and rep.outcome != "passed":
        rec.status = "skipped" if rep.outcome == "skipped" else "error"
        rec.message = _first_line(rep.longreprtext) if rep.outcome != "skipped" else _skip_reason(rep)
    elif rep.when == "call":
        rec.status = rep.outcome  # passed | failed | skipped
        rec.duration = float(rep.duration or 0.0)
        if rep.outcome == "failed":
            rec.message = _failure_message(rep)
        elif rep.outcome == "skipped":
            rec.message = _skip_reason(rep)


def _skip_reason(rep) -> Optional[str]:
    lr = rep.longrepr
    if isinstance(lr, tuple) and len(lr) >= 3:
        return str(lr[2]).replace("Skipped: ", "", 1)
    return _first_line(rep.longreprtext) or None


def _failure_message(rep) -> str:
    text = rep.longreprtext or ""
    # prefer the pytest "E   ..." assertion line, else the exception line
    for line in text.splitlines():
        if line.startswith("E "):
            return line[1:].strip()
    return _first_line(text) or "failed"


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #
def _feature_name(config: pytest.Config) -> str:
    return config.getini("specdeck_feature") or _humanize(_slug(Path(str(config.rootpath)).name))


def covered_requirements(rec: ScenarioRecord) -> list:
    """Declared covers=, plus every requirement whose examples the test took expected values from."""
    return list(dict.fromkeys([*rec.covers, *(rid for rid, _ in rec.examples_used)]))


def _outcome(rec: ScenarioRecord) -> str:
    if rec.status == "skipped" and (rec.message or "").startswith("specdeck: prerequisite scenario"):
        return "blocked"
    return rec.status if rec.status != "pending" else "notrun"


def requirements_report(config: pytest.Config, store: "_Store") -> Optional[dict]:
    from . import requirements as _reqs

    reqs = getattr(config, "_specdeck_reqs", None) or _reqs.Requirements()
    ordered = [store.records[n] for n in store.order if n in store.records]
    if not reqs.items and not reqs.problems and not any(covered_requirements(r) for r in ordered):
        return None
    scenarios = [{"id": r.id, "name": r.name, "status": _outcome(r), "nodeid": r.nodeid,
                  "covers": covered_requirements(r), "examples_used": [[rid, g] for rid, g in r.examples_used]}
                 for r in ordered]
    return _reqs.report(reqs, scenarios)


def _scenario_dict(rec: ScenarioRecord) -> dict:
    steps = [s.to_dict() for s in rec.steps]
    ran = [s.keyword for s in rec.steps]
    if rec.status != "passed" and rec.planned and ran == [p[0] for p in rec.planned[: len(ran)]]:
        # the rest of the scenario as written: never run (skipped) or not reached (failed)
        pending = "not run" if not ran else "not reached"
        for keyword, text, expected in rec.planned[len(ran):]:
            steps.append({"keyword": keyword, "text": text,
                          "assertion": {k: v for k, v in (("expected_label", expected),
                                                          ("observed_label", pending)) if v is not None}})
    if not steps:  # un-annotated test: synthesize from the outcome
        steps = [{"keyword": "When", "text": f"pytest runs {rec.nodeid}"}]
        verdict = {"passed": "it passes", "failed": "it fails", "skipped": "it is skipped",
                   "error": "it errors during setup"}.get(rec.status, f"it is {rec.status}")
        then_step: dict = {"keyword": "Then", "text": verdict}
        if rec.status in ("failed", "error"):
            then_step["assertion"] = {"expected_label": "passes", "observed_label": rec.message or rec.status}
        steps.append(then_step)
    elif rec.status in ("failed", "error") and not any(s.failed for s in rec.steps):
        steps.append({"keyword": "Then", "text": "the test passes",
                      "assertion": {"expected_label": "passes", "observed_label": rec.message or rec.status}})

    d: dict = {"id": rec.id, "name": rec.name}
    if rec.description:
        d["description"] = rec.description
    d["group"] = rec.group
    d["tags"] = list(dict.fromkeys([*rec.tags, rec.group, f"status:{rec.status}",
                                    *(f"req:{r}" for r in covered_requirements(rec))]))
    if rec.depends_on:
        d["depends_on"] = list(rec.depends_on)
    d["steps"] = steps
    d["logic"] = [f"pytest {rec.nodeid}"]
    return d


def _merge_partial(config: pytest.Config, store: _Store) -> None:
    """Update only the scenarios that ran, leaving the rest of the spec intact.

    A partial run (`-k`, a node id, one file) knows nothing about the scenarios
    it did not collect, so rewriting the file would delete them. Merging keeps
    every untouched scenario exactly as it was and replaces the ones that ran,
    in place, so the order of the file is stable.
    """
    out = Path(config.getini("specdeck_out"))
    out = out if out.is_absolute() else Path(str(config.rootpath)) / out
    tr = config.pluginmanager.get_plugin("terminalreporter")
    if not out.exists():
        if tr is not None:
            tr.write_line(f"specdeck: --specdeck-merge needs an existing {out.name}; "
                          f"run the whole suite once first", yellow=True)
        return
    try:
        existing = yaml.safe_load(out.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        if tr is not None:
            tr.write_line(f"specdeck: cannot merge, {out.name} is not valid YAML: {e}", yellow=True)
        return

    # Only scenarios that actually RAN may replace what is on disk. Collection
    # happens before -k/-m deselection, so `store` also holds scenarios that were
    # collected and then dropped; merging those would overwrite a real recorded
    # outcome with `pending`.
    fresh = {r.id: _scenario_dict(r)
             for r in (store.records[n] for n in store.order if n in store.records)
             if r.status != "pending"}
    scenarios = existing.get("scenarios") if isinstance(existing.get("scenarios"), list) else []
    merged, replaced = [], 0
    for sc in scenarios:
        sid = str(sc.get("id") or "") if isinstance(sc, dict) else ""
        if sid in fresh:
            merged.append(fresh.pop(sid))
            replaced += 1
        else:
            merged.append(sc)
    added = list(fresh.values())  # scenarios that are new since the last full run
    existing["scenarios"] = merged + added

    known = {g.get("id") for g in existing.get("groups", []) if isinstance(g, dict)}
    for gid, gname in store.groups.items():
        if gid not in known:
            existing.setdefault("groups", []).append({"id": gid, "name": gname})

    header = (f"# GENERATED by specdeck {__version__} from pytest — do not edit.\n"
              f"# Re-run pytest to regenerate. Scenarios carry a status:<outcome> tag.\n")
    out.write_text(header + yaml.safe_dump(existing, sort_keys=False, allow_unicode=True, width=100),
                   encoding="utf-8")
    if tr is not None:
        extra = f", {len(added)} added" if added else ""
        tr.write_line(f"specdeck: merged {replaced} scenario(s){extra} into {out.name} "
                      f"({len(merged) + len(added)} total)")


def build_spec(config: pytest.Config, store: _Store) -> dict:
    ordered = [store.records[n] for n in store.order if n in store.records]
    spec: dict = {"feature": _feature_name(config)}
    desc = config.getini("specdeck_description")
    if desc:
        spec["description"] = desc
    spec["groups"] = [{"id": gid, "name": gname} for gid, gname in store.groups.items()]
    spec["scenarios"] = [_scenario_dict(r) for r in ordered]
    return spec


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    store = _store(config)
    if store is None:
        return
    coverage_map = _finish_coverage(config, store)   # always stop the tracer, whatever else happens
    if not store.records or config.getoption("--no-specdeck"):
        return
    from .recorder import is_full_run

    partial = not is_full_run(config) and not config.getoption("--specdeck-out")
    if partial and config.getoption("--specdeck-merge"):
        _merge_partial(config, store)
        return
    if partial:
        # A selection (one file, -k, a node id) would shrink the spec to that subset.
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line("specdeck: partial run, spec not rewritten "
                          "(run the whole suite, or --specdeck-merge to update just these scenarios)")
        return
    spec = build_spec(config, store)
    out = Path(config.getoption("--specdeck-out") or config.getini("specdeck_out"))
    out = out if out.is_absolute() else Path(str(config.rootpath)) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    # Deterministic on purpose: no timestamp, so the file only changes when the
    # tests (or their outcomes) change and a committed copy diffs meaningfully.
    header = (
        f"# GENERATED by specdeck {__version__} from pytest — do not edit.\n"
        f"# Re-run pytest to regenerate. Scenarios carry a status:<outcome> tag.\n"
        f"#\n"
        f"# depends_on is documentation order, not execution order: specdeck never reorders\n"
        f"# tests, and scenarios are listed below in the order they run. A scenario is skipped\n"
        f"# as blocked only when a prerequisite that ran EARLIER did not pass; blocking\n"
        f"# cascades. A scenario whose only failed prerequisite runs later is not skipped —\n"
        f"# it runs, and can fail on its own. (Omitting this cost 60 points of accuracy when\n"
        f"# an agent was asked to sort real failures from blocked ones; see specdeck context.)\n"
    )
    out.write_text(header + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")

    if coverage_map is not None:
        cp = Path(config.getini("specdeck_coverage_out"))
        cp = cp if cp.is_absolute() else Path(str(config.rootpath)) / cp
        cp.parent.mkdir(parents=True, exist_ok=True)
        by_scenario = {store.records[n].id: places for n, places in coverage_map.items() if n in store.records}
        cp.write_text(json.dumps(by_scenario, indent=1, sort_keys=True), encoding="utf-8")

    needs_path = Path(config.getini("specdeck_needs"))
    needs_path = needs_path if needs_path.is_absolute() else Path(str(config.rootpath)) / needs_path
    ordered = [store.records[n] for n in store.order if n in store.records]
    write_needs(needs_path, spec["feature"], ordered, stamp)

    # The requirements report is written whether or not a terminal reporter is
    # present (`-p no:terminal` used to drop it silently); only printing is
    # conditional.
    rep = requirements_report(config, store)
    rp = None
    if rep is not None:
        rp = Path(config.getini("specdeck_requirements_out"))
        rp = rp if rp.is_absolute() else Path(str(config.rootpath)) / rp
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")

    tr = config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        edges = sum(len(r.depends_on) for r in ordered)
        counts = {}
        for r in ordered:
            counts[r.status] = counts.get(r.status, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        tr.write_line(
            f"specdeck: wrote {out.relative_to(config.rootpath) if out.is_relative_to(config.rootpath) else out} "
            f"({len(ordered)} scenarios, {len(store.groups)} groups, {edges} edges; {summary}) "
            f"+ {needs_path.name}"
        )
        if rep is not None:
            c = rep["counts"]
            bits = [f"{c['met']} of {c['total']} met"]
            for k in ("failing", "blocked", "not-run", "untested"):
                ids = [r["id"] for r in rep["requirements"] if r["status"] == k]
                if k == "untested":   # a declared non-test method (§6.5.2) is not a gap in the tests
                    declared = [r for r in rep["requirements"] if r["status"] == k
                                and any(m != "test" for m in r.get("verification") or [])]
                    ids = [i for i in ids if i not in {r["id"] for r in declared}]
                    if declared:
                        bits.append(f"{len(declared)} verified another way")
                if ids:
                    named = f" ({', '.join(ids[:4])}{', …' if len(ids) > 4 else ''})" if k in ("failing", "blocked") else ""
                    bits.append(f"{len(ids)} {k}{named}")
            tr.write_line(f"specdeck: requirements: {', '.join(bits)} + {rp.name}")
            for u in rep["unknown"]:
                tr.write_line(f"specdeck: warning: {', '.join(u['scenarios'])} covers {u['id']!r}, "
                              f"which isn't in the requirements file", yellow=True)
            for prob in rep["problems"]:
                tr.write_line(f"specdeck: warning: {prob}", yellow=True)
        for w in store.warnings:
            tr.write_line(f"specdeck: warning: {w}", yellow=True)

    _apply_gates(config, session, rep, out, tr)


def _apply_gates(config: pytest.Config, session: pytest.Session, rep, spec_path: Path, tr) -> None:
    """Turn advisory findings into a failing run, when asked.

    Off by default: adding specdeck to an existing suite must never change its
    exit code until someone opts in.
    """
    def enabled(flag: str, ini: str) -> bool:
        return bool(config.getoption(flag) or _ini_bool(config, ini))

    failures: list = []

    if enabled("--specdeck-fail-on-unknown-covers", "specdeck_fail_on_unknown_covers") and rep:
        for u in rep["unknown"]:
            failures.append(f"{', '.join(u['scenarios'])} covers {u['id']!r}, which isn't in the requirements file")

    if enabled("--specdeck-fail-on-unmet", "specdeck_fail_on_unmet") and rep:
        for r in rep["requirements"]:
            if r["status"] != "met":
                failures.append(f"requirement {r['id']!r} is {r['status']}")

    if enabled("--specdeck-fail-on-invalid", "specdeck_fail_on_invalid"):
        from .validation import is_valid, validate_file

        diags = validate_file(spec_path)
        if not is_valid(diags):
            for d in diags:
                if d.severity == "error":
                    failures.append(f"spec invalid: {d.code}: {d.message}")

    if not failures:
        return
    if tr is not None:
        tr.write_line("")
        tr.write_line(f"specdeck: gate failed ({len(failures)}):", red=True)
        for f in failures:
            tr.write_line(f"  {f}", red=True)
    session.exitstatus = 1


def _ini_bool(config: pytest.Config, name: str) -> bool:
    return str(config.getini(name) or "").strip().lower() in ("1", "true", "yes", "on")
