"""specdeck pytest plugin: tests are the source of truth; the spec is generated.

On every run the plugin records, per test: scenario metadata (declared via the
``scenario`` marker or derived from module / function name / docstring), the
Given/When/Then steps that actually executed, the outcome, and — for a failing
``then`` — the expected/observed labels and decision context. At session end it
writes a test-failure-DX YAML (readable by dx-spec-viewer, warning-free under its
validator) plus a minimal sphinx-needs ``needs.json``.

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
):
    """Declare spec metadata for a test. Everything is optional — omitted fields
    are derived (id from the function name, group from the module, name from the
    docstring's first line)."""
    return pytest.mark.scenario(
        id=id,
        name=name,
        group=group,
        tags=tuple(tags),
        depends_on=tuple(depends_on),
        description=description,
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
    g.addoption("--specdeck-live", action="store_true", default=False,
                help="record this run in the specdeck viewer and show it live (starts the viewer if needed)")
    g.addoption("--specdeck-record", action="store_true", default=False,
                help="record this run in the viewer's history without starting the viewer")
    g.addoption("--no-specdeck-live", action="store_true", default=False,
                help="do not record this run, even if specdeck_live is set in the config")
    g.addoption("--specdeck-port", type=int, default=None, help="viewer port (default 7357)")
    parser.addini("specdeck_live", "record runs for the viewer: true (and start it), record, or false", default="false")
    parser.addini("specdeck_port", "viewer port", default="7357")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "scenario(id=None, name=None, group=None, tags=(), depends_on=(), description=None): "
        "specdeck scenario metadata for this test",
    )
    config._specdeck = _Store()  # type: ignore[attr-defined]
    if not hasattr(config, "workerinput"):  # xdist workers don't record
        from .recorder import LiveRecorder, live_mode

        mode = live_mode(config)
        if mode:
            config.pluginmanager.register(LiveRecorder(config, mode), "specdeck-live")


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
        sid = _slug(kw["id"]) if kw.get("id") else f"{group}--{fn_slug}"
        base = sid
        n = 2
        while sid in seen_ids:
            sid, n = f"{base}-{n}", n + 1
        if sid != base:
            store.warnings.append(f"duplicate scenario id {base!r} -> {sid!r} ({item.nodeid})")
        seen_ids.add(sid)
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
            hits = _resolve_one(store, by_id, rec, dep_s)
            if hits:
                resolved.extend(h for h in hits if h not in resolved and h != rec.id)
            else:
                store.warnings.append(f"{rec.id}: depends_on {dep_s!r} does not match any scenario — dropped")
        rec.depends_on = resolved
    _warn_cycles(store)


def _resolve_one(store: _Store, by_id: dict, rec: ScenarioRecord, dep_s: str) -> list[str]:
    if dep_s in by_id:
        return [dep_s]
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
    if store is None or item.nodeid not in store.records:
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
    token = _ACTIVE.set(rec.steps if rec is not None else None)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


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
    d["tags"] = list(dict.fromkeys([*rec.tags, rec.group, f"status:{rec.status}"]))
    if rec.depends_on:
        d["depends_on"] = list(rec.depends_on)
    d["steps"] = steps
    d["logic"] = [f"pytest {rec.nodeid}"]
    return d


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
    if store is None or not store.records or config.getoption("--no-specdeck"):
        return
    from .recorder import is_full_run

    if not is_full_run(config) and not config.getoption("--specdeck-out"):
        # A selection (one file, -k, a node id) would shrink the spec to that subset.
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line("specdeck: partial run, spec not rewritten (run the whole suite to regenerate it)")
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
    )
    out.write_text(header + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")

    needs_path = Path(config.getini("specdeck_needs"))
    needs_path = needs_path if needs_path.is_absolute() else Path(str(config.rootpath)) / needs_path
    ordered = [store.records[n] for n in store.order if n in store.records]
    write_needs(needs_path, spec["feature"], ordered, stamp)

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
        for w in store.warnings:
            tr.write_line(f"specdeck: warning: {w}", yellow=True)
