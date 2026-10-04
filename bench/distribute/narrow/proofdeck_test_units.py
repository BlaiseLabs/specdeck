"""Direct unit tests for three proofdeck functions that the suite only checks end to end.

Added to experiment copies only (DIST_NARROW=1), to see whether a weak worker succeeds when
the failing test calls the buggy function directly. They are rigged: written for the three
planted bugs. Expected values come from the original code.
"""
import ast

from proofdeck._source import cached, is_docstring
from proofdeck.arid import is_arid
from proofdeck.model import Mutant
from proofdeck.schemata import switch_line

CODE = 'def total(items):\n    """Sum the items."""\n    x = "not a docstring"\n    "a bare string"\n    return sum(items)\n'


def test_is_docstring_is_true_for_the_first_string_of_a_function():
    src = cached(CODE)
    fn = src.tree.body[0]
    assert is_docstring(fn.body[0], src.parents()) is True


def test_is_docstring_is_false_for_a_later_bare_string():
    src = cached(CODE)
    fn = src.tree.body[0]
    assert is_docstring(fn.body[2], src.parents()) is False


def test_is_arid_for_a_logging_call():
    code = "import logging\n\ndef f(x):\n    logging.info('x=%s', x)\n    return x\n"
    assert is_arid(code, 4) is True


def test_switch_line_compiles_a_longer_operator_mutant_behind_its_switch():
    orig = "    if qty > 100:"
    src = cached("def f(qty):\n" + orig + "\n        return 15\n    return 5\n")
    m = Mutant(path="<string>", line=2, operator="boundary", original=orig, mutated="    if qty >= 100:")
    assert switch_line(src, m, 7) == "    if (qty >= 100 if _proofdeck_m(7) else qty > 100):"


def test_switch_line_compiles_a_shorter_operator_mutant_behind_its_switch():
    orig = "    if qty >= 100:"
    src = cached("def f(qty):\n" + orig + "\n        return 15\n    return 5\n")
    m = Mutant(path="<string>", line=2, operator="boundary", original=orig, mutated="    if qty > 100:")
    assert switch_line(src, m, 7) == "    if (qty > 100 if _proofdeck_m(7) else qty >= 100):"
