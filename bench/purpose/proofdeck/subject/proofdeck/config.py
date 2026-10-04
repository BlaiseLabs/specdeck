"""Configuration, always read from the base branch so a pull request can't loosen its own checks."""
from __future__ import annotations

import tomllib

from .git import base_text

DEFAULTS = {
    "exclude": [],                 # glob patterns of source files never mutated
    "max_mutants_per_file": 25,    # mutation budget per file (reported findings are not capped)
    "test_command": "",            # default: "<python> -m pytest"
    "timeout": 60,                 # seconds a mutant's tests may take, at most (fork mode scales it down
                                   # to 10x the tests' normal time); subprocess mode: at least
    "workers": 0,                  # mutants run in parallel; 0 = one per CPU, up to 8
    "mode": "auto",                # auto (fork where available) | fork | subprocess
    "coverage": "",                # coverage.py file with per-test contexts from CI; empty = trace
}


def load(repo, rev: str) -> dict:
    cfg = dict(DEFAULTS)
    text = base_text(repo, rev, "pyproject.toml")
    if text:
        try:
            cfg.update(tomllib.loads(text).get("tool", {}).get("proofdeck", {}) or {})
        except tomllib.TOMLDecodeError:
            pass
    return cfg
