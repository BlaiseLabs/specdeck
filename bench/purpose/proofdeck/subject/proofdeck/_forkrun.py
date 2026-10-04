"""pytest plugin for fork mode: collect once, fork a child per mutant.

proofdeck starts ONE pytest process with `-p proofdeck._forkrun` and a spec
file (PROOFDECK_SPEC). The source files in that process's sandbox already
contain every mutant behind a switch (see schemata.py). After collection this
plugin takes over the run loop:

1. coverage: fork a child that runs the whole suite under a line tracer, or
   read per-test coverage from a coverage.py file recorded in CI;
2. baseline: which of those tests pass with no mutant switched on, and how long
   each takes;
3. mutants: fork one child per mutant (several at a time), switch its mutant on,
   run the tests that execute its line (tests that don't fake anything first,
   fastest first), and stop at the first failure.

Forking shares everything the parent already imported, so each mutant costs
its tests' run time plus a few milliseconds, not a pytest start-up.
"""
from __future__ import annotations

import builtins
import json
import os
import signal
import sys
import threading
import time

import pytest

_T_IMPORT = time.time()
ACTIVE = 0


def _switch(key: int) -> bool:
    return key == ACTIVE


builtins._proofdeck_m = _switch  # the schemata call this; no import line needed in user code

_SPEC_PATH = os.environ.get("PROOFDECK_SPEC")
_SPEC = None
if _SPEC_PATH:
    with open(_SPEC_PATH, encoding="utf-8") as _fh:
        _SPEC = json.load(_fh)


class _Tracer:
    def __init__(self, targets: set) -> None:
        self.targets = targets
        self.current: set = set()
        self._is_target: dict = {}

    def local(self, frame, event, arg):
        if event == "line":
            self.current.add((frame.f_code.co_filename, frame.f_lineno))
        return self.local

    def global_(self, frame, event, arg):
        code = frame.f_code
        hit = self._is_target.get(code)
        if hit is None:
            hit = self._is_target[code] = os.path.realpath(code.co_filename) in self.targets
        return self.local if hit else None

    def start(self) -> None:
        sys.settrace(self.global_)
        threading.settrace(self.global_)

    def stop(self) -> None:
        sys.settrace(None)
        threading.settrace(None)


_TRACER = None
if _SPEC is not None and not _SPEC.get("coverage"):
    _TRACER = _Tracer({os.path.realpath(p) for p in _SPEC["targets"].values()})
    _TRACER.start()  # lines run while test modules are imported count for every test


def _write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _run(items: list, *, stop_on_failure: bool, tracer=None) -> dict:
    """Run `items` in order in this process (always a forked child)."""
    from _pytest.runner import runtestprotocol

    failed, durations, hits = [], {}, {}
    for i, item in enumerate(items):
        nxt = items[i + 1] if i + 1 < len(items) else None
        if tracer is not None:
            tracer.current = set()
            tracer.start()
        t = time.time()
        reports = runtestprotocol(item, log=False, nextitem=nxt)
        durations[item.nodeid] = time.time() - t
        if tracer is not None:
            tracer.stop()
            hits[item.nodeid] = sorted({(os.path.realpath(f), n) for f, n in tracer.current})
        if any(r.failed for r in reports):
            failed.append(item.nodeid)
            if stop_on_failure:
                break
    return {"failed": failed, "durations": durations, "hits": hits}


