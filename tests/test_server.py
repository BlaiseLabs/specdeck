"""The viewer server: one local server, a page per project, safe writes, runs started from the browser."""
import http.client
import json
import socket
import time
from urllib.parse import urlparse

from specdeck import and_, given, scenario, then, when

from .conftest import _NO_PROXY, DAG_SUITE, http_json


def _wait(pred, timeout=30.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out waiting")


@scenario("server-serves-projects", tags=["server"], depends_on=["history-records-a-run"])
def test_every_recorded_project_gets_a_page(pytester, record, viewer):
    """Every project that recorded a run appears in the viewer, with its own page and board."""
    base, app = viewer
    with given("a project that recorded a run"):
        pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
    with when("the viewer is asked for its projects and that project's board"):
        _, projects = http_json(f"{base}/api/projects")
        slug = projects["projects"][0]["slug"]
        status, board = http_json(f"{base}/api/p/{slug}/board")
        page = _NO_PROXY.open(f"{base}/p/{slug}", timeout=5).read().decode()
    with then("the project is listed with its counts", expected="1 passed of 4"):
        assert projects["projects"][0]["counts"]["passed"] == 1
        assert projects["projects"][0]["counts"]["total"] == 4
    with and_("its page is served with this server's write token, and the board has the analysis"):
        assert status == 200 and app.token in page
        assert board["analysis"]["frontier"] == ["parse-amount"]


@scenario("server-rejects-foreign-hosts-and-tokenless-writes", tags=["server", "security"])
def test_writes_need_the_token_and_a_loopback_host(viewer):
    """Requests with a non-loopback Host are refused, and writes need the page's token."""
    base, app = viewer
    u = urlparse(base)
    with when("a request arrives with Host: evil.example (what DNS rebinding looks like)"):
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        c.request("GET", "/api/projects", headers={"Host": "evil.example"})
        foreign = c.getresponse().status
    with then("it is refused", expected="403"):
        assert foreign == 403
    with and_("a write without the token is refused, even from localhost", expected="403"):
        status, body = http_json(f"{base}/api/p/x/run", {"scope": "all"})
        assert status == 403 and "token" in body["error"]
    with and_("the server can't be reached on the machine's network address, only on loopback"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outbound interface
            lan_ip = probe.getsockname()[0]
        except OSError:
            lan_ip = None
        finally:
            probe.close()
        if lan_ip and not lan_ip.startswith("127."):
            s = socket.socket()
            s.settimeout(2)
            assert s.connect_ex((lan_ip, app.port)) != 0
            s.close()


@scenario("server-edits-a-test", tags=["server", "editing"], depends_on=["server-serves-projects", "edit-step-text"])
def test_editing_through_the_viewer_rewrites_the_test_file(pytester, record, viewer):
    """The viewer's editor previews a diff, then writes the change into the test file."""
    base, app = viewer
    with given("a recorded project and the editor model for one scenario"):
        path = pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
        slug = http_json(f"{base}/api/projects")[1]["projects"][0]["slug"]
        _, model = http_json(f"{base}/api/p/{slug}/source?id=order-total")
    with when("a step is changed, previewed, then saved"):
        body = {"id": "order-total", "fingerprint": model["fingerprint"],
                "edits": {"steps": [{"index": 0, "text": "the amounts 1 and 2 are added"}]}}
        _, preview = http_json(f"{base}/api/p/{slug}/edit", {**body, "dry_run": True}, token=app.token)
        unchanged = path.read_text()
        _, saved = http_json(f"{base}/api/p/{slug}/edit", body, token=app.token)
    with then("the preview is a diff and writes nothing", expected='+    with when("the amounts 1 and 2 are added"):'):
        assert '+    with when("the amounts 1 and 2 are added"):' in preview["diff"]
        assert "two amounts are added" in unchanged
    with and_("saving writes the file and returns the fresh model"):
        assert saved["written"] and 'with when("the amounts 1 and 2 are added"):' in path.read_text()
        assert saved["model"]["steps"][0]["text"] == "the amounts 1 and 2 are added"


@scenario("server-runs-tests-and-streams", tags=["server", "live"], depends_on=["server-serves-projects"])
def test_a_run_started_from_the_viewer_streams_back_live(pytester, record, viewer):
    """'Run failing' starts pytest in the project; the stream announces changes and the board updates."""
    base, app = viewer
    with given("a recorded project whose bug has since been fixed on disk"):
        path = pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
        path.write_text(path.read_text().replace("int(text) + 1  # BUG: off by one", "int(text)"))
        slug = http_json(f"{base}/api/projects")[1]["projects"][0]["slug"]
        u = urlparse(base)
        stream = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
        stream.request("GET", f"/api/p/{slug}/stream")
        events = stream.getresponse()
    with when("'Run failing' is pressed"):
        status, started = http_json(f"{base}/api/p/{slug}/run", {"scope": "failing"}, token=app.token)
    with then("pytest is started for the failing and blocked scenarios only", expected="3 node ids"):
        assert status == 200, started
        assert sum("test_pricing.py::" in a for a in started["cmd"]) == 3
    with and_("the stream announces a change"):
        line = b""
        while b"event: change" not in line:
            line = events.fp.readline()
        stream.close()
    with and_("when the run finishes, everything passes and the run is a partial one from the viewer",
              expected="4 of 4 passing"):
        board = _wait(lambda: (lambda b: b if not b["active_run"] and b["last_run"]["id"] == 2 else None)(
            http_json(f"{base}/api/p/{slug}/board")[1]), timeout=60)
        assert board["counts"]["passed"] == 4
        run = http_json(f"{base}/api/p/{slug}/runs")[1]["runs"][0]
        assert run["trigger"] == "ui" and run["full"] == 0
        assert sorted(run["fixed"]) == ["invoice", "order-total", "parse-amount"]
