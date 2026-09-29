import json
from pathlib import Path

import pytest
import yaml

pytest_plugins = ["pytester"]

# The plugin registers itself under the entry-point name "specdeck". Inner pytester
# runs block that name and load the module explicitly, so the tests behave the
# same whether or not the package is pip-installed.
PLUGIN_ARGS = ("-p", "no:specdeck", "-p", "specdeck.plugin")


@pytest.fixture
def run(pytester):
    def _run(*args):
        result = pytester.runpytest(*PLUGIN_ARGS, *args)
        spec_path = pytester.path / ".specdeck" / "spec.yaml"
        needs_path = pytester.path / ".specdeck" / "needs.json"
        spec = yaml.safe_load(spec_path.read_text()) if spec_path.exists() else None
        needs = json.loads(needs_path.read_text()) if needs_path.exists() else None
        return result, spec, needs

    return _run


def scenario_by_id(spec: dict, sid: str) -> dict:
    for s in spec["scenarios"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"{sid} not in {[s['id'] for s in spec['scenarios']]}")


# --------------------------------------------------------------------------- #
# Viewer fixtures
# --------------------------------------------------------------------------- #
import threading
import urllib.request


@pytest.fixture
def sdhome(tmp_path, monkeypatch):
    """A private history database for this test (never the user's ~/.specdeck)."""
    home = tmp_path / "specdeck-home"
    monkeypatch.setenv("SPECDECK_HOME", str(home))
    monkeypatch.delenv("SPECDECK_PORT", raising=False)
    return home


@pytest.fixture
def record(pytester, sdhome):
    """Run the inner suite with --specdeck-record and return (result, History)."""
    from specdeck.history import History

    opened = []

    def _record(*args):
        result = pytester.runpytest(*PLUGIN_ARGS, "--specdeck-record", *args)
        hist = History(sdhome / "history.db")
        opened.append(hist)
        return result, hist

    yield _record
    for h in opened:
        h.close()


@pytest.fixture
def viewer(sdhome):
    """A live viewer server on a free port, in a background thread."""
    from specdeck.server import make_server

    server, app = make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{app.port}", app
    server.shutdown()
    server.server_close()


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, body=None, token=None, headers=None):
    """(status, json) for a GET, or a POST when body is given."""
    import json as _json
    import urllib.error

    data = None if body is None else _json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Specdeck-Token", token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _NO_PROXY.open(req, timeout=10) as r:
            return r.status, _json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, _json.loads(e.read() or b"{}")
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return 0, {}  # nothing listening


DAG_SUITE = '''
"""Pricing: the demo feature."""
from specdeck import scenario, given, when, then


def parse(text):
    return int(text) + 1  # BUG: off by one


@scenario("parse-amount")
def test_parse():
    """Amounts are parsed from text."""
    with given("the text '41'"):
        text = "41"
    with when("it is parsed"):
        value = parse(text)
    with then("the amount is 41", expected="41"):
        assert value == 41


@scenario("order-total", depends_on=["parse-amount"])
def test_total():
    """Order totals add parsed amounts."""
    with when("two amounts are added"):
        total = parse("1") + parse("2")
    with then("the total is 3", expected="3"):
        assert total == 3


@scenario("invoice", depends_on=["order-total"])
def test_invoice():
    """The invoice shows the total."""
    with then("the invoice line reads 'total: 3'"):
        assert f"total: {parse('1') + parse('2')}" == "total: 3"


@scenario("greeting")
def test_greeting():
    """An independent scenario that already passes."""
    with then("the greeting is friendly"):
        assert "hello".startswith("h")
'''
