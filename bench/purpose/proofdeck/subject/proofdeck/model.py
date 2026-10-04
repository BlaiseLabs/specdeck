"""Core data model. These types are real; the behaviour lives in the stubs."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class LineStatus(str, Enum):
    """What the tests did with one changed line."""

    CHECKED = "checked"        # a mutant on this line makes a test fail
    RAN_ONLY = "ran-only"      # tests execute it, but a mutant survives
    NEVER_RAN = "never-ran"    # no test executes it
    MOCK_ONLY = "mock-only"    # only checked through tests that mock what it calls
    IMPORT_TIME = "import-time"  # runs when the module is imported; fork mode can't switch a mutant on it


class FindingKind(str, Enum):
    SURVIVING_MUTANT = "surviving-mutant"
    UNCONFIRMED_EXPECTATION = "unconfirmed-expectation"   # (a) expected value born with the code
    RECOMPUTED_EXPECTATION = "recomputed-expectation"     # (b) expected value computed like the code
    SPECIAL_CASED_INPUT = "special-cased-input"           # (c) code branches on the test's exact input
    MOCK_ONLY = "mock-only"                               # (d) checked only via mocks
    VACUOUS_ASSERTION = "vacuous-assertion"               # assert True / is not None / f(x) == f(x)
    WEAKENED_TEST = "weakened-test"                       # (e) skips added, assertions removed
    CHECKER_TAMPERING = "checker-tampering"               # (e) pragma no mutate, excludes, config edits
    CONTRADICTS_REQUIREMENT = "contradicts-requirement"   # a linked test disagrees with an approved example
    REQUIREMENT_CHANGED = "requirement-changed"           # an approved requirement edited on the branch
    DRAFT_REQUIREMENT = "draft-requirement"               # a requirement added on the branch, for review


@dataclass(frozen=True)
class Mutant:
    path: str
    line: int
    operator: str
    original: str
    mutated: str


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    path: str
    line: int
    message: str
    mutant: Optional[Mutant] = None
    snippet: str = ""          # the source line the finding points at


@dataclass
class Report:
    lines: dict = field(default_factory=dict)       # (path, line) -> LineStatus
    findings: list = field(default_factory=list)    # [Finding]
    sources: dict = field(default_factory=dict)     # (path, line) -> source text, for display
    stats: dict = field(default_factory=dict)       # how the run went: mode, timings, process starts
    notes: list = field(default_factory=list)       # things the reader should know about the run
    ran_by: dict = field(default_factory=dict)      # (path, line) -> [nodeid]: tests that execute the line
    killed_by: dict = field(default_factory=dict)   # (path, line) -> [nodeid]: tests that noticed its mutant
    fakes: dict = field(default_factory=dict)       # nodeid -> [names]: what each test replaces with fakes
    at_import: set = field(default_factory=set)     # (path, line) run while importing: every test "ran" it

    def findings_of(self, kind: FindingKind) -> list:
        return [f for f in self.findings if f.kind == kind]

    def status(self, path: str, line: int) -> Optional[LineStatus]:
        return self.lines.get((path, line))
