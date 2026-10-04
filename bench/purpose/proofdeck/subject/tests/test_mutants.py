"""Generating and selecting mutants: one per line, most useful first, capped per file."""
from specdeck import and_, given, scenario, then, when

from proofdeck.model import Mutant
from proofdeck.mutate import mutants_for_line, select

from .conftest import src

BOUNDARY = src("""
    def bulk_discount(qty):
        if qty > 100:
            return 15
        return 0
""")


@scenario("mutants-boundary-first", tags=["mutation"], depends_on=["arid-logic-lines-kept"])
def test_comparison_boundary_is_tried_first():
    """On a comparison, the first mutant shifts the boundary — where off-by-one bugs live."""
    with given("the line `if qty > 100:`"):
        line = 2
    with when("candidate mutants are generated"):
        mutants = mutants_for_line(BOUNDARY, line)
    with then("the first candidate turns > into >=", expected="if qty >= 100:"):
        assert mutants[0].mutated.strip() == "if qty >= 100:"
    with and_("every candidate is on the requested line and differs from the original"):
        assert all(m.line == line and m.mutated != m.original for m in mutants)


@scenario("mutants-cover-operator-families", tags=["mutation"], depends_on=["mutants-boundary-first"])
def test_arithmetic_boolean_and_constants_are_mutated():
    """Arithmetic, boolean connectives and constants each produce candidates."""
    code = src("""
        def f(a, b, flag):
            total = a + b * 2
            ok = flag and a > 0
            return total if ok else 0
    """)
    with when("candidates are generated for the arithmetic and boolean lines"):
        arith = {m.operator for m in mutants_for_line(code, 2)}
        boolean = {m.operator for m in mutants_for_line(code, 3)}
    with then("the arithmetic line gets arithmetic-operator mutants", expected="'arithmetic' in operators"):
        assert "arithmetic" in arith
    with and_("the boolean line gets boolean-connective mutants", expected="'boolean' in operators"):
        assert "boolean" in boolean


@scenario("mutants-none-on-arid-lines", tags=["mutation", "noise"], depends_on=["test_noise_lines_are_arid"])
def test_arid_lines_produce_no_mutants():
    """An arid line yields no candidates at all."""
    code = src("""
        import logging
        def f(x):
            logging.info("x=%s", x)
            return x + 1
    """)
    with then("the logging line has no mutants", expected="[]"):
        assert mutants_for_line(code, 3) == []


@scenario("mutants-one-per-line-capped", tags=["mutation", "noise"], depends_on=["mutants-boundary-first"])
def test_selection_takes_one_per_line_and_caps_each_file():
    """At most one mutant per line and seven per file — Google cut 820 mutants per change to 7 this way."""
    with given("ten changed lines in one file with three candidates each, plus two lines in another file"):
        def cands(path, line):
            return [Mutant(path, line, op, "x", f"x{op}") for op in ("boundary", "arithmetic", "constant")]
        candidates = {
            "a.py": {line: cands("a.py", line) for line in range(1, 11)},
            "b.py": {line: cands("b.py", line) for line in (1, 2)},
        }
    with when("mutants are selected with the default cap"):
        chosen = select(candidates)
    with then("no line gets more than one mutant", expected="one per (path, line)"):
        keys = [(m.path, m.line) for m in chosen]
        assert len(keys) == len(set(keys))
    with and_("a.py is capped at seven", expected="7 mutants for a.py"):
        assert sum(m.path == "a.py" for m in chosen) == 7
    with and_("b.py keeps both of its lines, each with its first (most productive) candidate"):
        b = sorted((m for m in chosen if m.path == "b.py"), key=lambda m: m.line)
        assert [(m.line, m.operator) for m in b] == [(1, "boundary"), (2, "boundary")]
