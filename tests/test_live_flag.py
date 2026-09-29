"""--specdeck-live: record the run and make sure a viewer is up, reusing one that already runs."""
import os
import signal
import socket

import pytest
from specdeck import and_, given, scenario, then, when

from .conftest import PLUGIN_ARGS, http_json


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def port(sdhome):
    p = _free_port()
    yield p
    status, info = http_json(f"http://127.0.0.1:{p}/api/health")
    if status == 200:  # stop the viewer the test started
        os.kill(info["pid"], signal.SIGTERM)


@scenario("live-flag-starts-viewer", tags=["live"], depends_on=["history-records-a-run"])
def test_live_flag_starts_a_viewer_and_prints_the_projects_page(pytester, port):
    """With no viewer running, --specdeck-live starts one in the background and prints the project's URL."""
    with given("no viewer on the port"):
        pytester.makepyfile(test_a="def test_a():\n    assert True\n")
        assert http_json(f"http://127.0.0.1:{port}/api/health")[0] != 200
    with when("the suite runs with --specdeck-live"):
        result = pytester.runpytest(*PLUGIN_ARGS, "--specdeck-live", "--specdeck-port", str(port))
    with then("the viewer answers and the run printed its page", expected=f"http://127.0.0.1:{port}/p/..."):
        assert http_json(f"http://127.0.0.1:{port}/api/health")[0] == 200
        result.stdout.fnmatch_lines([f"specdeck live: http://127.0.0.1:{port}/p/*"])


@scenario("live-flag-adds-a-route", tags=["live"], depends_on=["live-flag-starts-viewer"])
def test_a_second_project_joins_the_running_viewer(pytester, port, tmp_path):
    """A second project's live run joins the viewer that's already running, under its own page."""
    with given("a viewer already started by one project"):
        pytester.makepyfile(test_a="def test_a():\n    assert True\n")
        pytester.runpytest(*PLUGIN_ARGS, "--specdeck-live", "--specdeck-port", str(port))
        pid = http_json(f"http://127.0.0.1:{port}/api/health")[1]["pid"]
    with when("another project runs with --specdeck-live and specdeck_live set in its config"):
        other = tmp_path / "other"
        other.mkdir()
        (other / "pyproject.toml").write_text('[tool.pytest.ini_options]\nspecdeck_feature = "other project"\n'
                                              'specdeck_live = "true"\n')
        (other / "test_b.py").write_text("def test_b():\n    assert True\n")
        pytester.chdir()
        result = pytester.runpytest(*PLUGIN_ARGS, "--specdeck-port", str(port), "--rootdir", str(other), str(other))
    with then("the same viewer now lists both projects", expected="2 projects, same pid"):
        _, projects = http_json(f"http://127.0.0.1:{port}/api/projects")
        assert len(projects["projects"]) == 2
        assert http_json(f"http://127.0.0.1:{port}/api/health")[1]["pid"] == pid
    with and_("the second run printed its own page", expected="/p/other-project"):
        result.stdout.fnmatch_lines([f"specdeck live: http://127.0.0.1:{port}/p/other-project*"])


@scenario("live-flag-off", tags=["live"])
def test_no_flag_means_no_recording(pytester, sdhome):
    """Without the flag or the config setting, nothing is recorded."""
    with when("the suite runs normally"):
        pytester.makepyfile(test_a="def test_a():\n    assert True\n")
        pytester.runpytest(*PLUGIN_ARGS)
    with then("no history database is created", expected="absent"):
        assert not (sdhome / "history.db").exists()
