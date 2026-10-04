"""pytest plugin that proofdeck loads into its own test runs (`-p proofdeck._pytrace`).

PROOFDECK_MODE=trace   record which target lines each test executes
PROOFDECK_MODE=results record which tests failed (mutant runs)
PROOFDECK_KEEP         optional file of node ids; every other collected test is deselected
Results are written as JSON to PROOFDECK_OUT.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest

_MODE = os.environ.get("PROOFDECK_MODE", "results")
_OUT = os.environ.get("PROOFDECK_OUT")
_TARGETS = {os.path.realpath(p) for p in os.environ.get("PROOFDECK_TRACE_FILES", "").split(os.pathsep) if p}


class _Tracer:
    def __init__(self) -> None:
        self.current: set = set()
        self.import_hits: set = set()
        self.per_test: dict = {}
        self._is_target: dict = {}

    def local(self, frame, event, arg):
        if event == "line":
            self.current.add((frame.f_code.co_filename, frame.f_lineno))
        return self.local

    def global_(self, frame, event, arg):
        code = frame.f_code
        hit = self._is_target.get(code)
        if hit is None:
            hit = self._is_target[code] = os.path.realpath(code.co_filename) in _TARGETS
        return self.local if hit else None

    def start(self) -> None:
        sys.settrace(self.global_)
        threading.settrace(self.global_)

    def stop(self) -> None:
        sys.settrace(None)
        threading.settrace(None)


_tracer = _Tracer() if _MODE == "trace" else None
if _tracer is not None:
    _tracer.start()  # from here on: lines run while importing test modules count as "import" hits
_failed: set = set()
_passed: set = set()
_errors: list = []


def _as_json(hits: set) -> dict:
    out: dict = {}
    for f, n in hits:
        out.setdefault(os.path.realpath(f), set()).add(n)
    return {f: sorted(ns) for f, ns in out.items()}


def pytest_collection_modifyitems(config, items):
    keep_file = os.environ.get("PROOFDECK_KEEP")
    if not keep_file:
        return
    with open(keep_file, encoding="utf-8") as fh:
        keep = {line.rstrip("\n") for line in fh if line.strip()}
    selected = [i for i in items if i.nodeid in keep]
    deselected = [i for i in items if i.nodeid not in keep]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _tracer is None:
        yield
        return
    _tracer.import_hits |= _tracer.current
    _tracer.current = set()
    _tracer.start()  # tests or plugins may have replaced the tracer
    yield
    _tracer.per_test[item.nodeid] = _tracer.current
    _tracer.current = set()


def pytest_runtest_logreport(report):
    if report.failed:
        _failed.add(report.nodeid)
    elif report.when == "call" and report.passed:
        _passed.add(report.nodeid)


def pytest_collectreport(report):
    if report.failed:
        _errors.append(report.nodeid or "<collection>")


def pytest_sessionfinish(session, exitstatus):
    if _tracer is not None:
        _tracer.stop()
    if not _OUT:
        return
    data = {"failed": sorted(_failed), "passed": sorted(_passed - _failed), "errors": _errors,
            "exitstatus": int(exitstatus)}
    if _tracer is not None:
        data["import"] = _as_json(_tracer.import_hits)
        data["tests"] = {nodeid: _as_json(hits) for nodeid, hits in _tracer.per_test.items()}
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
