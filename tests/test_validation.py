"""specdeck's own spec validator — no outside project needed to know a spec is valid."""
import textwrap

import yaml

from specdeck import and_, given, scenario, then, when
from specdeck.validation import is_valid, validate_requirements_file, validate_text

GOOD = textwrap.dedent("""
    feature: Example
    groups:
    - {id: core, name: Core}
    scenarios:
    - id: a
      name: First
      group: core
      tags: [core]
      steps:
      - {keyword: Given, text: something}
      - keyword: Then
        text: it holds
        assertion: {expected_label: yes, observed_label: yes}
      logic: ['pytest tests/test_x.py::test_a']
    - id: b
      name: Second
      group: core
      depends_on: [a]
      steps:
      - {keyword: When, text: it runs}
""")


def codes(text):
    return {d.code for d in validate_text(text)}


@scenario("validate-clean", group="validation", tags=["validator"], covers=["spec.valid"])
def test_a_well_formed_spec_has_no_diagnostics():
    """A correct spec produces nothing at all — not even warnings."""
    with given("a spec with groups, ids, steps, an assertion and a resolvable dependency"):
        pass
    with when("it is validated"):
        diags = validate_text(GOOD)
    with then("there are no diagnostics", expected="[]"):
        assert diags == [], [d.to_dict() for d in diags]
    with and_("it is valid"):
        assert is_valid(diags)


@scenario("validate-dangling-dep", group="validation", tags=["validator"], depends_on=["validate-clean"], covers=["deps.resolvable", "spec.valid"])
def test_dangling_dependency_is_an_error():
    """A depends_on pointing at no scenario is an error — the bug the plugin must never emit."""
    with given("a spec whose scenario depends on an id that does not exist"):
        bad = GOOD.replace("depends_on: [a]", "depends_on: [nope]")
    with when("it is validated"):
        diags = validate_text(bad)
    with then("BAD_REF is reported as an error", expected="BAD_REF"):
        assert any(d.code == "BAD_REF" and d.severity == "error" for d in diags)
    with and_("the spec is not valid"):
        assert not is_valid(diags)


@scenario("validate-cycle", group="validation", tags=["validator"], depends_on=["validate-clean"], covers=["spec.valid"])
def test_dependency_cycle_is_an_error():
    """Mutually dependent scenarios are reported once, not endlessly."""
    with given("two scenarios that depend on each other"):
        bad = GOOD + textwrap.dedent("""
            - id: c
              name: Third
              group: core
              depends_on: [d]
              steps: [{keyword: Given, text: x}]
            - id: d
              name: Fourth
              group: core
              depends_on: [c]
              steps: [{keyword: Given, text: y}]
        """)
    with when("it is validated"):
        diags = [d for d in validate_text(bad) if d.code == "CYCLE"]
    with then("exactly one cycle error is reported", expected="1 CYCLE error"):
        assert len(diags) == 1 and diags[0].severity == "error"


@scenario("validate-structure", group="validation", tags=["validator"], depends_on=["validate-clean"], covers=["spec.valid"])
def test_structural_problems_are_caught():
    """Missing feature, bad keywords, duplicate ids and unknown keys are all reported."""
    with given("a spec with several structural faults"):
        bad = textwrap.dedent("""
            scenarios:
            - id: a
              name: One
              nonsense: 1
              steps:
              - {keyword: Sideways, text: hm}
            - id: a
              name: Two
              steps: []
        """)
    with when("it is validated"):
        found = codes(bad)
    with then("each fault has its own code", expected="NO_FEATURE, BAD_KEYWORD, DUP_ID, UNKNOWN_KEY"):
        assert {"NO_FEATURE", "BAD_KEYWORD", "DUP_ID", "UNKNOWN_KEY"} <= found


@scenario("validate-unparseable", group="validation", tags=["validator"], depends_on=["validate-clean"], covers=["spec.safe-yaml"])
def test_unparseable_yaml_reports_a_line():
    """The colon-space foot-gun is reported as YAML_PARSE with a line number."""
    with given("a spec with unquoted `key: value` text inside a scalar"):
        bad = "feature: X\nscenarios:\n- name: a: b\n"
    with when("it is validated"):
        diags = validate_text(bad)
    with then("YAML_PARSE is the single error and it carries a line", expected="YAML_PARSE with line"):
        assert len(diags) == 1 and diags[0].code == "YAML_PARSE"
        assert diags[0].line is not None


@scenario("validate-requirements-file", group="validation", tags=["validator"], depends_on=["validate-clean"])
def test_requirements_file_problems_surface_as_diagnostics(tmp_path):
    """Problems in requirements.yaml come back in the same shape as spec problems."""
    with given("a requirements file with a duplicate id"):
        p = tmp_path / "requirements.yaml"
        p.write_text(textwrap.dedent("""
            requirements:
            - id: one
              says: A thing is true.
            - id: one
              says: Said twice.
        """))
    with when("it is validated"):
        diags = validate_requirements_file(p)
    with then("the duplicate is reported", expected="a REQUIREMENT diagnostic"):
        assert any(d.code == "REQUIREMENT" for d in diags)