class _Pool:
    """Forked children, at most `workers` at a time, each killed at its deadline."""

    def __init__(self, workers: int, tmpdir: str) -> None:
        self.workers = max(1, workers)
        self.tmpdir = tmpdir

    def run(self, jobs: list) -> dict:
        """jobs: [(name, timeout, fn)] -> {name: fn's result dict, or "timeout" / "crash"}."""
        results, running, queue = {}, {}, list(jobs)
        try:
            while queue or running:
                while queue and len(running) < self.workers:
                    name, timeout, fn = queue.pop(0)
                    out = os.path.join(self.tmpdir, f"job-{name}.json")
                    pid = os.fork()
                    if pid == 0:  # child
                        code = 0
                        try:
                            os.setpgid(0, 0)
                            _write(out, fn())
                        except BaseException as exc:  # noqa: BLE001 - report anything, then leave
                            try:
                                _write(out, {"error": f"{type(exc).__name__}: {exc}"})
                            except BaseException:
                                pass
                            code = 1
                        finally:
                            os._exit(code)
                    running[pid] = (name, time.time() + timeout if timeout else None, out)
                pid, _status = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    now = time.time()
                    for p, (name, deadline, _out) in list(running.items()):
                        if deadline and now > deadline:
                            self._kill(p)
                            results[name] = "timeout"
                            del running[p]
                    time.sleep(0.002)
                    continue
                if pid in running:
                    name, _deadline, out = running.pop(pid)
                    data = _read(out)
                    results[name] = data if data is not None and "error" not in data else "crash"
        finally:
            for p in list(running):
                self._kill(p)
        return results

    @staticmethod
    def _kill(pid: int) -> None:
        for kill in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
            try:
                kill()
                break
            except OSError:
                continue
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def _order(nodeids, durations: dict, faked: dict) -> list:
    """Tests that don't fake anything first, then the rest; fastest first within each."""
    return sorted(nodeids, key=lambda n: (bool(faked.get(n)), durations.get(n, 0.0), n))


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    global ACTIVE
    if _SPEC is None:
        return None
    from . import covdata, mocks

    out_path = _SPEC["out"]
    t_loop = time.time()
    stats = {"mode": "fork", "pytest_starts": 1, "collect_s": round(t_loop - _T_IMPORT, 3),
             "items": len(session.items)}
    if _TRACER is not None:
        _TRACER.stop()
    if session.testsfailed:  # collection errors
        _write(out_path, {"error": f"{session.testsfailed} error(s) while collecting the tests", "stats": stats})
        return True

    items = list(session.items)
    by_id = {i.nodeid: i for i in items}
    targets = _SPEC["targets"]                      # rel -> sandbox path
    abs2rel = {os.path.realpath(p): rel for rel, p in targets.items()}
    workers = int(_SPEC["workers"])
    pool = _Pool(workers, _SPEC["tmpdir"])
    notes = []

    # 1. which tests execute which changed line -------------------------------------------
    cov: dict = {}
    baseline_failed, durations = set(), {}
    traced = _SPEC.get("coverage") is None
    if not traced:
        try:
            by_function: dict = {}
            for it in items:
                mod = getattr(getattr(it, "module", None), "__name__", None)
                if mod:
                    cls = getattr(it, "cls", None)
                    qual = f"{cls.__name__}.{it.originalname}" if cls else getattr(it, "originalname", it.name)
                    by_function.setdefault(f"{mod}.{qual}", []).append(it.nodeid)
            raw = covdata.read(_SPEC["coverage"], list(targets))
            ids = set(by_id)
            for key, contexts in raw.items():
                nodes = set()
                for ctx in contexts:
                    nodes |= set(by_id) if ctx == "" else covdata.context_to_nodeids(ctx, ids, by_function)
                if nodes:
                    cov[key] = nodes
            if not cov:
                raise covdata.CoverageFileError("no recorded test context matches a collected test")
            notes.append(f"used per-test coverage from {_SPEC['coverage']}")
        except covdata.CoverageFileError as exc:
            notes.append(f"couldn't use the coverage file ({exc}); traced the tests instead")
            traced = True
    t_cov = time.time()
    at_import: set = set()
    import_time: list = []  # mutants on import-time lines: their switch is never reached after the fork
    if traced:
        tracer = _TRACER or _Tracer(set(abs2rel))
        imported = {(os.path.realpath(f), n) for f, n in tracer.current}
        res = pool.run([("trace", None, lambda: _run(items, stop_on_failure=False, tracer=tracer))])["trace"]
        if not isinstance(res, dict):
            _write(out_path, {"error": f"the traced test run ended abnormally ({res})", "stats": stats})
            return True
        baseline_failed, durations = set(res["failed"]), res["durations"]
        everyone = set(by_id)
        for f, n in imported:
            if f in abs2rel:
                cov.setdefault((abs2rel[f], n), set()).update(everyone)
                at_import.add((abs2rel[f], n))
        for nodeid, hits in res["hits"].items():
            for f, n in hits:
                if f in abs2rel:
                    cov.setdefault((abs2rel[f], n), set()).add(nodeid)
    stats["coverage_s"] = round(time.time() - t_cov, 3)
    stats["traced"] = traced

    # 2. choose the mutants to run --------------------------------------------------------
    never_ran, baseline_failing, jobs_spec = [], [], []
    per_file: dict = {}
    skipped = 0
    for m in sorted(_SPEC["mutants"], key=lambda m: (m["path"], m["line"])):
        tests = cov.get((m["path"], m["line"]), set()) & set(by_id)
        if not tests:
            never_ran.append([m["path"], m["line"]])
            continue
        if (m["path"], m["line"]) in at_import:
            import_time.append([m["path"], m["line"]])
            continue
        if per_file.get(m["path"], 0) >= int(_SPEC["cap"]):
            skipped += 1
            continue
        per_file[m["path"]] = per_file.get(m["path"], 0) + 1
        jobs_spec.append((m, tests))

    if not traced and jobs_spec:  # baseline for the tests we'll use, and their durations
        union = [i for i in items if any(i.nodeid in t for _, t in jobs_spec)]
        t_base = time.time()
        res = pool.run([("baseline", None, lambda: _run(union, stop_on_failure=False))])["baseline"]
        if not isinstance(res, dict):
            _write(out_path, {"error": f"the baseline test run ended abnormally ({res})", "stats": stats})
            return True
        baseline_failed, durations = set(res["failed"]), res["durations"]
        stats["baseline_s"] = round(time.time() - t_base, 3)

    root = str(session.config.rootpath)
    fakes_cache: dict = {}

    def faked(nodeid):
        if nodeid not in fakes_cache:
            fakes_cache[nodeid] = mocks.faked(root, nodeid)
        return fakes_cache[nodeid]

    jobs, meta = [], {}
    max_timeout = float(_SPEC["timeout"])
    for m, tests in jobs_spec:
        usable = tests - baseline_failed
        if not usable:
            baseline_failing.append([m["path"], m["line"]])
            continue
        ordered = _order(usable, durations, {n: faked(n) for n in usable})
        expected = sum(durations.get(n, 1.0) for n in ordered)
        timeout = min(max_timeout, max(5.0, 10 * expected + 2))
        chain = [by_id[n] for n in ordered]

        def child(key=m["key"], chain=chain):
            global ACTIVE
            ACTIVE = key
            return _run(chain, stop_on_failure=True)

        jobs.append((str(m["key"]), timeout, child))
        meta[str(m["key"])] = ordered

    # 3. run them ---------------------------------------------------------------------------
    t_run = time.time()
    raw_results = pool.run(jobs) if jobs else {}
    stats["mutants"] = len(jobs)
    stats["run_s"] = round(time.time() - t_run, 3)
    results = []
    covering = {str(m["key"]): sorted(tests) for m, tests in jobs_spec}  # who ran each line
    if import_time:
        notes.append(f"{len(import_time)} changed lines run when their module is imported, which fork mode "
                     f"cannot check; run with --mode subprocess to check them")
    for key, res in raw_results.items():
        if res in ("timeout", "crash"):
            results.append({"key": int(key), "outcome": res, "killer": None, "faked": []})
            continue
        killer = res["failed"][0] if res["failed"] else None
        results.append({"key": int(key), "outcome": "killed" if killer else "survived", "killer": killer,
                        "faked": faked(killer) if killer else []})
    _write(out_path, {"never_ran": never_ran, "baseline_failing": baseline_failing, "skipped": skipped,
                      "results": results, "covering": covering, "baseline_failed": sorted(baseline_failed),
                      "import_time": import_time, "at_import": sorted(at_import),
                      "fakes": {n: f for n, f in fakes_cache.items() if f}, "notes": notes, "stats": stats})
    return True
