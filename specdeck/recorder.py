"""Record a pytest run into the viewer's history, live, and make sure a viewer is up.

Enabled with ``--specdeck-live`` (record + start the viewer if none is running),
``--specdeck-record`` (record only), or ``specdeck_live = true | record`` in the
pytest ini. Recording never fails the test run: any error is reported once and
recording stops.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

from .history import History, db_path, home

DEFAULT_PORT = 7357
BLOCKED_PREFIX = "specdeck: prerequisite scenario"


def viewer_port(config: pytest.Config) -> int:
    opt = config.getoption("--specdeck-port", None)
    if opt:
        return int(opt)
    env = os.environ.get("SPECDECK_PORT")
    if env:
        return int(env)
    return int(config.getini("specdeck_port") or DEFAULT_PORT)


def live_mode(config: pytest.Config) -> Optional[str]:
    """None, "record" or "live"."""
    if config.getoption("--no-specdeck-live", False):
        return None
    if config.getoption("--specdeck-live", False):
        return "live"
    if config.getoption("--specdeck-record", False):
        return "record"
    ini = str(config.getini("specdeck_live") or "").strip().lower()
    if ini in ("1", "true", "yes", "on", "live"):
        return "live"
    if ini == "record":
        return "record"
    return None


def is_full_run(config: pytest.Config) -> bool:
    """True when this run selects the whole suite (so it can define the board and the spec)."""
    opt = config.option
    if (getattr(opt, "keyword", "") or getattr(opt, "markexpr", "") or getattr(opt, "lf", False)
            or getattr(opt, "deselect", None) or getattr(opt, "stepwise", False)):
        return False
    src = getattr(config, "args_source", None)
    if src is not None and src != pytest.Config.ArgsSource.ARGS:
        return True
    root = Path(str(config.rootpath)).resolve()
    inv = Path(str(config.invocation_params.dir))
    testpaths = {(root / p).resolve() for p in (config.getini("testpaths") or [])}
    targets = set()
    for a in config.args:
        if "::" in a:
            return False
        p = Path(a)
        p = (p if p.is_absolute() else inv / p).resolve()
        if not p.is_dir():
            return False
        targets.add(p)
    return targets == {root} or (bool(testpaths) and targets == testpaths)


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=2)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def child_env(**extra) -> dict:
    """Environment for processes specdeck starts (the viewer, runs from the viewer).

    Keeps this interpreter's user site-packages visible even if HOME was changed
    (pytester does that), so the child finds the same pytest and specdeck.
    """
    import site

    env = {**os.environ, "SPECDECK_HOME": str(home()), **extra}
    if site.ENABLE_USER_SITE and "PYTHONUSERBASE" not in os.environ:
        env["PYTHONUSERBASE"] = site.getuserbase()
    return env


def health(port: int, timeout: float = 0.4) -> Optional[dict]:
    try:
        with _OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if data.get("app") == "specdeck" else None
    except Exception:
        return None


def ensure_viewer(port: int) -> tuple:
    """(ok, note). Starts ``python -m specdeck serve`` detached if nothing answers on the port."""
    info = health(port)
    if info:
        if info.get("db") != str(db_path()):
            return True, f"the viewer on port {port} uses a different history ({info.get('db')})"
        return True, ""
    log_dir = home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "server.log", "ab")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "specdeck", "serve", "--port", str(port)],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env=child_env(),
        )
    except OSError as exc:
        return False, f"could not start the viewer: {exc}"
    finally:
        log.close()
    for _ in range(60):
        time.sleep(0.05)
        if health(port, timeout=0.2):
            return True, ""
    return False, f"the viewer did not come up on port {port}; see {log_dir / 'server.log'}"


class LiveRecorder:
    def __init__(self, config: pytest.Config, mode: str) -> None:
        self.config = config
        self.mode = mode
        self.hist: Optional[History] = None
        self.project: Optional[dict] = None
        self.run_id: Optional[int] = None
        self.url: Optional[str] = None
        self.note = ""
        self.ids: dict = {}  # nodeid -> scenario id (only for recorded items)

    # -- helpers ----------------------------------------------------------- #
    def _store(self):
        from .plugin import _store
        return _store(self.config)

    def _fail(self, exc: Exception) -> None:
        self.note = f"recording stopped: {type(exc).__name__}: {exc}"
        self.run_id = None

    def _tw(self, text: str, **markup) -> None:
        tr = self.config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(text, **markup)

    # -- hooks ------------------------------------------------------------- #
    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        from .plugin import _feature_name

        store = self._store()
        if store is None:
            return
        config = self.config
        root = Path(str(config.rootpath))
        scenarios = []
        for item in session.items:
            rec = store.records.get(item.nodeid)
            if rec is None:
                continue
            self.ids[item.nodeid] = rec.id
            scenarios.append({"id": rec.id, "nodeid": item.nodeid, "meta": _meta(item, rec, root)})
        try:
            self.hist = History()
            self.project = self.hist.register_project(
                root, _feature_name(config), config.getini("specdeck_description") or "",
                python=sys.executable, invocation_dir=str(config.invocation_params.dir),
            )
            self.run_id = self.hist.start_run(
                self.project["id"], scenarios,
                args=list(config.invocation_params.args), full=is_full_run(config),
                trigger=os.environ.get("SPECDECK_TRIGGER", "terminal"), pid=os.getpid(),
                invocation_dir=str(config.invocation_params.dir),
                git_branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD"),
                git_sha=_git(root, "rev-parse", "--short", "HEAD"),
            )
        except Exception as exc:  # never break the test run
            self._fail(exc)
            return
        if self.mode == "live":
            port = viewer_port(config)
            ok, note = ensure_viewer(port)
            self.note = note
            if ok:
                self.url = f"http://127.0.0.1:{port}/p/{self.project['slug']}"
                self._tw(f"specdeck live: {self.url}", cyan=True)

    def pytest_runtest_logstart(self, nodeid: str, location) -> None:
        sid = self.ids.get(nodeid)
        if self.run_id is None or sid is None:
            return
        try:
            self.hist.set_running(self.project["id"], self.run_id, sid)
        except Exception as exc:
            self._fail(exc)

    def pytest_runtest_logfinish(self, nodeid: str, location) -> None:
        sid = self.ids.get(nodeid)
        store = self._store()
        if self.run_id is None or sid is None or store is None:
            return
        rec = store.records.get(nodeid)
        if rec is None:
            return
        status = rec.status
        if status == "skipped" and (rec.message or "").startswith(BLOCKED_PREFIX):
            status = "blocked"
        elif status not in ("passed", "failed", "skipped", "error"):
            status = "error"
        try:
            self.hist.set_result(self.project["id"], self.run_id, sid, status,
                                 duration=rec.duration, message=rec.message, steps=_steps(rec))
        except Exception as exc:
            self._fail(exc)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus) -> None:
        if self.run_id is None:
            return
        status = "interrupted" if int(exitstatus) == int(pytest.ExitCode.INTERRUPTED) else None
        try:
            self.hist.finish_run(self.project["id"], self.run_id, status=status)
        except Exception as exc:
            self._fail(exc)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.url:
            terminalreporter.write_line(f"specdeck live: {self.url}  (run #{self.run_id or '?'})", cyan=True)
        elif self.run_id and self.project:
            terminalreporter.write_line(
                f"specdeck: recorded run #{self.run_id} for {self.project['slug']} "
                f"(view with: python -m specdeck serve)")
        if self.note:
            terminalreporter.write_line(f"specdeck: {self.note}", yellow=True)

    def pytest_unconfigure(self, config) -> None:
        if self.hist is not None:
            self.hist.close()
            self.hist = None


def _meta(item: pytest.Item, rec, root: Path) -> dict:
    import inspect

    fn = getattr(item, "function", None)
    line = None
    if fn is not None:
        try:
            line = inspect.unwrap(fn).__code__.co_firstlineno
        except AttributeError:
            line = None
    path = Path(str(item.path))
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = str(path)
    original = getattr(item, "originalname", None) or item.name
    return {
        "name": rec.name, "group": rec.group, "group_name": rec.group_name,
        "description": rec.description, "tags": list(rec.tags), "depends_on": list(rec.depends_on),
        "planned": [list(p) for p in rec.planned], "file": rel, "line": line,
        "function": original, "parametrized": original != item.name,
    }


def _steps(rec) -> list:
    """The spec's merged steps, each tagged ok / failed / pending for the viewer."""
    from .plugin import _scenario_dict

    steps = _scenario_dict(rec)["steps"]
    ran = len(rec.steps)
    out = []
    for i, s in enumerate(steps):
        s = dict(s)
        observed = (s.get("assertion") or {}).get("observed_label")
        if i < ran:
            s["state"] = "failed" if rec.steps[i].failed else "ok"
        elif observed in ("not run", "not reached"):
            s["state"] = "pending"
        elif rec.status in ("failed", "error"):
            s["state"] = "failed"
        elif rec.status == "passed":
            s["state"] = "ok"
        else:
            s["state"] = "pending"
        out.append(s)
    return out
