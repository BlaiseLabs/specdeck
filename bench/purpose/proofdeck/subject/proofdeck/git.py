"""The few git operations proofdeck needs. The branch side is always the working tree."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


class GitError(RuntimeError):
    pass


def git(repo, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def merge_base(repo, base: str) -> str:
    """The commit the branch started from. Everything is compared against this."""
    try:
        return git(repo, "merge-base", base, "HEAD").strip()
    except GitError as exc:
        raise GitError(f"can't compare against {base!r}: {exc}") from exc


def base_text(repo, rev: str, path: str) -> Optional[str]:
    """File contents at `rev`, or None if it didn't exist there."""
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def branch_text(repo, path: str) -> Optional[str]:
    p = Path(repo) / path
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def base_files(repo, rev: str) -> list:
    return [p for p in git(repo, "ls-tree", "-r", "--name-only", rev).splitlines() if p]


def branch_files(repo) -> list:
    tracked = git(repo, "ls-files").splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return [p for p in dict.fromkeys(tracked + untracked) if p and (Path(repo) / p).is_file()]
