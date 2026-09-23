"""Spec generation from plain and annotated tests.

Each test here writes a tiny throwaway test module, runs pytest on it with the
plugin, and inspects the generated .specdeck/spec.yaml.
"""
import sys
from pathlib import Path

import pytest

from .conftest import scenario_by_id


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


VIEWER_ROOT = Path("/workspaces/python/dx-spec-viewer")


@pytest.mark.skipif(not (VIEWER_ROOT / "spec_validator").exists(), reason="dx-spec-viewer not checked out alongside")
def test_generated_spec_is_warning_free_under_dx_spec_viewer_validator(pytester, run):
    """Import the viewer's validator in-process (pytester rewrites HOME, which
    hides user-site packages from a subprocess) and require zero diagnostics —
    not just zero errors."""
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

    if str(VIEWER_ROOT) not in sys.path:
        sys.path.insert(0, str(VIEWER_ROOT))
    from spec_validator import validate_text  # type: ignore[import-not-found]

    diags = validate_text(out.read_text(encoding="utf-8"), source="spec.yaml")
    assert diags == [], [d.to_dict() for d in diags]
