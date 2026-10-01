"""`specdeck watch` — re-run the suite when a file changes.

Deliberately dependency-free: a polling stat() walk, not an OS watcher. A suite
small enough to want watch mode is small enough to stat in a few milliseconds,
and this keeps specdeck installable with nothing but pytest and PyYAML.

Edits made from the viewer land on disk like any other edit, so watch mode
closes that loop too: edit a step in the browser, the suite re-runs by itself.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

IGNORE_DIRS = {".git", ".specdeck", "__pycache__", ".pytest_cache", ".venv", "venv",
               "node_modules", ".mypy_cache", ".ruff_cache", "build", "dist", ".tox"}
WATCH_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".cfg", ".ini"}


MAX_HASH_BYTES = 4 * 1024 * 1024


def _signature(p: Path) -> tuple:
    """(size, mtime_ns, digest) — the digest because mtime alone is not enough.

    Two writes inside one clock tick get the *same* `st_mtime_ns`, so an edit
    that keeps the file the same length (`x = 1` -> `x = 2`) is invisible to a
    stat-only watcher. Watched files are source files, so hashing them is cheap;
    anything unexpectedly large falls back to stat only.
    """
    st = p.stat()
    if st.st_size > MAX_HASH_BYTES:
        return (st.st_size, st.st_mtime_ns, None)
    import hashlib

    return (st.st_size, st.st_mtime_ns, hashlib.blake2b(p.read_bytes(), digest_size=16).digest())


def snapshot(root: Path) -> dict:
    """path -> signature for every watched file under root."""
    out: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() not in WATCH_SUFFIXES:
                continue
            p = Path(dirpath) / name
            try:
                out[str(p)] = _signature(p)
            except OSError:
                continue
    return out


def changed(before: dict, after: dict) -> list:
    """Files added, removed or touched between two snapshots."""
    return sorted(set(before) ^ set(after)) + sorted(
        k for k in (set(before) & set(after)) if before[k] != after[k])


def watch(root: Path, pytest_args: list, *, interval: float = 0.6, live: bool = True,
          runner=None, once: bool = False) -> int:
    """Run the suite, then re-run it whenever a watched file changes.

    `runner` is injectable so tests drive this without spawning pytest.
    """
    root = Path(root).resolve()
    cmd = [sys.executable, "-m", "pytest", *pytest_args]
    if live and not any(a.startswith("--specdeck-live") for a in pytest_args):
        cmd.append("--specdeck-live")

    def run() -> int:
        print(f"specdeck watch: running {' '.join(cmd[2:])}", flush=True)
        if runner is not None:
            return runner(cmd)
        return subprocess.call(cmd, cwd=str(root))

    # Snapshot BEFORE running, so an edit made while the suite is running is
    # still seen as a change on the next poll instead of being absorbed.
    state = snapshot(root)
    rc = run()
    if once:
        return rc
    print(f"specdeck watch: watching {len(state)} files under {root} — Ctrl-C to stop", flush=True)
    try:
        while True:
            time.sleep(interval)
            fresh = snapshot(root)
            diff = changed(state, fresh)
            if not diff:
                continue
            shown = ", ".join(Path(p).name for p in diff[:3])
            more = f" (+{len(diff) - 3})" if len(diff) > 3 else ""
            print(f"\nspecdeck watch: changed: {shown}{more}", flush=True)
            time.sleep(0.1)  # settle: editors often write in several steps
            state = snapshot(root)
            rc = run()
    except KeyboardInterrupt:
        print("\nspecdeck watch: stopped", flush=True)
    return rc
