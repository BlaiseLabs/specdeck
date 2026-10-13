"""The shop's rules, checked on its source. Each test is one rule in requirements.yaml."""
import ast
from pathlib import Path

from specdeck import scenario

SRC = Path(__file__).resolve().parents[1] / "src" / "shop"


def calls(name, skip=()):
    """(file:line, code) for each call to `name` in the shop package."""
    for path in sorted(SRC.glob("*.py")):
        if path.name in skip:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == name:
                yield f"{path.name}:{node.lineno}", ast.unparse(node)


@scenario("rule-decimal-money", covers=["rules.decimal-money"])
def test_money_never_touches_float():
    floats = [f"{p.name}:{n.lineno}" for p in SRC.glob("*.py") for n in ast.walk(ast.parse(p.read_text()))
              if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    assert list(calls("float")) == [] and floats == []


@scenario("rule-utc-clock", covers=["rules.utc-clock"])
def test_time_comes_from_the_clock():
    direct = [c for c in calls("now", skip={"clock.py"}) if not c[1].startswith(("clock.now", "now("))]
    assert direct + list(calls("utcnow")) + list(calls("today")) == []


@scenario("rule-i18n", covers=["rules.i18n"])
def test_every_message_comes_from_i18n():
    from shop.i18n import MESSAGES

    raw = [c for c in calls("print") if not c[1].startswith("print(t(")]
    missing = [(lang, k) for lang in MESSAGES for k in MESSAGES["en"] if k not in MESSAGES[lang]]
    assert raw == [] and missing == []
