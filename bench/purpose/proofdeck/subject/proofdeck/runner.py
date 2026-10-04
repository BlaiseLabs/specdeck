"""Running the tests, in throwaway copies of the working tree ("sandboxes").

Two ways, same results:

fork mode (default where os.fork exists): every mutant is compiled into the
sandbox behind a switch, ONE pytest process collects the suite, and a forked
child runs each mutant's tests (see _forkrun.py). Per-test coverage can come
from a coverage.py file recorded in CI instead of a traced run.

subprocess mode (fallback, e.g. Windows): one traced pytest run, then one
pytest process per mutant with the mutated file written into a sandbox.

The user's files are never modified. Sandboxes are put first on PYTHONPATH so a
package installed in editable mode is imported from the copy, not the original.
"""
from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import schemata
from .mutate import apply


@dataclass
class Outcome:
    """What running the mutants found, whichever mode ran them."""
    mode: str
    never_ran: list = field(default_factory=list)          # [(path, line)]
    baseline_failing: list = field(default_factory=list)   # lines whose every test already fails
    results: list = field(default_factory=list)            # [(Mutant, outcome, killers, fakes)]
    covering: dict = field(default_factory=dict)            # (path, line) -> [nodeid] that execute it
    baseline_failed: set = field(default_factory=set)       # tests that fail before any mutation
    fakes: dict = field(default_factory=dict)               # nodeid -> [names it fakes], for tests that do
    at_import: set = field(default_factory=set)             # (path, line) executed while importing
    import_time: list = field(default_factory=list)         # lines fork mode could not mutate for that reason
    skipped: int = 0                                        # covered lines over the per-file budget
    notes: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class ForkUnavailable(RuntimeError):
    pass


def fork_supported() -> bool:
    return hasattr(os, "fork") and os.name == "posix"


def pytest_flags(root: Path) -> list:
    """Flags for our pytest runs: quiet, no cache, no plugins that would record or reorder.

    Coverage plugins are switched off; if the project's own addopts pass --cov, pytest-cov
    must stay loaded (or pytest rejects the option), so it is turned off with --no-cov."""
    cov = ["-p", "no:cov"]
    for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        f = root / name
        try:
            if f.is_file() and "--cov" in f.read_text(errors="ignore"):
                cov = ["--no-cov"]
                break
        except OSError:
            pass
    return ["-q", "-p", "no:cacheprovider", "-p", "no:specdeck", *cov, "-p", "no:randomly"]

_IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "env", "node_modules", "__pycache__", "*.pyc",
                                 ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
                                 ".proofdeck", ".specdeck", "build", "dist", "*.egg-info")
_PKG_PARENT = str(Path(__file__).resolve().parent.parent)


class Coverage:
    def __init__(self) -> None:
        self.by_line: dict = {}       # (path, line) -> {nodeid}
        self.passed: set = set()      # tests that pass before any mutation
        self.failed: set = set()
        self.duration = 0.0
        self.shadowed = False         # tests imported the original files, not the sandbox copies
        self.at_import: set = set()   # (path, line) run while importing

    def covering(self, path: str, line: int) -> set:
        return self.by_line.get((path, line), set())


