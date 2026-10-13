"""The adherence policy: how an agent is to treat the project's rules.

The rules themselves are requirements (design constraints, standards compliance, process). The
adherence policy is different: it says what keeping a rule means — that getting a rule's test to
pass is not the same as keeping the rule, and what to do when a request conflicts with one.
`specdeck agents` writes it into AGENTS.md, above the rules.

It is a list of clauses, each with an id. specdeck ships default clauses; a project changes them
in an `adherence:` block of requirements.yaml:

    adherence:
      conflicts: If a request conflicts with a rule, stop and ask before going on.   # reword
      intent: false                                                                     # drop
      review: Leave a TODO naming the rule wherever you were unsure.                   # add

``adherence: false`` drops the policy altogether. Without a block, the defaults apply.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_SOURCE = "specdeck default"


@dataclass(frozen=True)
class Clause:
    id: str
    text: str
    rationale: str = ""
    evidence: str = ""        # what was measured, for specdeck's own default clauses


@dataclass(frozen=True)
class Policy:
    clauses: tuple = ()
    source: str = DEFAULT_SOURCE      # where the policy came from: the default, or the file that changed it

    def get(self, cid: str):
        return next((c for c in self.clauses if c.id == cid), None)

    def text(self) -> str:
        """The clauses as one paragraph, the form that was measured."""
        return " ".join(c.text for c in self.clauses)


DEFAULT = Policy((
    Clause("intent",
           "A rule's test checks the rule. Making the test pass without keeping the rule (a dynamic import, "
           "a check sidestepped) is breaking the rule.",
           rationale="A test checks one way of breaking a rule. Under pressure an agent can satisfy the test and "
                     "break the rule anyway, which hides the break from everyone reading the results.",
           evidence="bench/rules_ab: asked to use pandas in a stdlib-only project, 2 of 2 agents with a rule test "
                    "imported it through importlib to get past the test; with this clause 4 of 4 kept the rule."),
    Clause("conflicts",
           "When a request asks for something a rule forbids, do not do that part. Keep the rule, and start your "
           "summary with \"Not done as asked:\", naming what you did not do, the rule, and what you did instead.",
           rationale="A person who asks for something a rule forbids may not know the rule. Keeping the rule "
                     "silently leaves them thinking they got what they asked for; a fixed opening makes the "
                     "departure impossible to miss and easy to search for.",
           evidence="bench/rules_ab, four wordings on six requests that pulled against a rule: summaries that "
                    "said plainly what was not done and why — no clause 0 of 6, \"keep the rule and say so\" 3 of "
                    "6, this wording 5 of 6. No rule was broken under any wording."),
))


def resolve(block, source: str) -> tuple:
    """(Policy, problems) from the `adherence:` block of a requirements file (None when there is none)."""
    if block is None:
        return DEFAULT, []
    if block is False:
        return Policy((), source), []
    if not isinstance(block, dict):
        return DEFAULT, [f"{source}: `adherence:` should map clause ids to text (or `false` to drop the policy); "
                         f"the default policy is used"]
    problems: list = []
    clauses = {c.id: c for c in DEFAULT.clauses}
    order = [c.id for c in DEFAULT.clauses]
    for cid, value in block.items():
        cid = str(cid)
        if value is False or value is None:
            if cid not in clauses:
                problems.append(f"{source}: adherence clause {cid!r} is dropped but specdeck has no such default "
                                f"clause ({', '.join(order) or 'none'})")
            clauses.pop(cid, None)
            continue
        if isinstance(value, dict):
            text, rationale = str(value.get("text") or "").strip(), str(value.get("rationale") or "").strip()
        elif isinstance(value, str):
            text, rationale = value.strip(), ""
        else:
            problems.append(f"{source}: adherence clause {cid!r} should be text, a mapping with `text:`, or false")
            continue
        if not text:
            problems.append(f"{source}: adherence clause {cid!r} has no text")
            continue
        clauses[cid] = Clause(cid, " ".join(text.split()), rationale)
        if cid not in order:
            order.append(cid)
    return Policy(tuple(clauses[c] for c in order if c in clauses), source), problems