@scenario("validate-missing-file", group="validation", tags=["validator"], depends_on=["validate-clean"])
def test_missing_requirements_file_is_an_error(tmp_path):
    """Asking to validate a file that isn't there is an error, not a crash."""
    with when("a non-existent requirements file is validated"):
        diags = validate_requirements_file(tmp_path / "nope.yaml")
    with then("NO_FILE is reported", expected="NO_FILE"):
        assert [d.code for d in diags] == ["NO_FILE"]


@scenario("validate-unreadable-requirements", group="validation", tags=["validator"], depends_on=["validate-requirements-file"])
def test_a_requirements_file_that_does_not_parse_is_an_error(tmp_path):
    """Bad indentation turned a 9-entry file into 'no requirements'; that must fail validation, not warn."""
    with given("a requirements file whose second entry is indented differently"):
        p = tmp_path / "requirements.yaml"
        p.write_text("requirements:\n- id: one\n  says: A thing.\n  - id: two\n    says: Another.\n")
    with when("it is validated"):
        diags = validate_requirements_file(p)
    with then("an error names the file as unreadable", expected="REQUIREMENTS_UNREADABLE"):
        assert [d.code for d in diags if d.severity == "error"] == ["REQUIREMENTS_UNREADABLE"]
        assert not is_valid(diags)


@scenario("validate-requirement-quality", group="validation", tags=["requirements", "standard"],
          covers=["req.quality"])
def test_requirement_quality_is_checked_against_29148(tmp_path):
    """`specdeck validate` checks each requirement and the set against ISO/IEC/IEEE 29148:2018:
    the construct of §5.2.4, the characteristics of §5.2.5 and §5.2.6 a tool can check, and the
    language criteria of §5.2.7. Each finding cites its clause."""
    from specdeck.validation import validate_requirements_file

    with given("requirements with typical weaknesses, and a spec in which one test traces to none of them"):
        reqs = tmp_path / "requirements.yaml"
        reqs.write_text("""\
requirements:
  - id: good
    statement: The order shall get 15% off at 100 units or more.
    rationale: Agreed with sales.
  - id: vague
    statement: The checkout shall be fast and user-friendly if possible.
    rationale: Users complain.
  - id: plain
    statement: Totals are rounded half up.
  - id: double
    statement: The receipt shall show the total; it shall show the discount.
    rationale: Audit.
  - id: unverified
    statement: The export shall be TBD.
    rationale: Finance asked.
  - id: inspected
    statement: The receipt shall carry the company logo.
    rationale: Brand rules.
    verification: inspection
""")
        spec = tmp_path / "spec.yaml"
        spec.write_text(yaml.safe_dump({"feature": "f", "scenarios": [
            {"id": "a", "name": "A", "tags": ["req:good", "req:vague", "req:plain", "req:double"], "logic": ["pytest t.py::a"]},
            {"id": "b", "name": "B", "tags": ["req:ghost"], "logic": ["pytest t.py::b"]},
            {"id": "c", "name": "C", "logic": ["pytest t.py::c"]}]}))
    with when("it is validated with the spec"):
        diags = validate_requirements_file(reqs, spec=spec)
        by = lambda code: {d.scenario for d in diags if d.code == code}  # noqa: E731
    with then("the well-formed requirement passes untouched", expected="no findings for 'good'"):
        assert not [d for d in diags if d.scenario == "good"]
    with and_("§5.2.7: vague terms are named, with their category", expected="fast, user-friendly, if possible"):
        lang = " ".join(d.message for d in diags if d.code == "LANGUAGE" and d.scenario == "vague")
        assert '"fast"' in lang and '"user-friendly"' in lang and '"if possible"' in lang and "§5.2.7" in lang
    with and_("§5.2.4: a statement without 'shall' is flagged; §5.2.5: so is a missing rationale and a double requirement"):
        assert "plain" in by("NOT_SHALL") and "plain" in by("NO_RATIONALE") and "double" in by("NOT_SINGULAR")
    with and_("§5.2.5/§5.2.6: unverified and TBD requirements, untraced tests and unknown links are reported"):
        assert by("NOT_VERIFIED") == {"unverified"}           # 'inspected' declares a non-test method
        assert "unverified" in by("TBX") and by("UNTRACED_TEST") == {"c"} and by("UNKNOWN_REQUIREMENT") == {"ghost"}
    with and_("every finding cites the clause it comes from"):
        assert all(d.clause and d.clause.startswith("§") for d in diags if d.code != "REQUIREMENT")
