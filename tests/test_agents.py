"""The agent instructions file: the project's rules, from its requirements, with their status."""
from specdeck import and_, given, scenario, then, when
from specdeck.agents import BEGIN, END, write
from specdeck.requirements import load

REQUIREMENTS = """\
requirements:
  - id: arch.queue
    statement: Services shall exchange messages only through the queue.
    rationale: Decided 2026-03 so that each service can be deployed alone.
    type: design-constraint
  - id: std.wcag
    statement: Each page shall meet WCAG 2.2 level AA.
    rationale: Required by the contract.
    type: standards-compliance
    standard: WCAG 2.2
    verification: inspection
  - id: done.changelog
    statement: Each change shall add a line to CHANGELOG.md.
    rationale: Releases are written from the changelog.
    type: process
  - id: price.bulk
    statement: An order of 100 units or more shall get 15% off.
    rationale: Agreed with sales.
    type: functional
"""
REPORT = {"requirements": [
    {"id": "arch.queue", "status": "failing", "scenarios": [{"id": "q", "nodeid": "tests/test_arch.py::test_queue"}]},
    {"id": "done.changelog", "status": "met", "scenarios": [{"id": "c", "nodeid": "tests/test_meta.py::test_log"}]},
    {"id": "price.bulk", "status": "met", "scenarios": [{"id": "b", "nodeid": "tests/test_price.py::test_bulk"}]}]}


@scenario("agents-rules", group="agents", tags=["agents", "standard"], covers=["agents.rules"])
def test_the_rules_are_written_with_their_reasons_tests_and_status(tmp_path):
    """Design constraints (ISO/IEC/IEEE 29148:2018 §9.6.16), standards compliance (§9.6.17) and
    process requirements (§5.2.8.3) — the Definition of Done — become the agent's rules, each with
    its rationale, the tests that verify it, and whether it held in the last run."""
    with given("a requirements file with one rule of each kind and a functional requirement, and a last run"):
        f = tmp_path / "requirements.yaml"
        f.write_text(REQUIREMENTS)
    with when("the rules are written into a new AGENTS.md"):
        text = write(tmp_path / "AGENTS.md", load(f), REPORT)
    with then("each kind of rule has its section, citing the standard",
              expected="Design constraints (§9.6.16), Standards compliance (§9.6.17), Definition of Done (§5.2.8.3)"):
        assert text.index("### Design constraints (§9.6.16)") < text.index("### Standards compliance (§9.6.17)") \
            < text.index("### Definition of Done (§5.2.8.3)")
    with and_("a rule carries its statement, its rationale and the test that verifies it"):
        assert "**Services shall exchange messages only through the queue.** (`arch.queue`)" in text
        assert "Why: Decided 2026-03" in text and "`tests/test_arch.py::test_queue`" in text
    with and_("a rule broken in the last run says so, and is named again before finishing",
              expected="named first, at its rule, and again before finishing"):
        assert "**broken in the last run**" in text and "Still broken from the last run: `arch.queue`." in text
        assert text.index("**Broken in the last run:** `arch.queue`") < text.index("### Design constraints")
    with and_("a rule verified another way tells the agent to check it by hand", expected="inspection"):
        assert "Verified by inspection, not by a test: check it yourself" in text
    with and_("functional requirements are not rules", expected="price.bulk absent"):
        assert "price.bulk" not in text


@scenario("agents-in-place", group="agents", tags=["agents"], depends_on=["agents-rules"], covers=["agents.in-place"])
def test_only_the_generated_block_of_an_existing_file_is_replaced(tmp_path):
    """AGENTS.md is usually written by hand. The rules go in a marked block, and a later run
    replaces that block and nothing else."""
    with given("a hand-written AGENTS.md"):
        f = tmp_path / "requirements.yaml"
        f.write_text(REQUIREMENTS)
        out = tmp_path / "AGENTS.md"
        out.write_text("# Agents\n\nUse uv, not pip.\n")
    with when("the rules are written twice, with the hand-written text edited in between"):
        write(out, load(f), REPORT)
        out.write_text(out.read_text().replace("Use uv, not pip.", "Use uv, not pip. Run ruff.") + "\nFooter.\n")
        fixed = {"requirements": [dict(r, status="met") for r in REPORT["requirements"]]}
        text = write(out, load(f), fixed)
    with then("the hand-written text is kept, before and after the block", expected="both edits survive"):
        assert text.startswith("# Agents\n\nUse uv, not pip. Run ruff.\n") and text.rstrip().endswith("Footer.")
    with and_("there is one block, and the block reflects the second run", expected="one BEGIN, no broken rule"):
        assert text.count(BEGIN) == 1 and text.count(END) == 1
        assert "Broken in the last run" not in text and "holds in the last run" in text
