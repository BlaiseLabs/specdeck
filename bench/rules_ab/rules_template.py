"""The shop's rules, as checks on its source. Each check returns the places that break the rule."""
import ast
import sys
from pathlib import Path
{IMPORT}
SRC = Path(__file__).resolve().parents[1] / "src" / "shop"
IO_MODULES = {"sqlite3", "urllib", "http", "socket", "requests", "httpx", "aiohttp", "urllib3", "ftplib", "smtplib"}


def _modules(src):
    for path in sorted(Path(src).rglob("*.py")):
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node, a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            yield node, node.module.split(".")[0]


def _calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name):
                yield node


def check_stdlib_only(src=SRC):
    return [f"{p.name}:{n.lineno} imports {m}" for p, tree in _modules(src) for n, m in _imports(tree)
            if m not in sys.stdlib_module_names and m != "shop"]


def check_decimal_money(src=SRC):
    out = []
    for p, tree in _modules(src):
        out += [f"{p.name}:{n.lineno} float literal {n.value!r}" for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, float)]
        out += [f"{p.name}:{n.lineno} float()" for n in _calls(tree, "float")]
    return out


def check_gateway_io(src=SRC):
    return [f"{p.name}:{n.lineno} imports {m}" for p, tree in _modules(src) if p.name != "gateway.py"
            for n, m in _imports(tree) if m in IO_MODULES]


def check_i18n(src=SRC):
    out = []
    for p, tree in _modules(src):
        for n in _calls(tree, "print"):
            for a in n.args:
                if not (isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "t"):
                    out.append(f"{p.name}:{n.lineno} prints text that does not come from t()")
    i18n = Path(src) / "i18n.py"
    if i18n.exists():
        ns = {}
        try:
            exec(compile(i18n.read_text(encoding="utf-8"), str(i18n), "exec"), ns)
            msgs = ns.get("MESSAGES", {})
            for lang in msgs:
                missing = set(msgs.get("en", {})) - set(msgs[lang])
                out += [f"i18n.py: {lang} has no {k!r}" for k in sorted(missing)]
        except Exception as exc:  # noqa: BLE001
            out.append(f"i18n.py does not load: {exc!r}")
    return out


def check_no_print(src=SRC):
    out = []
    for p, tree in _modules(src):
        if p.name == "cli.py":
            continue
        out += [f"{p.name}:{n.lineno} print()" for n in _calls(tree, "print")]
        out += [f"{p.name}:{n.lineno} write to stdout" for n in _calls(tree, "write")
                if "stdout" in ast.unparse(n.func)]
    return out


def check_utc_clock(src=SRC):
    out = []
    for p, tree in _modules(src):
        if p.name == "clock.py":
            continue
        for name in ("now", "utcnow", "today", "time"):
            for n in _calls(tree, name):
                text = ast.unparse(n.func)
                if name == "time" and text != "time.time":
                    continue
                if name == "now" and (text == "now" or text.endswith("clock.now")):
                    continue                          # shop.clock.now(), however it was imported
                out.append(f"{p.name}:{n.lineno} {text}()")
    return out

{DEC_stdlib_only}
def test_the_shop_imports_only_the_standard_library():
    assert check_stdlib_only() == []

{DEC_decimal_money}
def test_money_never_touches_float():
    assert check_decimal_money() == []

{DEC_gateway_io}
def test_only_the_gateway_does_io():
    assert check_gateway_io() == []

{DEC_i18n}
def test_every_message_comes_from_i18n():
    assert check_i18n() == []

{DEC_no_print}
def test_only_the_cli_prints():
    assert check_no_print() == []

{DEC_utc_clock}
def test_time_comes_from_the_clock():
    assert check_utc_clock() == []
