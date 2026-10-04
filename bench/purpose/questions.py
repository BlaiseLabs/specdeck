"""Business-purpose questions about specdeck's own repo.

Every answer is mechanical: the "which tests" answers are the covers= links in
.specdeck/requirements.json (checked by verify_truth below), and the "why" answers are key
concepts from requirements.yaml. Requirements are always named by their `says` sentence,
never by id, because ids exist only in the specdeck copy.

Every question is answerable in principle in all three copies — test names and bodies,
source docstrings and comments stay in all of them — but how much reading it takes differs,
which is the point. The set questions (Q6–Q8) also depend on which *other* needs a test
serves; the plain copy has to infer those, so they are reported separately.
"""
import json
import re
from pathlib import Path

TRUTH_FILE = Path("/workspaces/python/specdeck/.specdeck/requirements.json")


def _re(p):
    return re.compile(p, re.I | re.S)


def _all(*pats):
    ps = [_re(p) for p in pats]
    return lambda a: all(p.search(a) for p in ps)


def _names(*must, never=()):
    """Every name in `must` mentioned, none of `never`."""
    return lambda a: all(n in a for n in must) and not any(n in a for n in never)


GATE_OPTIONAL = ["test_the_report_exits_zero_unless_a_gate_is_asked_for", "test_gates_are_off_by_default",
                 "test_gate_can_be_set_in_config"]
ENFORCEABLE_ONLY = ["test_fail_on_unmet_fails_the_run", "test_fail_on_unmet_passes_when_covered",
                    "test_fail_on_unknown_covers"]
ENFORCEABLE_SHARED = ["test_gate_can_be_set_in_config", "test_fail_on_invalid_spec"]
PARTIAL_SAFE = ["test_partial_run_can_merge_instead_of_skipping",
                "test_merge_without_an_existing_spec_explains_itself", "test_partial_run_still_refuses_by_default"]
MAINTENANCE_OTHER = ["test_prune_keeps_the_newest_runs", "test_forget_removes_a_project", "test_compare_two_runs",
                     "test_watch_snapshot_notices_edits", "test_watch_ignores_generated_directories",
                     "test_watch_runs_the_suite_immediately"]

LOSS = r"(delet|drop|lose|lost|los(es|ing)|remov|disappear|wip(e|ing)|shrink|vanish|discard|erase|overwrit)"

