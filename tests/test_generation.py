"""Spec generation from plain and annotated tests.

Each test here writes a tiny throwaway test module, runs pytest on it with the
plugin, and inspects the generated .specdeck/spec.yaml.
"""
import sys
from pathlib import Path

import pytest

from specdeck import scenario

from .conftest import scenario_by_id


@scenario("generate-zero-config", group="generation", covers=["spec.generated"])
def test_unannotated_tests_become_scenarios_with_derived_metadata(pytester, run):
    pytester.makepyfile(
        test_alpha='''
        """Alpha module docstring becomes the group name."""

        def test_one():
            """Adds two numbers."""
            assert 1 + 1 == 2

        def test_two_things():
            assert True
        ''',
    )
    result, spec, needs = run()
    result.assert_outcomes(passed=2)

    assert spec["groups"] == [{"id": "alpha", "name": "Alpha module docstring becomes the group name."}]
    one = scenario_by_id(spec, "alpha--one")
    assert one["name"] == "Adds two numbers."  # docstring first line
    assert one["group"] == "alpha"
    assert "status:passed" in one["tags"] and "alpha" in one["tags"]
    assert one["logic"] == ["pytest test_alpha.py::test_one"]
    # synthesized steps for an un-annotated test
    assert [s["keyword"] for s in one["steps"]] == ["When", "Then"]
    assert one["steps"][1]["text"] == "it passes"

    two = scenario_by_id(spec, "alpha--two-things")
    assert two["name"] == "Two things"  # humanized function name when no docstring


def test_name_is_first_sentence_not_first_line(pytester, run):
    """A wrapped docstring must not produce a name that stops mid-sentence."""
    pytester.makepyfile(
        test_wrap='''
        def test_w():
            """Auto-tap: a plain script (no imports) constructs a gateway bot,
            events are pushed to it, and record captures them. Second sentence.

            Body paragraph becomes the description.
            """
        ''',
    )
    _, spec, _ = run()
    s = spec["scenarios"][0]
    assert s["name"] == (
        "Auto-tap: a plain script (no imports) constructs a gateway bot, "
        "events are pushed to it, and record captures them."
    )
    assert s["description"] == "Body paragraph becomes the description."


