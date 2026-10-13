"""CI gates and the two bugs they were written alongside.

Gates are opt-in: adding specdeck to a suite must never change its exit code
until someone asks for it.
"""
import textwrap

from specdeck import and_, given, scenario, then, when

REQS = textwrap.dedent("""
    requirements:
    - id: pricing.tax
      says: Tax is added to the order total.
      examples:
      - given: {qty: 1}
        expect: 2
""")


@scenario("gate-off-by-default", group="gates", tags=["ci"], covers=["gate.optional"])
def test_gates_are_off_by_default(pytester, run):
    """An unmet requirement does not fail the run unless a gate is switched on."""
    with given("a suite with a requirement no test covers"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_g="def test_a(): pass")
    with when("pytest runs with no gate flags"):
        result, _, _ = run()
    with then("the suite passes", expected="exit 0"):
        assert result.ret == 0


@scenario("gate-unmet", group="gates", tags=["ci"], depends_on=["gate-off-by-default"], covers=["gate.enforceable"])
def test_fail_on_unmet_fails_the_run(pytester, run):
    """--specdeck-fail-on-unmet turns an untested requirement into a failed run."""
    with given("a suite with a requirement no test covers"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_g="def test_a(): pass")
    with when("pytest runs with --specdeck-fail-on-unmet"):
        result, _, _ = run("--specdeck-fail-on-unmet")
    with then("the run fails and says which requirement", expected="exit 1, names pricing.tax"):
        assert result.ret == 1
        assert "pricing.tax" in result.stdout.str()
        assert "gate failed" in result.stdout.str()


@scenario("gate-unmet-passes-when-met", group="gates", tags=["ci"], depends_on=["gate-unmet"], covers=["gate.enforceable"])
def test_fail_on_unmet_passes_when_covered(pytester, run):
    """A covered, passing requirement satisfies the gate."""
    with given("a test that covers the requirement and passes"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_g='''
            from specdeck import scenario

            @scenario("t", covers=["pricing.tax"])
            def test_a(): pass
        ''')
    with when("pytest runs with --specdeck-fail-on-unmet"):
        result, _, _ = run("--specdeck-fail-on-unmet")
    with then("the run passes", expected="exit 0"):
        assert result.ret == 0


@scenario("gate-unknown-covers", group="gates", tags=["ci"], depends_on=["gate-off-by-default"], covers=["gate.enforceable"])
def test_fail_on_unknown_covers(pytester, run):
    """A typo in covers= can fail the run instead of only warning."""
    with given("a test covering a requirement id that does not exist"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_g='''
            from specdeck import scenario

            @scenario("t", covers=["pricing.taxes"])
            def test_a(): pass
        ''')
    with when("pytest runs with --specdeck-fail-on-unknown-covers"):
        result, _, _ = run("--specdeck-fail-on-unknown-covers")
    with then("the run fails and names the typo", expected="exit 1, names pricing.taxes"):
        assert result.ret == 1
        assert "pricing.taxes" in result.stdout.str()


@scenario("gate-via-ini", group="gates", tags=["ci"], depends_on=["gate-unmet"], covers=["gate.optional", "gate.enforceable"])
def test_gate_can_be_set_in_config(pytester, run):
    """Gates can live in the project config instead of the command line."""
    with given("specdeck_fail_on_unmet = true in the ini file"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makeini("[pytest]\nspecdeck_fail_on_unmet = true\n")
        pytester.makepyfile(test_g="def test_a(): pass")
    with when("pytest runs with no flags"):
        result, _, _ = run()
    with then("the run fails", expected="exit 1"):
        assert result.ret == 1


@scenario("gate-invalid-spec", group="gates", tags=["ci"], depends_on=["gate-off-by-default"], covers=["gate.enforceable", "spec.valid"])
def test_fail_on_invalid_spec(pytester, run):
    """The generated spec is validated in-process when the gate asks for it."""
    with given("an ordinary passing suite"):
        pytester.makepyfile(test_g="def test_a(): pass")
    with when("pytest runs with --specdeck-fail-on-invalid"):
        result, spec, _ = run("--specdeck-fail-on-invalid")
    with then("the generated spec is valid, so the run passes", expected="exit 0"):
        assert result.ret == 0
        assert spec is not None


# --------------------------------------------------------------------------- #
# The two bugs
# --------------------------------------------------------------------------- #
@scenario("bug-alias-after-dedup", group="gates", tags=["regression"], depends_on=["gate-invalid-spec"], covers=["deps.resolvable"])
def test_parametrized_alias_survives_an_id_collision(pytester, run):
    """An alias registered before de-duplication pointed at an id that never existed.

    A parametrized test with an explicit id whose case id collides with another
    scenario used to emit a depends_on referencing the pre-rename id — a dangling
    reference the validator calls an error.
    """
    with given("a scenario whose id collides with a parametrized case id"):
        pytester.makepyfile(test_g='''
            import pytest
            from specdeck import scenario

            @scenario("t-1")
            def test_collides(): pass

            @scenario("t")
            @pytest.mark.parametrize("n", [1, 2])
            def test_cases(n): pass

            @scenario("dependent", depends_on=["t"])
            def test_dep(): pass
        ''')
    with when("the spec is generated"):
        result, spec, _ = run()
    with then("every depends_on resolves to a real scenario", expected="no dangling reference"):
        ids = {s["id"] for s in spec["scenarios"]}
        for s in spec["scenarios"]:
            for dep in s.get("depends_on", []):
                assert dep in ids, f"{s['id']} depends on missing {dep}"
    with and_("the spec validates clean"):
        from specdeck.validation import is_valid, validate_file

        assert is_valid(validate_file(pytester.path / ".specdeck" / "spec.yaml"))


@scenario("bug-requirements-without-terminal", group="gates", tags=["regression"],
          depends_on=["gate-invalid-spec"], covers=["req.verification-status"])
def test_requirements_report_is_written_without_a_terminal_reporter(pytester, run):
    """The report used to live inside `if terminalreporter`, so -p no:terminal dropped it."""
    with given("a suite with requirements and no terminal reporter"):
        pytester.makefile(".yaml", requirements=REQS)
        pytester.makepyfile(test_g='''
            from specdeck import scenario

            @scenario("t", covers=["pricing.tax"])
            def test_a(): pass
        ''')
    with when("pytest runs with -p no:terminal"):
        run("-p", "no:terminal")
    with then("the requirements report is still on disk", expected=".specdeck/requirements.json exists"):
        import json

        p = pytester.path / ".specdeck" / "requirements.json"
        assert p.exists()
        assert json.loads(p.read_text())["counts"]["met"] == 1
