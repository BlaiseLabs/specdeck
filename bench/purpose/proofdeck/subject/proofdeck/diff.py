"""Which lines did the branch add or change? Only these are ever mutated."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .git import branch_text, git, merge_base

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    return (
        name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or any(part in ("tests", "test", "testing") for part in p.parts[:-1])
    )


def added_lines(repo, base: str = "main", *, tests: bool = False, sources: bool = True) -> dict:
    """{path: {line numbers}} of non-blank lines added or modified since the branch left `base`,
    in .py files, comparing the working tree (so uncommitted work counts too)."""
    rev = merge_base(repo, base)
    out: dict = {}
    current = None
    for line in git(repo, "diff", "--no-color", "--no-ext-diff", "-U0", "--find-renames", rev, "--", "*.py").splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            continue
        m = _HUNK.match(line)
        if m and current:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(current, set()).update(range(start, start + count))
    for path in git(repo, "ls-files", "--others", "--exclude-standard", "--", "*.py").splitlines():
        text = branch_text(repo, path) or ""
        out[path] = set(range(1, len(text.splitlines()) + 1))
    result = {}
    for path, lines in out.items():
        if is_test_path(path) and not tests or not is_test_path(path) and not sources:
            continue
        text = branch_text(repo, path)
        if text is None:
            continue
        src = text.splitlines()
        keep = {n for n in lines if 0 < n <= len(src) and src[n - 1].strip()}
        if keep:
            result[path] = keep
    return result


def changed_lines(repo, base: str = "main") -> dict:
    """Added or modified non-blank lines in non-test .py files: the mutation targets."""
    return added_lines(repo, base)
