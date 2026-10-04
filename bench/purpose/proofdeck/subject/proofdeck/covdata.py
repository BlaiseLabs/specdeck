"""Reuse per-test coverage from CI instead of tracing the suite again.

Reads a coverage.py data file (SQLite) directly, so coverage doesn't need to be
installed where proofdeck runs. The data must record which test ran each line,
which coverage.py calls "contexts". Either of these produces them:

    pytest --cov=src --cov-context=test            # pytest-cov; contexts are "<node id>|run"
    coverage run -m pytest   with  [tool.coverage.run] dynamic_context = "test_function"
                                                    # contexts are "package.module.Class.test_name"

The file must come from the same code that is being checked: line numbers are
matched as recorded.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional


class CoverageFileError(Exception):
    pass


def numbits_to_lines(numbits: bytes) -> list:
    return [i * 8 + j for i, byte in enumerate(numbits) for j in range(8) if byte & (1 << j)]


def _match(stored: str, targets: dict) -> Optional[str]:
    norm = stored.replace("\\", "/")
    best = None
    for rel in targets:
        if norm == rel or norm.endswith("/" + rel):
            if best is None or len(rel) > len(best):
                best = rel
    return best


def read(path, targets) -> dict:
    """{(rel_path, line): {context, ...}} for the target files; '' is the no-test context.

    `targets`: iterable of repo-relative paths. Raises CoverageFileError if the file can't be
    used (missing, not coverage data, or no per-test contexts).
    """
    if not os.path.isfile(path):
        raise CoverageFileError(f"no coverage file at {path}")
    targets = {t.replace("\\", "/"): t for t in targets}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as exc:
        raise CoverageFileError(f"{path} is not a coverage.py data file: {exc}") from exc
    if not {"file", "context"} <= tables:
        raise CoverageFileError(f"{path} is not a coverage.py data file")
    try:
        files = {fid: _match(p, targets) for fid, p in con.execute("SELECT id, path FROM file")}
        contexts = dict(con.execute("SELECT id, context FROM context"))
        if not any(c for c in contexts.values()):
            raise CoverageFileError(
                f"{path} has no per-test contexts; record them with `pytest --cov-context=test` "
                f"or `dynamic_context = \"test_function\"`")
        out: dict = {}

        def add(fid, cid, lines):
            rel = files.get(fid)
            if rel is None:
                return
            ctx = contexts.get(cid, "")
            for n in lines:
                if n > 0:
                    out.setdefault((rel, n), set()).add(ctx)

        if "line_bits" in tables:
            for fid, cid, bits in con.execute("SELECT file_id, context_id, numbits FROM line_bits"):
                add(fid, cid, numbits_to_lines(bits))
        if "arc" in tables:
            for fid, cid, a, b in con.execute("SELECT file_id, context_id, fromno, tono FROM arc"):
                add(fid, cid, (a, b))
    finally:
        con.close()
    if not any(files.values()):
        raise CoverageFileError(f"{path} doesn't cover any of the changed files")
    return out


def context_to_nodeids(context: str, by_nodeid: set, by_function: dict) -> set:
    """Map a coverage context to collected test node ids.

    pytest-cov: "tests/test_x.py::test_y[1]|run" -> that node id.
    coverage test_function: "tests.test_x.test_y" or "tests.test_x.TestC.test_y" -> every
    collected item of that function (all its parametrized cases).
    """
    if not context:
        return set()
    if "|" in context:
        nodeid = context.rsplit("|", 1)[0]
        return {nodeid} if nodeid in by_nodeid else set()
    return set(by_function.get(context, ()))
