# Where each `why` in requirements.yaml comes from

Every `why` paraphrases or quotes proofdeck's own written rationale in the frozen snapshot
(`subject/`, working tree of /workspaces/python/proofdeck on 2026-10-04) or its commit
messages. Nothing was added that proofdeck does not say itself.

| Requirement | Source of the why |
|---|---|
| mutants.arid-skipped | `proofdeck/arid.py` module docstring ("a mutant would only produce noise", "Reviewers ignore findings there"); `tests/test_arid.py::test_default_argument_values_are_arid` docstring ("the most-rejected Python mutant at Google") |
| mutants.most-useful-first | `proofdeck/mutate.py` module docstring ("Order follows what reviewers found actionable: comparison boundaries (where off-by-one bugs live)"); `tests/test_mutants.py::test_selection_takes_one_per_line_and_caps_each_file` docstring ("Google cut 820 mutants per change to 7 this way") |
| status.unproven-lines-fail | README first paragraph ("whether the tests would notice if that line were wrong"); `proofdeck/__init__.py` docstring; `tests/test_line_status.py::test_line_is_never_ran_when_no_test_executes_it` docstring ("not reported as tested"); `tests/test_acceptance.py::test_vacuous_tests_fail_the_gate_at_the_buggy_line` docstring |
| status.mock-only | `proofdeck/analyze.py` MSG_MOCK_ONLY ("it has never been checked against the real one"); `tests/test_line_status.py::test_redirecting_stdin_or_argv_does_not_make_a_test_mock_only` docstring ("fakes no collaborator, so a test that does only that is trusted like any other") |
| status.import-time | `tests/test_line_status.py::test_a_changed_import_time_line_is_not_reported_as_survived_by_fork_mode` docstring (quoted nearly verbatim); README status table |
| report.no-score | `proofdeck/report.py` module docstring (quoted) |
| report.guidance-from-requirement | `tests/test_report.py::test_guidance_never_tells_the_author_to_assert_current_output` docstring (quoted); README "The case it exists for" ("an agent following it cannot lock the bug in") |
| report.json-evidence | `tests/test_report.py::test_json_says_which_tests_ran_and_noticed_each_line_and_what_they_fake` docstring; README "`specdeck trust` joins that ... met but unproven, or met only through fakes" |
| config.from-base | `proofdeck/config.py` docstring ("so a pull request can't loosen its own checks"); README ("so a branch cannot exempt its own code"); `tests/test_requirements.py::test_the_requirements_file_location_comes_from_the_base_branch` docstring ("the branch can't move it") |
| tamper.reported | `proofdeck/tampering.py` docstring ("in a way that hides code or tests", "can't take effect in the same pull request; it is still reported"); README ("pragmas never exempt anything; adding one is reported") |
| provenance.independent-source | `proofdeck/provenance.py` module docstring (quoted nearly verbatim) |
| gate.unconfirmed-advisory | README Limits ("Unconfirmed expectations can't be told apart from correct new ones; the tool lists them for a person to check against the requirement") |
| detect.untrustworthy-oracles | README first paragraph ("a passing test only proves that the code agrees with itself"); `proofdeck/detectors.py` docstring ("tests whose expected value can't disagree with the code, or can't fail at all") |
| requirements.contradiction-gates | README Requirements section ("That catches the pasted-from-buggy-output test that every mutant misses", "Only tests that claim a requirement are compared with it, so the same argument values in an unrelated test never count"); commit ab5a0bf |
| requirements.approved-on-base | `proofdeck/ledger.py` docstring ("a person reviewed it when it was merged", "the code and the requirement it's checked against can't both come from the branch"); README ("requirements change in their own reviewed change, not alongside the code") |
| speed.fork-mode | commit 5c29948 message ("Pytest start-up and collection was ~95% of each mutant's cost", "9.4 s ... 3.3 s ... 2.6 s"); `proofdeck/_forkrun.py` docstring ("a mutant costs its tests' run time plus a few milliseconds, not a pytest start-up") |

Sources by kind (an entry can have several): module docstrings 10, test docstrings 9,
README 10, commit messages 2, code message strings 1.
