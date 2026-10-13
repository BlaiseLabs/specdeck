"""Requirement quality, checked against ISO/IEC/IEEE 29148:2018.

What a tool can check of the standard's quality criteria, each diagnostic citing its clause:

- §5.2.4 requirements construct: a binding requirement uses "shall"; positive statements, not
  "shall not"; active voice, not "it is required that"; not "shall be able to".
- §5.2.5 characteristics of an individual requirement: *verifiable* (a linked test, or a declared
  non-test verification method, §6.5.2), *necessary* (its rationale is recorded, §5.2.8),
  *singular* (one capability: one "shall"). *Conforming* is the attribute check in
  ``requirements.load``. *Appropriate*, *feasible* and *correct* need judgement; the generated
  specification lists them for review rather than pretending to check them.
- §5.2.6 characteristics of a set: *complete* (no TBD/TBS/TBR, every test traced to a
  requirement), *consistent* (unique ids, which ``requirements.load`` checks, and no links to
  requirements that do not exist).
- §5.2.7 requirement language criteria: the kinds of unbounded or ambiguous terms the standard
  says to avoid. The word lists are specdeck's; the categories are the standard's.

Everything here is a warning: a requirement can be useful before it is well formed, and
``specdeck validate --strict`` is the gate for teams that want the standard enforced.
"""
from __future__ import annotations

import re
from typing import Optional

STANDARD = "ISO/IEC/IEEE 29148:2018"

# §5.2.7: the standard's categories of unbounded or ambiguous terms. specdeck's word lists.
LANGUAGE = {
    "superlative": ["best", "most", "worst", "least", "optimal", "fastest", "slowest", "maximum", "minimum"],
    "subjective": ["user friendly", "user-friendly", "easy to use", "easy", "simple", "intuitive", "cost effective",
                   "cost-effective", "seamless", "nice", "clean", "elegant", "robust"],
    "vague pronoun": ["it", "this", "they", "them"],
    "ambiguous": ["almost always", "significant", "minimal", "approximately", "several", "various", "adequate",
                  "reasonable", "sufficient", "normally", "generally", "usually", "typically", "quickly", "fast",
                  "slow", "large", "small", "many", "few", "and/or"],
    "open-ended": ["provide support", "but not limited to", "as a minimum", "etc", "and so on", "such as"],
    "comparative": ["better than", "higher quality", "faster than", "more than", "less than", "improved"],
    "loophole": ["if possible", "as appropriate", "as applicable", "where possible", "if practical",
                 "as needed", "if necessary", "when needed"],
    "totality": ["all", "always", "never", "every", "none", "any"],
}
_TBX = re.compile(r"\b(TBD|TBS|TBR)\b")


def _diag(severity: str, code: str, message: str, clause: str, rid: Optional[str] = None, line=None):
    from .validation import Diagnostic
    return Diagnostic(severity, code, f"{message} ({STANDARD} {clause})", scenario=rid, line=line, clause=clause)


def language(text: str) -> list:
    """[(category, term)] for each §5.2.7 term found in a requirement statement, in order of the list."""
    found = []
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    for category, terms in LANGUAGE.items():
        for term in terms:
            if re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", low):
                found.append((category, term))
    return found


def check_requirement(req) -> list:
    """§5.2.4, §5.2.5 (necessary, singular) and §5.2.7 for one requirement."""
    out = []
    text = " ".join(req.statement.split())
    low = text.lower()
    shalls = len(re.findall(r"\bshall\b", low))
    if shalls == 0:
        out.append(_diag("warning", "NOT_SHALL", 'a binding requirement is stated with "shall" '
                         '("should" is a goal, "will" a statement of fact, "must" is best avoided)', "§5.2.4",
                         req.id, req.line))
    if re.search(r"\bshall not\b", low):
        out.append(_diag("warning", "NEGATIVE", 'state it positively rather than with "shall not"', "§5.2.4",
                         req.id, req.line))
    if re.search(r"\bshall be able to\b", low):
        out.append(_diag("warning", "ABLE_TO", 'avoid "shall be able to"; state what the system shall do', "§5.2.4",
                         req.id, req.line))
    if re.search(r"\bit is required that\b", low):
        out.append(_diag("warning", "PASSIVE", 'use the active voice, not "it is required that"', "§5.2.4",
                         req.id, req.line))
    if shalls > 1 or ";" in text:
        out.append(_diag("warning", "NOT_SINGULAR", "states more than one capability; split it so each requirement "
                         "is singular", "§5.2.5", req.id, req.line))
    if not req.rationale:
        out.append(_diag("warning", "NO_RATIONALE", "no rationale: the reason the requirement is necessary is not "
                         "recorded", "§5.2.5, §5.2.8.2", req.id, req.line))
    if _TBX.search(text):
        out.append(_diag("warning", "TBX", "contains a TBD/TBS/TBR placeholder; the set is not complete until it is "
                         "resolved", "§5.2.6", req.id, req.line))
    for category, term in language(text):
        out.append(_diag("warning", "LANGUAGE", f'"{term}" is {_article(category)} {category} term; these are hard '
                         f"to verify or open to more than one reading", "§5.2.7", req.id, req.line))
    return out


def _article(word: str) -> str:
    return "an" if word[0] in "aeiou" else "a"


def check_set(reqs, scenarios: Optional[list]) -> list:
    """§5.2.5 verifiable and §5.2.6 complete/consistent, using the generated spec's links.

    ``scenarios``: the generated spec's scenarios (each with tags ``req:<id>``), or None when no
    spec is available, in which case only the requirements file itself is checked.
    """
    out = []
    for req in reqs.items.values():
        out += check_requirement(req)
    if scenarios is None:
        return out
    linked: dict = {}
    for sc in scenarios:
        ids = [t[4:] for t in sc.get("tags") or [] if isinstance(t, str) and t.startswith("req:")]
        for rid in ids:
            linked.setdefault(rid, []).append(sc["id"])
        if not ids:
            out.append(_diag("warning", "UNTRACED_TEST", f'scenario "{sc["id"]}" traces to no requirement: behaviour '
                             "the requirements do not describe", "§5.2.6", sc["id"]))
    for req in reqs.items.values():
        non_test = [m for m in req.verification if m != "test"]
        if req.id not in linked and not non_test:
            out.append(_diag("warning", "NOT_VERIFIED", "no test verifies it and no other verification method is "
                             "declared", "§5.2.5, §6.5.2", req.id, req.line))
    for rid, ids in linked.items():
        if rid not in reqs.items:
            out.append(_diag("warning", "UNKNOWN_REQUIREMENT", f"{len(ids)} scenario(s) cover {rid!r}, which is not "
                             "in the requirements file", "§5.2.6", rid))
    return out
