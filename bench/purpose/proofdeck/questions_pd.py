"""Business-purpose questions about proofdeck, written after requirements.yaml and links.yaml
were frozen and before any session ran. Frozen: the hash of this file is in RESULTS.md.

Answers are mechanical: "why" answers are key concepts from a requirement's `why`; test
lists are the covers= links (links.yaml, checked against the requirements report of a real
run by verify_truth). Requirements are named by what they say, never by id, because ids
exist only in the specdeck copies.
"""
import json
import re
from pathlib import Path

import yaml

HERE = Path(__file__).parent


def _re(p):
    return re.compile(p, re.I | re.S)


def _all(*pats):
    ps = [_re(p) for p in pats]
    return lambda a: all(p.search(a) for p in ps)


def _names(*must, never=()):
    return lambda a: all(n in a for n in must) and not any(n in a for n in never)


ADVISORY = ["test_unconfirmed_expectations_do_not_fail_the_gate",
            "test_correct_branch_passes_with_its_new_expectations_marked_unconfirmed"]
MOCK_ONLY = ["test_line_is_mock_only_when_checked_only_through_mocks",
             "test_redirecting_stdin_or_argv_does_not_make_a_test_mock_only",
             "test_mocked_away_code_is_not_reported_as_tested"]
UNPROVEN_ONLY = ["test_line_is_checked_when_its_mutant_is_killed", "test_line_is_ran_only_when_its_mutant_survives",
                 "test_line_is_never_ran_when_no_test_executes_it", "test_gate_fails_when_a_mutant_survives"]
UNPROVEN_SHARED = ["test_vacuous_tests_fail_the_gate_at_the_buggy_line", "test_mocked_away_code_is_not_reported_as_tested",
                   "test_code_checked_against_itself_fails_the_gate"]
PROVENANCE_ONLY = ["test_correct_new_expectations_are_also_unconfirmed", "test_expectation_that_existed_on_main_is_confirmed",
                   "test_expectation_loaded_from_a_preexisting_data_file_is_confirmed",
                   "test_a_confirmation_comment_written_on_the_branch_does_not_count",
                   "test_pasted_expectations_are_surfaced_even_though_every_mutant_dies"]
PROVENANCE_SHARED = ["test_expectation_born_with_the_code_is_unconfirmed",
                     "test_an_expected_value_that_agrees_with_an_approved_example_is_confirmed",
                     "test_correct_branch_passes_with_its_new_expectations_marked_unconfirmed"]

QUESTIONS = [
    ("Q1", "proofdeck refuses to treat a comment such as '# confirmed' written beside an expected value on "
           "the branch as confirming that value. Why? What is the reasoning?",
     _all(r"(agent|author|wrote the code|same (person|source|branch|change|pull|PR|author)|could (just |also |easily )?"
          r"(write|add)|anyone|not independent|independen|self.?(certif|confirm|attest))"), "why"),
    ("Q2", "proofdeck never reports a mutation score or a percentage, only findings at lines. Why?",
     _all(r"(actionab|concrete|Google|(point|anchor|tell|show|say)s?.{0,60}(which line|the line|exact|where|what to fix))"),
     "why"),
    ("Q3", "proofdeck never mutates a function's default argument values. Why not?",
     _all(r"(Google|reject|noise|noisy|ignore|not actionable|unactionable|unhelpful|low.value|reviewers?)"), "why"),
    ("Q4", "If a branch edits an existing requirement's example (for instance to match what its new code "
           "returns), proofdeck fails the gate. Why does the project care?",
     _all(r"(review|approv|independen|both come from|same (branch|change|pull|PR)|their own (change|PR|pull)|"
          r"separate (change|PR|pull)|goal.?post|match(es)? (the|its|their) (buggy |new )?(code|output|behaviou?r)|"
          r"rewrite the (spec|requirement)|launder)"), "why"),
    ("Q5", "What business need does tests/test_fork_mode.py::test_switched_code_behaves_like_the_mutant_and_keeps_line_numbers "
           "serve? Say what the user gets that this test protects.",
     _all(r"(fast|speed|quick|cost|cheap|start.?up|one pytest|single pytest|pytest (once|start)|"
          r"one process per mutant|same answers|overhead|efficien|perform)"), "reverse"),
    ("Q6", "What business need does tests/test_report.py::test_json_says_which_tests_ran_and_noticed_each_line_and_what_they_fake "
           "serve? Say who uses that information and what it lets them tell apart.",
     lambda a: bool(re.search(r"(requirement|specdeck|trust).{0,160}(fake|mock|real|proven|unproven)", a, re.I | re.S)
                    or re.search(r"(fake|mock).{0,160}(requirement|specdeck)", a, re.I | re.S)), "reverse"),
    ("Q7", "The project guarantees that unconfirmed expectations are listed for the reviewer but never fail the "
           "gate on their own. List every test function (file::function) you would check to confirm that "
           "guarantee still holds.",
     _names(*ADVISORY), "set"),
    ("Q8", "The project guarantees that a changed line whose change is noticed only by tests that fake its "
           "collaborators is reported mock-only rather than checked (and that feeding input through stdin or "
           "argv is not faking). List every test function (file::function) that checks this guarantee.",
     _names(*MOCK_ONLY), "set"),
    ("Q9", "Suppose the project dropped the guarantee that a changed line counts as checked only when breaking it "
           "makes a test fail, and that ran-only or never-ran lines fail the gate. Which test functions could then "
           "be deleted because they serve no other guarantee? List only those (file::function).",
     _names(*UNPROVEN_ONLY, never=UNPROVEN_SHARED), "set"),
    ("Q10", "Suppose the project dropped the guarantee that an expected value counts as confirmed only when it "
            "comes from a source independent of the branch. Which test functions could then be deleted because "
            "they serve no other guarantee? List only those (file::function).",
     _names(*PROVENANCE_ONLY, never=PROVENANCE_SHARED), "set"),
]