QUESTIONS = [
    ("Q1", "tests/test_maintenance.py::test_partial_run_still_refuses_by_default — what would go wrong for "
           "users if the behaviour it checks were removed? Explain the reason the project cares.",
     _all(LOSS, r"(not run|n.t run|unrun|weren.t|unselected|deselected|not (selected|collected)|subset|"
                r"other (scenarios|tests)|rest of|the rest|outside|only the)"), "why"),
    ("Q2", "Scenarios can declare `depends_on` other scenarios. Why does the project use that only to order "
           "reading and reporting, and never to control the order in which tests execute?",
     _all(r"(alone|isolat|independen|parallel|any order|order.dependen|anti.pattern|coupl|wrong reason|"
          r"in sequence|shuffl|random)"), "why"),
    ("Q3", "When most of the test suite runs one source function, the document written for coding agents "
           "shows that function as a count (e.g. `most (47 of 88)`) instead of listing the scenarios. "
           "Why? What went wrong, or would go wrong, with the list?",
     _all(r"(noise|mislead|misread|over.?(predict|report|stat|estimat|flag)|false (positive|alarm)|"
          r"not (useful|informative|evidence|signal)|same for every|every change|too many|all of them|"
          r"\b87\b|as (affected|failing|broken)|would break|look like evidence|signal)"), "why"),
    ("Q4", "What business need does tests/test_gates.py::test_parametrized_alias_survives_an_id_collision "
           "protect? Answer with the guarantee the project makes, in a sentence.",
     lambda a: bool(re.search(r"(depend|prerequisite|depends_on).{0,160}(exist|dangl|resolv|real|unknown|"
                              r"missing|never existed|nonexist|valid)", a, re.I | re.S)
                    or re.search(r"(exist|dangl|resolv|unknown|missing|never existed|nonexist).{0,160}"
                                 r"(depend|prerequisite|depends_on)", a, re.I | re.S)), "reverse"),
    ("Q5", "What business need does tests/test_ci.py::test_statuses_come_from_the_spec_tags_and_blocks_cascade "
           "serve? Say what the user gets that this test protects.",
     _all(r"(pull.?request|\bPR\b|markdown|comment)"), "reverse"),
    ("Q6", "The project guarantees that adding specdeck to a test suite never changes the suite's exit code "
           "unless a gate is switched on. List every test function (file::function) you would check to "
           "confirm that guarantee still holds.",
     _names(*GATE_OPTIONAL), "set"),
    ("Q7", "Suppose the project dropped the guarantee that an unmet requirement or an unknown covers= id can "
           "fail the run. Which test functions could then be deleted because they serve no other "
           "guarantee? List only those (file::function).",
     _names(*ENFORCEABLE_ONLY, never=ENFORCEABLE_SHARED), "set"),
    ("Q8", "Suppose the project dropped the guarantee that a partial test run never silently shrinks the "
           "generated spec. Which test functions could then be deleted because they serve no other "
           "guarantee? List only those (file::function).",
     _names(*PARTIAL_SAFE, never=MAINTENANCE_OTHER), "set"),
    ("Q9", "Why are specdeck's gates (the options that make the run fail on unmet requirements, unknown "
           "covers= ids or an invalid spec) switched off unless a user turns them on?",
     _all(r"(uninstall|adopt|on arrival|drop.in|non.?disruptive|safe to (add|install)|"
          r"(break|fail|disrupt|surpris|chang).{0,60}(build|CI|pipeline|existing|their|other|people|users|"
          r"exit code))"), "why"),
    ("Q10", "What does tests/test_context.py::test_the_document_says_what_depends_on_actually_means protect, "
            "and why does the project care about it?",
     _all(r"block.{0,160}(after|later|earlier|before|ran first|runs first|order)",
          r"(mis|wrong|incorrect|obvious|assum|otherwise|confus|40|naive|upstream)"), "reverse+why"),
]

SET_TRUTH = {"Q6": GATE_OPTIONAL, "Q7": ENFORCEABLE_ONLY, "Q8": PARTIAL_SAFE}


def verify_truth() -> None:
    """The test lists above must be exactly what covers= says."""
    reqs = {r["id"]: {s["nodeid"].split("::")[1] for s in r["scenarios"]}
            for r in json.loads(TRUTH_FILE.read_text())["requirements"]}
    owners = {}
    for rid, tests in reqs.items():
        for t in tests:
            owners.setdefault(t, set()).add(rid)
    assert reqs["gate.optional"] == set(GATE_OPTIONAL)
    assert reqs["gate.enforceable"] == set(ENFORCEABLE_ONLY) | set(ENFORCEABLE_SHARED)
    assert all(owners[t] == {"gate.enforceable"} for t in ENFORCEABLE_ONLY)
    assert all(len(owners[t]) > 1 for t in ENFORCEABLE_SHARED)
    assert reqs["run.partial-safe"] == set(PARTIAL_SAFE) and all(owners[t] == {"run.partial-safe"} for t in PARTIAL_SAFE)
    assert not any(t in owners for t in MAINTENANCE_OTHER)
    assert owners["test_parametrized_alias_survives_an_id_collision"] == {"deps.resolvable"}
    assert owners["test_statuses_come_from_the_spec_tags_and_blocks_cascade"] == {"report.pr-comment"}
    assert owners["test_partial_run_still_refuses_by_default"] == {"run.partial-safe"}
    assert owners["test_the_document_says_what_depends_on_actually_means"] == {"context.states-the-rule"}
    assert owners["test_a_function_most_of_the_suite_runs_is_counted_not_listed"] == {"context.hubs-are-not-evidence"}
    print("ground truth matches covers=")


if __name__ == "__main__":
    verify_truth()
