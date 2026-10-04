"""Static detectors: expectations that can't fail, or can't disagree with the code."""
from specdeck import and_, given, scenario, then, when

from proofdeck import FindingKind
from proofdeck.detectors import recomputed_expectations, special_cased_inputs, vacuous_assertions

from .conftest import src

IMPL = src("""
    def price_after_discount(price, percent):
        return price - price * percent / 100
""")


@scenario("detect-recomputed-formula", tags=["detector", "oracle"])
def test_expected_value_recomputed_with_the_codes_formula():
    """(b) A test that recomputes the answer with the implementation's own formula is flagged."""
    with given("a test whose expected side repeats `price - price * percent / 100`"):
        test = src("""
            def test_discount():
                price, percent = 100, 10
                assert price_after_discount(price, percent) == price - price * percent / 100
        """)
    with when("recomputed expectations are detected"):
        found = recomputed_expectations(test, IMPL)
    with then("the assertion is flagged as recomputed", expected=FindingKind.RECOMPUTED_EXPECTATION.value):
        assert [f.kind for f in found] == [FindingKind.RECOMPUTED_EXPECTATION]
    with and_("the finding points at the assertion line", expected="line 3"):
        assert found[0].line == 3


@scenario("detect-self-comparison", tags=["detector", "oracle"])
def test_expected_value_from_calling_the_code_itself():
    """(b) Comparing a function's result with another call to the same function is flagged."""
    with given("tests using the function under test as its own oracle"):
        test = src("""
            def test_a():
                assert price_after_discount(100, 10) == price_after_discount(100, 10)

            def test_b():
                expected = price_after_discount(100, 10)
                assert price_after_discount(100, 10) == expected
        """)
    with when("recomputed expectations are detected"):
        found = recomputed_expectations(test, IMPL)
    with then("both assertions are flagged", expected="lines 2 and 6"):
        assert sorted(f.line for f in found) == [2, 6]


@scenario("detect-independent-expectation-ok", tags=["detector", "oracle"])
def test_independent_literal_is_not_flagged_as_recomputed():
    """A literal expected value written by hand is not a recomputation."""
    with given("a test comparing against the literal 90.0"):
        test = src("""
            def test_discount():
                assert price_after_discount(100, 10) == 90.0
        """)
    with then("nothing is flagged", expected="[]"):
        assert recomputed_expectations(test, IMPL) == []


@scenario("detect-special-cased-input", tags=["detector", "cheating"])
def test_code_branching_on_the_exact_test_input():
    """(c) An equality branch on exactly the test's arguments that returns a constant is flagged."""
    with given("code hard-wired to the single case the test checks"):
        impl = src("""
            def price_after_discount(price, percent):
                if price == 100 and percent == 10:
                    return 90
                return price
        """)
        test = src("""
            def test_discount():
                assert price_after_discount(100, 10) == 90
        """)
    with when("special-cased inputs are detected"):
        found = special_cased_inputs(test, impl)
    with then("the branch is flagged", expected=FindingKind.SPECIAL_CASED_INPUT.value):
        assert [f.kind for f in found] == [FindingKind.SPECIAL_CASED_INPUT]
    with and_("the finding points at the branch in the implementation", expected="line 2"):
        assert found[0].line == 2


@scenario("detect-real-boundary-not-special-casing", tags=["detector", "cheating"], depends_on=["detect-special-cased-input"])
def test_genuine_threshold_is_not_special_casing():
    """A real threshold (`qty >= 100`) tested at its boundary is legitimate, not special-casing."""
    with given("a genuine tier boundary and a test that exercises it"):
        impl = src("""
            def bulk_discount(qty):
                if qty >= 100:
                    return 15
                return 0
        """)
        test = src("""
            def test_tier():
                assert bulk_discount(100) == 15
        """)
    with then("nothing is flagged", expected="[]"):
        assert special_cased_inputs(test, impl) == []


@scenario("detect-vacuous-assertions", tags=["detector", "oracle"])
def test_assertions_that_cannot_fail_for_a_wrong_result():
    """`assert True`, `is not None` and bare isinstance checks cannot catch a wrong answer."""
    with given("a test made only of assertions that pass for any numeric result"):
        test = src("""
            def test_runs():
                result = price_after_discount(100, 10)
                assert result is not None
                assert isinstance(result, float)
                assert True
        """)
    with when("vacuous assertions are detected"):
        found = vacuous_assertions(test)
    with then("all three are flagged", expected="lines 3, 4, 5"):
        assert sorted(f.line for f in found) == [3, 4, 5]
        assert {f.kind for f in found} == {FindingKind.VACUOUS_ASSERTION}


@scenario("detect-real-assertion-ok", tags=["detector", "oracle"], depends_on=["detect-vacuous-assertions"])
def test_value_assertion_is_not_vacuous():
    """A comparison against a concrete expected value is a real assertion."""
    with given("a test asserting the exact result"):
        test = src("""
            def test_value():
                assert price_after_discount(100, 10) == 90.0
        """)
    with then("nothing is flagged", expected="[]"):
        assert vacuous_assertions(test) == []
