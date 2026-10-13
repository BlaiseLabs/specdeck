"""The generated software requirements specification, in the form of ISO/IEC/IEEE 29148:2018 §9.6."""
from specdeck import and_, given, scenario, then, when
from specdeck.requirements import load
from specdeck.srs import build


@scenario("srs-follows-29148", group="requirements", tags=["requirements", "standard", "srs"], covers=["req.srs"])
def test_the_srs_has_the_content_of_29148_section_9_6(tmp_path):
    """The SRS follows §9.6 in the standard's order, declares its tailoring (§4.5.2), groups the
    requirements by the sections of §9.6.11–9.6.18, marks unwritten sections TBS, and verifies
    each requirement in a traceability matrix (§9.6.19)."""
    with given("two typed requirements, one untyped, a purpose written by people, and a last run"):
        f = tmp_path / "requirements.yaml"
        f.write_text("""\
srs:
  title: Pricing service SRS
  purpose: Price orders for the shop.
requirements:
  - id: price.bulk
    statement: The order shall get 15% off at 100 units or more.
    rationale: Agreed with sales.
    type: functional
    priority: high
  - id: price.fast
    statement: A quote shall be returned within 200 ms.
    rationale: Checkout times out at 1 s.
    type: performance
    verification: [test, analysis]
  - id: price.legacy
    says: Totals are rounded half up.
""")
        reqs = load(f)
        report = {"requirements": [
            {"id": "price.bulk", "status": "met", "scenarios": [{"id": "bulk", "nodeid": "t.py::test_bulk"}]},
            {"id": "price.fast", "status": "failing", "scenarios": [{"id": "fast", "nodeid": "t.py::test_fast"}]}]}
        scenarios = [{"id": "bulk", "tags": ["req:price.bulk"]}, {"id": "fast", "tags": ["req:price.fast"]},
                     {"id": "stray", "tags": []}]
    with when("the SRS is built"):
        doc = build(reqs, report, scenarios, reqs.srs, tmp_path)
    with then("its sections are those of §9.6, in the standard's order", expected="purpose before functions before verification"):
        order = ["(§9.6.1)", "(§9.6.2)", "(§9.6.3)", "(§9.6.9)", "(§9.6.10)", "(§9.6.11)", "(§9.6.12)",
                 "(§9.6.14)", "(§9.6.16)", "(§9.6.18)", "(§9.6.19)", "(§9.6.20)"]
        assert [doc.index(c) for c in order] == sorted(doc.index(c) for c in order)
        assert doc.startswith("# Pricing service SRS") and "tailored conformance (§4.5.2)" in doc
    with and_("what people wrote is used; what they didn't is marked to be supplied, not hidden"):
        assert "Price orders for the shop." in doc and "### 1.3 Scope (§9.6.3)\n\n_To be supplied (TBS)._" in doc
    with and_("each requirement sits in its type's section with its §5.2.8 attributes and status"):
        functions = doc[doc.index("(§9.6.12)"):doc.index("(§9.6.13)")]
        assert "`price.bulk`" in functions and "**Priority:** high" in functions and "met — verified by `t.py::test_bulk`" in functions
        performance = doc[doc.index("(§9.6.14)"):doc.index("(§9.6.15)")]
        assert "`price.fast`" in performance and "**Verification method:** test, analysis" in performance
        assert "### 2.9 Requirements without a type" in doc and "`price.legacy`" in doc[doc.index("without a type"):]
    with and_("verification is a traceability matrix, with quality findings and the review checklist"):
        assert "| `price.fast` | test, analysis | `t.py::test_fast` | failing |" in doc
        assert '"stray" traces to no requirement' in doc and "**Feasible:**" in doc