@scenario("generate-runtime-steps", group="generation", covers=["spec.generated"])
def test_marker_overrides_and_runtime_steps(pytester, run):
    pytester.makepyfile(
        test_beta='''
        from specdeck import scenario, given, when, then, and_

        @scenario("cap-toy", name="Capture the toy SDK", group="capture", tags=["wedge"])
        def test_capture():
            with given("a proxied toy client"):
                client = object()
            with when("a chained call is made"):
                pass
            with then("it is recorded under its access path", expected="toy.Client_result.send"):
                assert client is not None
            with and_("secrets are redacted"):
                pass
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(passed=1)
    s = scenario_by_id(spec, "cap-toy")
    assert s["name"] == "Capture the toy SDK"
    assert s["group"] == "capture" and spec["groups"][0] == {"id": "capture", "name": "Capture"}
    assert set(s["tags"]) >= {"wedge", "capture", "status:passed"}
    assert [(st["keyword"], st["text"]) for st in s["steps"]] == [
        ("Given", "a proxied toy client"),
        ("When", "a chained call is made"),
        ("Then", "it is recorded under its access path"),
        ("And", "secrets are redacted"),
    ]
    # a passing `then` with an expected label records it (observed == expected)
    assert s["steps"][2]["assertion"] == {
        "expected_label": "toy.Client_result.send",
        "observed_label": "toy.Client_result.send",
    }


def test_failing_then_records_expected_observed_and_decision_context(pytester, run):
    pytester.makepyfile(
        test_gamma='''
        from specdeck import when, then

        def test_priority():
            with when("the output path is resolved"):
                path = "env_result.json"
            with then(
                "the CLI flag wins",
                expected="out/result.json",
                decision_context=[("CLI --output", "cli_output"), ("ENV OUTPUT_PATH", "env_output")],
            ):
                assert path == "out/result.json"
        ''',
    )
    result, spec, needs = run()
    result.assert_outcomes(failed=1)
    s = scenario_by_id(spec, "gamma--priority")
    assert "status:failed" in s["tags"]
    then_step = s["steps"][1]
    assert then_step["assertion"]["expected_label"] == "out/result.json"
    assert "env_result.json" in then_step["assertion"]["observed_label"]  # from pytest's assert message
    assert then_step["decision_context"] == [
        {"source": "CLI --output", "key": "cli_output"},
        {"source": "ENV OUTPUT_PATH", "key": "env_output"},
    ]
    assert needs["versions"]["generated"]["needs"]["gamma--priority"]["status"] == "failed"


@scenario("deps-skip-blocked", group="generation", covers=["deps.document-only"])
def test_dependent_is_skipped_when_prerequisite_fails(pytester, run):
    pytester.makepyfile(
        test_delta='''
        from specdeck import scenario

        @scenario("base")
        def test_base():
            assert False, "boom"

        @scenario("child", depends_on=["base"])
        def test_child():
            assert True

        @scenario("by-function-name", depends_on=["test_base"])
        def test_named_dep():
            assert True
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(failed=1, skipped=2)
    child = scenario_by_id(spec, "child")
    assert child["depends_on"] == ["base"]
    assert "status:skipped" in child["tags"]
    assert scenario_by_id(spec, "by-function-name")["depends_on"] == ["base"]  # bare name resolved


@scenario("deps-unknown-dropped", group="generation", covers=["deps.resolvable"])
def test_unknown_dependency_is_dropped_with_a_warning(pytester, run):
    pytester.makepyfile(
        test_eps='''
        from specdeck import scenario

        @scenario("a", depends_on=["does-not-exist"])
        def test_a():
            pass
        ''',
    )
    result, spec, _ = run()
    assert "depends_on 'does-not-exist' does not match" in result.stdout.str()
    assert "depends_on" not in scenario_by_id(spec, "a")


@scenario("deps-cycle-warned", group="generation", covers=["deps.resolvable"])
def test_cycle_is_reported(pytester, run):
    pytester.makepyfile(
        test_zeta='''
        from specdeck import scenario

        @scenario("x", depends_on=["y"])
        def test_x(): pass

        @scenario("y", depends_on=["x"])
        def test_y(): pass
        ''',
    )
    result, _, _ = run()
    assert "dependency cycle" in result.stdout.str()


def test_no_specdeck_flag_suppresses_output(pytester, run):
    pytester.makepyfile(test_none="def test_a(): pass")
    result, spec, needs = run("--no-specdeck")
    result.assert_outcomes(passed=1)
    assert spec is None and needs is None


@scenario("generate-safe-yaml", group="generation", covers=["spec.safe-yaml"])
def test_step_text_with_colons_stays_parseable(pytester, run):
    """The classic test-failure-dx foot-gun: `key: value` text inside a step."""
    pytester.makepyfile(
        test_colon='''
        from specdeck import given

        def test_c():
            with given("set_env(var: FEATURE_X_ENABLED, value: false) is applied"):
                pass
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(passed=1)
    assert spec["scenarios"][0]["steps"][0]["text"].startswith("set_env(var: FEATURE_X_ENABLED")


@scenario("generate-valid-output", group="generation", covers=["spec.valid", "spec.generated"])
def test_generated_spec_is_warning_free_under_our_own_validator(pytester, run):
    """A generated spec must produce zero diagnostics — not merely zero errors.

    Checked with specdeck's own validator: specdeck does not depend on any other
    project to know whether the file it writes is valid.
    """
    pytester.makepyfile(
        test_eta='''
        from specdeck import scenario, given, then

        @scenario("first")
        def test_first():
            with given("something"):
                pass

        @scenario("second", depends_on=["first"], tags=["example"])
        def test_second():
            """Second scenario.

            With a longer description paragraph.
            """
            with then("it holds", expected="yes"):
                assert True
        ''',
    )
    result, _, _ = run()
    result.assert_outcomes(passed=2)
    out = pytester.path / ".specdeck" / "spec.yaml"

    from specdeck.validation import validate_text

    diags = validate_text(out.read_text(encoding="utf-8"), source="spec.yaml")
    assert diags == [], [d.to_dict() for d in diags]


@scenario("narrate-skipped", group="generation", covers=["spec.narrates-unrun"])
def test_skipped_scenario_keeps_its_narrated_steps(pytester, run):
    """Steps are read from the test source, so a scenario that never ran still reads as a spec."""
    pytester.makepyfile(
        test_plan='''
        import pytest
        from specdeck import scenario, given, when, then, and_

        KIND = "surviving-mutant"

        @scenario("later")
        @pytest.mark.skip(reason="not built yet")
        def test_later():
            with given("a branch with a vacuous test"):
                pass
            with when("it is analysed"):
                pass
            with then("the boundary mutant survives", expected=KIND):
                pass
            with and_("the gate fails", expected="exit 1"):
                pass
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(skipped=1)
    steps = scenario_by_id(spec, "later")["steps"]
    assert [(s["keyword"], s["text"]) for s in steps] == [
        ("Given", "a branch with a vacuous test"),
        ("When", "it is analysed"),
        ("Then", "the boundary mutant survives"),
        ("And", "the gate fails"),
    ]
    # module-level constants in expected= are resolved; nothing ran, so nothing was observed
    assert steps[2]["assertion"] == {"expected_label": "surviving-mutant", "observed_label": "not run"}


@scenario("narrate-failed-tail", group="generation", covers=["spec.narrates-unrun"])
def test_error_is_pinned_to_the_step_that_raised_and_later_steps_are_not_reached(pytester, run):
    """A non-assertion error lands on the step where it happened; the intended Then steps still show."""
    pytester.makepyfile(
        test_stub='''
        from specdeck import scenario, given, when, then

        def analyse():
            raise NotImplementedError("analyse")

        @scenario("stub")
        def test_stub():
            with given("a repo"):
                pass
            with when("it is analysed"):
                analyse()
            with then("findings are reported", expected="1 finding"):
                pass
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(failed=1)
    steps = scenario_by_id(spec, "stub")["steps"]
    assert [s["keyword"] for s in steps] == ["Given", "When", "Then"]
    assert steps[1]["assertion"]["observed_label"] == "NotImplementedError: analyse"
    assert steps[2]["text"] == "findings are reported"
    assert steps[2]["assertion"] == {"expected_label": "1 finding", "observed_label": "not reached"}


def test_parametrized_step_text_is_rendered_per_instance(pytester, run):
    """f-string step text is filled in from each parametrize value, even when the test was skipped."""
    pytester.makepyfile(
        test_param='''
        import pytest
        from specdeck import scenario, given

        @pytest.mark.skip(reason="later")
        @pytest.mark.parametrize("needle", ["print(", "log.info"])
        def test_arid(needle):
            with given(f"a line containing {needle!r}"):
                pass
        ''',
    )
    _, spec, _ = run()
    texts = sorted(s["steps"][0]["text"] for s in spec["scenarios"])
    assert texts == ["a line containing 'log.info'", "a line containing 'print('"]


@scenario("narrate-between-steps", group="generation", covers=["spec.narrates-unrun"])
def test_failure_between_steps_is_still_reported(pytester, run):
    """If the test fails outside any step, the failure is appended rather than hidden by 'not reached' steps."""
    pytester.makepyfile(
        test_gap='''
        from specdeck import scenario, given, then

        @scenario("gap")
        def test_gap():
            with given("a setup"):
                pass
            raise RuntimeError("between steps")
            with then("never"):
                pass
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(failed=1)
    steps = scenario_by_id(spec, "gap")["steps"]
    assert steps[1] == {"keyword": "Then", "text": "never", "assertion": {"observed_label": "not reached"}}
    assert steps[-1]["text"] == "the test passes"
    assert "RuntimeError" in steps[-1]["assertion"]["observed_label"]


@scenario("deps-blocking-cascade", group="generation", covers=["deps.document-only"])
def test_blocking_cascades_through_a_chain(pytester, run):
    """If a prerequisite was itself skipped as blocked, its dependents are blocked too (not run)."""
    pytester.makepyfile(
        test_chain='''
        from specdeck import scenario

        @scenario("a")
        def test_a():
            assert False

        @scenario("b", depends_on=["a"])
        def test_b():
            pass

        @scenario("c", depends_on=["b"])
        def test_c():
            assert False, "must not run"
        ''',
    )
    result, spec, _ = run()
    result.assert_outcomes(failed=1, skipped=2)
    c = scenario_by_id(spec, "c")
    assert "status:skipped" in c["tags"]