def verify_truth(requirements_json: Path | None = None) -> None:
    """The lists above must be what links.yaml says, and (given a real run's requirements
    report) what specdeck computed from the covers= links."""
    links = yaml.safe_load((HERE / "links.yaml").read_text())
    owners = {t: set(ids) for tests in links.values() for t, ids in tests.items()}
    reqs = {}
    for t, ids in owners.items():
        for i in ids:
            reqs.setdefault(i, set()).add(t)
    assert reqs["gate.unconfirmed-advisory"] == set(ADVISORY)
    assert reqs["status.mock-only"] == set(MOCK_ONLY)
    assert reqs["status.unproven-lines-fail"] == set(UNPROVEN_ONLY) | set(UNPROVEN_SHARED)
    assert all(owners[t] == {"status.unproven-lines-fail"} for t in UNPROVEN_ONLY)
    assert all(len(owners[t]) > 1 for t in UNPROVEN_SHARED)
    assert reqs["provenance.independent-source"] == set(PROVENANCE_ONLY) | set(PROVENANCE_SHARED)
    assert all(owners[t] == {"provenance.independent-source"} for t in PROVENANCE_ONLY)
    assert all(len(owners[t]) > 1 for t in PROVENANCE_SHARED)
    assert owners["test_switched_code_behaves_like_the_mutant_and_keeps_line_numbers"] == {"speed.fork-mode"}
    assert owners["test_json_says_which_tests_ran_and_noticed_each_line_and_what_they_fake"] == {"report.json-evidence"}
    assert owners["test_a_confirmation_comment_written_on_the_branch_does_not_count"] == {"provenance.independent-source"}
    assert owners["test_no_percentage_or_score_is_ever_reported"] == {"report.no-score"}
    assert owners["test_default_argument_values_are_arid"] == {"mutants.arid-skipped"}
    assert owners["test_changing_an_approved_requirement_on_the_branch_fails"] == {"requirements.approved-on-base"}
    if requirements_json:
        rep = json.loads(Path(requirements_json).read_text())
        got = {r["id"]: {s["nodeid"].split("::")[1].split("[")[0] for s in r["scenarios"]} for r in rep["requirements"]}
        assert got == reqs, {k: (got.get(k), reqs.get(k)) for k in set(got) | set(reqs) if got.get(k) != reqs.get(k)}
        assert all(r["status"] == "met" for r in rep["requirements"]), [r["id"] for r in rep["requirements"]
                                                                        if r["status"] != "met"]
    print("ground truth matches links.yaml" + (" and the run's covers= report" if requirements_json else ""))


if __name__ == "__main__":
    import sys
    verify_truth(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
