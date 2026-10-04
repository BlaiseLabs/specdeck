"""Arid lines: where a mutant would only produce noise (after Google's arid nodes)."""
import pytest
from specdeck import and_, given, scenario, then, when

from proofdeck.arid import is_arid

from .conftest import src

SAMPLE = src('''
    import logging
    import time

    log = logging.getLogger(__name__)


    def total(items, fee=5):
        """Sum item prices plus a fee."""
        log.info("totalling %d items", len(items))
        print("debug", items)
        time.sleep(0.1)
        value: int
        value = sum(items) + fee
        return value


    if __name__ == "__main__":
        total([1, 2])
''')


def _line(needle):
    for i, text in enumerate(SAMPLE.splitlines(), start=1):
        if needle in text:
            return i
    raise AssertionError(needle)


@scenario(tags=["noise"])
@pytest.mark.parametrize(
    "needle",
    ["import logging", "log.info", "print(", "time.sleep", '"""Sum item', 'if __name__ == "__main__"', "value: int"],
)
def test_noise_lines_are_arid(needle):
    """Logging, print, sleeps, imports, docstrings, main guards and type-only lines are not mutated."""
    with given(f"a source line containing {needle!r}"):
        line = _line(needle)
    with then("it is classified as arid", expected="is_arid == True"):
        assert is_arid(SAMPLE, line) is True


@scenario("arid-logic-lines-kept", tags=["noise"])
def test_logic_lines_are_not_arid():
    """Lines that compute results stay mutation targets."""
    with given("the line that computes the total"):
        line = _line("value = sum(items) + fee")
    with then("it is not arid", expected="is_arid == False"):
        assert is_arid(SAMPLE, line) is False
    with and_("the return line is not arid either"):
        assert is_arid(SAMPLE, _line("return value")) is False


@scenario("arid-default-arguments-skipped", tags=["noise"])
def test_default_argument_values_are_arid():
    """Default argument values are skipped — the most-rejected Python mutant at Google."""
    with given("a function signature whose only mutable token is a default value"):
        line = _line("def total(items, fee=5)")
    with then("the signature line is arid", expected="is_arid == True"):
        assert is_arid(SAMPLE, line) is True
