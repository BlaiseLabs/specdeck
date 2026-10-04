"""proofdeck — does each changed line have a test that would notice if it were wrong?

    proofdeck check --base main

For every changed source line it reports checked / ran-only / never-ran /
mock-only, plus findings for tests whose expected values can't be trusted:
unconfirmed, recomputed, special-cased, vacuous, weakened, tampered.
"""
from .analyze import analyze
from .model import Finding, FindingKind, LineStatus, Mutant, Report

__all__ = ["analyze", "Finding", "FindingKind", "LineStatus", "Mutant", "Report"]
