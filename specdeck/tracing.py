"""Which source functions each scenario actually executes.

Measured, not declared: the suite runs under coverage.py and each test gets its own
context. Lines executed while the modules were imported are recorded separately and
subtracted, because every test "runs" them and they discriminate nothing — an
unfiltered map says all 38 tapedeck tests touch all 16 source files, which is true
and useless.

This exists because an A/B against the raw test files (bench/agent_ab) found the
generated spec's one disabling gap: it names no source code, so an agent holding a
diff has no bridge from `path/to/file.py:31` to any scenario.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Optional

IMPORT_CONTEXT = "::specdeck-import::"


class Unavailable(RuntimeError):
    """coverage.py is missing, or something else is already measuring."""


class Recorder:
    """Per-test line coverage, with import-time lines kept apart."""

    def __init__(self, sources: list) -> None:
        self.sources = [str(s) for s in sources]
        self._cov = None

    def start(self) -> None:
        try:
            import coverage
        except ImportError as exc:  # pragma: no cover - exercised by the no-coverage path
            raise Unavailable("coverage.py is not installed") from exc
        if getattr(coverage.Coverage, "current", None) and coverage.Coverage.current() is not None:
            raise Unavailable("something else is already measuring coverage (pytest-cov?)")
        try:
            # cover_pylib is moot while `source` is set, but says what is meant
            self._cov = coverage.Coverage(data_file=None, cover_pylib=False, source=self.sources)
            self._cov.start()
            self._cov.switch_context(IMPORT_CONTEXT)
        except Exception as exc:  # coverage raises its own family of errors
            self._cov = None
            raise Unavailable(str(exc)) from exc

    def switch(self, nodeid: str) -> None:
        if self._cov is not None:
            self._cov.switch_context(nodeid)

    def stop(self) -> dict:
        """``{nodeid: {(abs path, line)}}``, import-time lines removed."""
        if self._cov is None:
            return {}
        self._cov.stop()
        data = self._cov.get_data()
        per_test: dict = {}
        at_import: set = set()
        for path in data.measured_files():
            for line, contexts in (data.contexts_by_lineno(path) or {}).items():
                for ctx in contexts:
                    if ctx == IMPORT_CONTEXT:
                        at_import.add((path, line))
                    elif ctx:
                        per_test.setdefault(ctx, set()).add((path, line))
        self._cov = None
        return {nodeid: lines - at_import for nodeid, lines in per_test.items()}


def _owners(path: str) -> list:
    """``[(first line, last line, qualified name)]`` for every function in the file."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    out: list = []

    def walk(node, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                out.append((child.lineno, child.end_lineno, name))
                walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")

    walk(tree)
    return out


@lru_cache(maxsize=256)
def _owners_cached(path: str, mtime: float) -> tuple:
    return tuple(_owners(path))


def to_functions(per_test: dict, root, skip: tuple = ()) -> dict:
    """``{nodeid: ["rel/path.py:Class.method", ...]}``, most-executed first.

    ``skip``: path fragments to leave out, so a suite does not map its own test files.
    """
    root = Path(root).resolve()
    out: dict = {}
    for nodeid, lines in per_test.items():
        counts: dict = {}
        for path, line in lines:
            try:
                rel = str(Path(path).resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if any(frag in rel for frag in skip):
                continue
            try:
                mtime = Path(path).stat().st_mtime
            except OSError:
                continue
            hits = [name for start, end, name in _owners_cached(path, mtime) if start <= line <= end]
            where = f"{rel}:{hits[-1]}" if hits else f"{rel}:(module level)"
            counts[where] = counts.get(where, 0) + 1
        out[nodeid] = [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out


def index(per_scenario: dict, numbers: dict, *, hub_share: float = 0.5, min_list: int = 8,
          total: Optional[int] = None) -> dict:
    """Invert to ``{file: {function: [scenario numbers] or ("most", count)}}``.

    One number per reference instead of a scenario id keeps the map affordable: on tapedeck
    it is ~3.1k tokens against ~8.5k for the same map written with ids.

    A function that most of the suite runs — a plugin hook, a constructor, a config reader —
    gets a count instead of a list. Measured reason: given the list for one such function
    (run by 47 of 88 scenarios) an agent answered "which tests break" by naming 87 tests,
    against 9 for the same agent without the map, when the true answer was none. The list
    reads as evidence and is none: it is the same long list for every change in that
    function. Collapsing it removes the temptation and shortens the map.
    """
    out: dict = {}
    for sid, places in per_scenario.items():
        n = numbers.get(sid)
        if n is None:
            continue
        for place in places:
            file, _, fn = place.partition(":")
            out.setdefault(file, {}).setdefault(fn or "(module level)", []).append(n)
    n_total = total if total is not None else len(per_scenario)
    # Both conditions: a large share of the suite AND a list long enough to be noise. On a
    # small suite "3 of 4 scenarios" is still the answer, and worth reading.
    cutoff = max(min_list, int(hub_share * n_total))
    cooked: dict = {}
    for f, fns in sorted(out.items()):
        cooked[f] = {}
        for fn, ns in sorted(fns.items()):
            uniq = sorted(set(ns))
            cooked[f][fn] = ("most", len(uniq), n_total) if len(uniq) >= cutoff else uniq
    return cooked


def summarize(per_scenario: dict) -> Optional[str]:
    if not per_scenario:
        return None
    total = len({p for places in per_scenario.values() for p in places})
    return f"coverage: {len(per_scenario)} scenarios mapped to {total} source functions"