class Workspace:
    def __init__(self, repo, *, workers: int = 4, test_command: str = "", timeout: float = 60) -> None:
        self.repo = Path(repo).resolve()
        self.workers = max(1, int(workers))
        self.cmd = shlex.split(test_command) if test_command else [sys.executable, "-m", "pytest"]
        self.min_timeout = float(timeout)
        self.tmp = Path(tempfile.mkdtemp(prefix="proofdeck-"))
        self.sandboxes: list = []

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sandbox(self, i: int) -> Path:
        while len(self.sandboxes) <= i:
            dest = self.tmp / f"sandbox{len(self.sandboxes)}"
            shutil.copytree(self.repo, dest, ignore=_IGNORE, symlinks=True)
            self.sandboxes.append(dest)
        return self.sandboxes[i]

    def _env(self, box: Path, **extra) -> dict:
        env = dict(os.environ)
        paths = [str(box), str(box / "src"), _PKG_PARENT]
        if env.get("PYTHONPATH"):
            paths.append(env["PYTHONPATH"])
        env.update(PYTHONPATH=os.pathsep.join(paths), PYTHONDONTWRITEBYTECODE="1", **extra)
        env.pop("PYTEST_ADDOPTS", None)
        return env

    def _pytest(self, box: Path, args: list, env_extra: dict, timeout: Optional[float]) -> tuple:
        out = box / ".proofdeck-out.json"
        if out.exists():
            out.unlink()
        cmd = [*self.cmd, *pytest_flags(box), "-p", "proofdeck._pytrace", *args]
        start = time.time()
        try:
            proc = subprocess.run(cmd, cwd=box, env=self._env(box, PROOFDECK_OUT=str(out), **env_extra),
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "timeout", time.time() - start
        data = json.loads(out.read_text()) if out.exists() else None
        return data, proc, time.time() - start

    def trace(self, targets: list) -> Coverage:
        box = self._sandbox(0)
        files = [str(box / t) for t in targets] + [str(self.repo / t) for t in targets]
        data, proc, took = self._pytest(box, [], {"PROOFDECK_MODE": "trace",
                                                  "PROOFDECK_TRACE_FILES": os.pathsep.join(files)}, None)
        if data is None:
            tail = (proc.stdout + proc.stderr)[-1500:] if proc not in (None, "timeout") else proc
            raise RuntimeError(f"the test suite didn't run: {tail}")
        cov = Coverage()
        cov.duration = took
        cov.passed, cov.failed = set(data["passed"]), set(data["failed"])
        root, orig = os.path.realpath(box) + os.sep, os.path.realpath(self.repo) + os.sep

        def rel(f: str) -> Optional[str]:
            if f.startswith(root):
                return f[len(root):].replace(os.sep, "/")
            if f.startswith(orig):
                cov.shadowed = True
            return None

        for nodeid, hits in data["tests"].items():
            for f, lines in hits.items():
                r = rel(f)
                if r:
                    for n in lines:
                        cov.by_line.setdefault((r, n), set()).add(nodeid)
        everyone = set(data["tests"])  # a line run at import time is exercised by every test
        for f, lines in data["import"].items():
            r = rel(f)
            if r:
                for n in lines:
                    cov.by_line.setdefault((r, n), set()).update(everyone)
                    cov.at_import.add((r, n))
        return cov

    def run_mutants(self, jobs: list, per_run_timeout: float) -> dict:
        """jobs: [(mutant, {nodeids})]. Returns {mutant: set of failing node ids, or None if it timed out}."""
        boxes: queue.Queue = queue.Queue()
        for i in range(min(self.workers, max(1, len(jobs)))):
            boxes.put(self._sandbox(i))
        timeout = max(self.min_timeout, per_run_timeout)

        def run(job):
            mutant, nodeids = job
            box = boxes.get()
            target = box / mutant.path
            original = target.read_text(encoding="utf-8")
            keep = box / ".proofdeck-keep.txt"
            try:
                target.write_text(apply(original, mutant), encoding="utf-8")
                keep.write_text("\n".join(sorted(nodeids)) + "\n", encoding="utf-8")
                files = sorted({n.split("::", 1)[0] for n in nodeids})
                data, proc, _ = self._pytest(box, files, {"PROOFDECK_MODE": "results", "PROOFDECK_KEEP": str(keep)},
                                             timeout)
                if proc == "timeout":
                    return mutant, None
                if data is None or data.get("errors"):
                    return mutant, set(nodeids)  # the mutant broke import or collection: tests notice
                return mutant, set(data["failed"])
            finally:
                target.write_text(original, encoding="utf-8")
                boxes.put(box)

        with ThreadPoolExecutor(max_workers=boxes.qsize()) as pool:
            return dict(pool.map(run, jobs))


def run_fork(repo, texts: dict, candidates: dict, cfg: dict, coverage: Optional[str]) -> Outcome:
    """One pytest process for everything; see _forkrun.py."""
    if not fork_supported():
        raise ForkUnavailable("this system has no fork()")
    workers = int(cfg["workers"]) or min(8, os.cpu_count() or 2)
    with Workspace(repo, workers=workers, test_command=cfg["test_command"], timeout=cfg["timeout"]) as ws:
        box = ws._sandbox(0)
        key, by_key, spec_mutants = 1, {}, []
        for path in sorted(candidates):
            source, chosen = schemata.build(texts[path], candidates[path], first_key=key)
            (box / path).write_text(source, encoding="utf-8")
            for line, (k, m) in chosen.items():
                by_key[k] = m
                spec_mutants.append({"key": k, "path": path, "line": line})
                key = max(key, k + 1)
        spec = {
            "targets": {p: str(box / p) for p in candidates},
            "mutants": spec_mutants,
            "cap": int(cfg["max_mutants_per_file"]),
            "workers": workers,
            "timeout": float(cfg["timeout"]),
            "coverage": coverage,
            "out": str(ws.tmp / "fork-out.json"),
            "tmpdir": str(ws.tmp),
        }
        spec_path = ws.tmp / "fork-spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        env = ws._env(box, PROOFDECK_SPEC=str(spec_path))
        if sys.platform == "darwin":
            env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
        cmd = [*ws.cmd, *pytest_flags(box), "-p", "proofdeck._forkrun"]
        proc = subprocess.run(cmd, cwd=box, env=env, capture_output=True, text=True)
        out = Path(spec["out"])
        data = json.loads(out.read_text()) if out.exists() else None
        if data is None:
            raise ForkUnavailable(f"the test session ended without results: {(proc.stdout + proc.stderr)[-1200:]}")
        if "error" in data:
            raise RuntimeError(f"{data['error']}\n{(proc.stdout + proc.stderr)[-1500:]}")
        o = Outcome(mode="fork", notes=data.get("notes", []), stats=data.get("stats", {}), skipped=data["skipped"])
        o.never_ran = [tuple(x) for x in data["never_ran"]]
        o.baseline_failing = [tuple(x) for x in data["baseline_failing"]]
        for r in data["results"]:
            killers = [r["killer"]] if r["killer"] else []
            o.results.append((by_key[r["key"]], r["outcome"], killers, [r["faked"]] if killers else []))
        for key, tests in data.get("covering", {}).items():
            m = by_key[int(key)]
            o.covering[(m.path, m.line)] = list(tests)
        o.baseline_failed = set(data.get("baseline_failed", []))
        o.fakes = dict(data.get("fakes", {}))
        o.at_import = {tuple(x) for x in data.get("at_import", [])}
        o.import_time = [tuple(x) for x in data.get("import_time", [])]
        missing = sum(len(v) for v in candidates.values()) - len(by_key)
        if missing:
            o.notes.append(f"{missing} lines had no mutant that could be switched and were not checked")
        return o


def run_subprocess(repo, texts: dict, candidates: dict, cfg: dict, say) -> Outcome:
    """One traced run, then a pytest process per mutant."""
    from .mocks import faked
    from .mutate import select

    workers = int(cfg["workers"]) or min(8, os.cpu_count() or 2)
    o = Outcome(mode="subprocess")
    started = time.time()
    with Workspace(repo, workers=workers, test_command=cfg["test_command"], timeout=cfg["timeout"]) as ws:
        cov = ws.trace(sorted(candidates))
        if cov.shadowed:
            o.notes.append("tests imported the original files instead of the sandbox copies")
        ran = {}
        for path, by_line in candidates.items():
            for n in by_line:
                if cov.covering(path, n):
                    ran.setdefault(path, {})[n] = by_line[n]
                else:
                    o.never_ran.append((path, n))
        chosen = select(ran, cap_per_file=int(cfg["max_mutants_per_file"]))
        o.skipped = sum(len(v) for v in ran.values()) - len(chosen)
        jobs = []
        for m in chosen:
            o.covering[(m.path, m.line)] = sorted(cov.covering(m.path, m.line))
            tests = cov.covering(m.path, m.line) & cov.passed
            if tests:
                jobs.append((m, tests))
            else:
                o.baseline_failing.append((m.path, m.line))
        say(f"running {len(jobs)} mutants")
        results = ws.run_mutants(jobs, per_run_timeout=cov.duration * 3 + 5) if jobs else {}
        for m, killers in results.items():
            if killers is None:
                o.results.append((m, "timeout", [], []))
            elif not killers:
                o.results.append((m, "survived", [], []))
            else:
                o.results.append((m, "killed", sorted(killers), [faked(repo, k) for k in sorted(killers)]))
        o.baseline_failed = set(cov.failed)
        o.at_import = set(cov.at_import)
        for nodeid in {n for tests in o.covering.values() for n in tests}:
            names = faked(repo, nodeid)
            if names:
                o.fakes[nodeid] = names
        o.stats = {"mode": "subprocess", "pytest_starts": 1 + len(jobs), "mutants": len(jobs),
                   "total_s": round(time.time() - started, 3), "traced": True}
    return o
