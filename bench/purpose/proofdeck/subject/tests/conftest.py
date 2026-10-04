"""Fixtures: throwaway git repos, including the five demo variants.

Every variant shares the same story. `main` has `apply_discount` and a good
test. The `feature` branch adds `bulk_discount` / `order_total`; the spec says
100+ units get 15%. All variants except `good` ship the off-by-one bug
(`qty > 100`) with a different kind of test beside it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PYPROJECT = """\
[project]
name = "pricing-demo"
version = "0.0.1"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
"""

BASE_SRC = '''\
def apply_discount(price, percent):
    """Return price reduced by percent (0-100), rounded to cents."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (1 - percent / 100), 2)
'''

BASE_TEST = """\
import pytest
from pricing import apply_discount

def test_apply_discount_basic():
    assert apply_discount(100, 10) == 90.0
    assert apply_discount(19.99, 0) == 19.99
    assert apply_discount(50, 100) == 0.0

def test_apply_discount_rejects_out_of_range():
    with pytest.raises(ValueError):
        apply_discount(10, -1)
    with pytest.raises(ValueError):
        apply_discount(10, 101)
"""

FEATURE_SRC = '''

def bulk_discount(qty):
    """Percent discount for a quantity: 10+ units -> 5, 100+ units -> 15."""
    if qty {op} 100:
        return 15
    if qty >= 10:
        return 5
    return 0


def order_total(unit_price, qty, tax_rate=0.1):
    """Total with bulk discount applied, then tax."""
    pct = bulk_discount(qty)
    subtotal = apply_discount(unit_price * qty, pct)
    return round(subtotal * (1 + tax_rate), 2)
'''

VARIANT_TESTS = {
    "good": """\
from pricing import bulk_discount, order_total

def test_bulk_discount_tiers():
    assert bulk_discount(9) == 0
    assert bulk_discount(10) == 5
    assert bulk_discount(99) == 5
    assert bulk_discount(100) == 15

def test_order_total():
    assert order_total(10, 1) == 11.0
    assert order_total(10, 10) == 104.5
    assert order_total(10, 100) == 935.0
    assert order_total(10, 100, tax_rate=0) == 850.0
""",
    "vacuous": """\
from pricing import bulk_discount, order_total

def test_bulk_discount_runs():
    assert bulk_discount(50) is not None

def test_order_total_runs():
    result = order_total(10, 100)
    assert result is not None
    assert isinstance(result, float)
""",
    "tautological": """\
from pricing import bulk_discount, order_total

def test_bulk_discount_tiers():
    assert bulk_discount(9) == 0
    assert bulk_discount(10) == 5
    assert bulk_discount(100) == 5
    assert bulk_discount(101) == 15

def test_order_total():
    assert order_total(10, 1) == 11.0
    assert order_total(10, 10) == 104.5
    assert order_total(10, 100) == 1045.0
    assert order_total(10, 100, tax_rate=0) == 950.0
""",
    "mockonly": """\
from unittest.mock import patch
import pricing

def test_order_total_uses_bulk_discount():
    with patch("pricing.bulk_discount", return_value=5) as bd, \\
         patch("pricing.apply_discount", return_value=950.0) as ad:
        assert pricing.order_total(10, 100) == 1045.0
        bd.assert_called_once_with(100)
        ad.assert_called_once_with(1000, 5)

def test_bulk_discount():
    with patch("pricing.bulk_discount", return_value=15) as bd:
        assert pricing.bulk_discount(100) == 15
        bd.assert_called_once()
""",
    "selfref": """\
from pricing import bulk_discount, order_total

def test_bulk_discount():
    assert bulk_discount(100) == bulk_discount(100)

def test_order_total():
    expected = order_total(10, 100)
    assert order_total(10, 100) == expected
    assert True
""",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def write(repo: Path, files: dict) -> None:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def commit(repo: Path, files: dict, message: str) -> None:
    write(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def new_repo(path: Path, files: dict, message: str = "baseline") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    commit(path, {".gitignore": "__pycache__/\n.coverage*\n.proofdeck/\n.specdeck/\n", **files}, message)
    return path


def build_demo(path: Path, variant: str) -> Path:
    """The demo repo for `variant`, checked out on its `feature` branch."""
    repo = new_repo(
        path,
        {"pyproject.toml": PYPROJECT, "src/pricing.py": BASE_SRC, "tests/test_base.py": BASE_TEST},
        "baseline: apply_discount + tests",
    )
    git(repo, "checkout", "-qb", "feature")
    op = ">=" if variant == "good" else ">"
    commit(
        repo,
        {
            "src/pricing.py": BASE_SRC + FEATURE_SRC.format(op=op),
            "tests/test_new.py": VARIANT_TESTS[variant],
        },
        f"feature: bulk_discount + order_total ({variant} tests)",
    )
    return repo


def line_of(repo: Path, rel: str, needle: str) -> int:
    """1-based number of the first line in `rel` containing `needle`."""
    for i, text in enumerate((repo / rel).read_text().splitlines(), start=1):
        if needle in text:
            return i
    raise AssertionError(f"{needle!r} not found in {rel}")


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "proofdeck", *args],
        cwd=repo, env=env, capture_output=True, text=True, timeout=300,
    )


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


@pytest.fixture
def demo(tmp_path):
    return lambda variant: build_demo(tmp_path / variant, variant)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Run the end-to-end journeys last, after the building blocks they depend on.

    specdeck never reorders; it only skips a scenario whose prerequisite already
    failed. Putting acceptance at the end is what lets that skip take effect.
    """
    items.sort(key=lambda item: item.path.name == "test_acceptance.py")
