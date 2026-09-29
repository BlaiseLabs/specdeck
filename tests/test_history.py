"""Run history: every recorded run is kept, and the board shows the latest state of each scenario."""
import json

from specdeck import and_, given, scenario, then, when

from .conftest import DAG_SUITE


def _board(hist):
    project = hist.projects()[0]
    return hist.board(project["id"])


def _status(board):
    return {s["id"]: s["status"] for s in board["scenarios"]}


@scenario("history-records-a-run", tags=["history"])
def test_a_run_is_recorded_with_status_steps_and_source(pytester, record):
    """A recorded run keeps each scenario's outcome, its steps and where its test lives."""
    with given("a suite where a bug fails one scenario and blocks the two that depend on it"):
        pytester.makepyfile(test_pricing=DAG_SUITE)
    with when("the suite runs with --specdeck-record"):
        result, hist = record()
        board = _board(hist)
    with then("each scenario has its outcome, and dependents of the failure are 'blocked'",
              expected="parse failed, total and invoice blocked, greeting passed"):
        assert _status(board) == {"parse-amount": "failed", "order-total": "blocked",
                                  "invoice": "blocked", "greeting": "passed"}
    with and_("the failing step carries what was observed, and later steps are pending"):
        steps = {s["id"]: s for s in board["scenarios"]}["parse-amount"]["steps"]
        assert [st["state"] for st in steps] == ["ok", "ok", "failed"]
        assert "42" in steps[2]["assertion"]["observed_label"]
    with and_("the scenario knows its file and line, so the viewer can open and edit it"):
        s = {s["id"]: s for s in board["scenarios"]}["order-total"]
        assert s["file"] == "test_pricing.py" and s["function"] == "test_total" and s["line"] > 1
    with and_("the terminal says the run was recorded"):
        result.stdout.fnmatch_lines(["*specdeck: recorded run #1 for *"])


@scenario("history-partial-run-updates-only-its-tests", tags=["history"], depends_on=["history-records-a-run"])
def test_running_one_test_updates_it_without_forgetting_the_others(pytester, record):
    """Running a single test updates that scenario on the board and leaves the rest as they were."""
    with given("a full recorded run with one failure"):
        path = pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
    with when("the bug is fixed and only the parse test is re-run"):
        path.write_text(path.read_text().replace("int(text) + 1  # BUG: off by one", "int(text)"))
        result, hist = record(f"{path.name}::test_parse")
        board = _board(hist)
    with then("parse now passes, and the untouched scenarios keep their last result",
              expected="parse passed; total, invoice still blocked"):
        assert _status(board) == {"parse-amount": "passed", "order-total": "blocked",
                                  "invoice": "blocked", "greeting": "passed"}
    with and_("the run is marked partial, and the trend only counts complete runs"):
        runs = hist.runs(hist.projects()[0]["id"])
        assert [r["full"] for r in runs] == [0, 1]
        assert len(board["trend"]) == 1
    with and_("a partial run does not rewrite the spec file with a subset"):
        result.stdout.fnmatch_lines(["*partial run, spec not rewritten*"])
        spec = (pytester.path / ".specdeck" / "spec.yaml").read_text()
        assert "id: invoice" in spec


@scenario("history-full-run-drops-deleted-tests", tags=["history"], depends_on=["history-records-a-run"])
def test_a_deleted_test_leaves_the_board_after_the_next_full_run(pytester, record):
    """A test deleted from the suite disappears from the board once the whole suite runs again."""
    with given("a recorded suite with an independent greeting scenario"):
        path = pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
    with when("the greeting test is deleted and the full suite runs"):
        text = path.read_text()
        path.write_text(text[: text.index('@scenario("greeting")')])
        _, hist = record()
    with then("the board no longer lists it", expected="greeting absent"):
        assert "greeting" not in _status(_board(hist))


@scenario("history-deltas", tags=["history"], depends_on=["history-partial-run-updates-only-its-tests"])
def test_each_run_reports_what_started_and_stopped_passing(pytester, record):
    """Each run lists the scenarios that started passing and the ones that regressed."""
    with given("a recorded failing run"):
        path = pytester.makepyfile(test_pricing=DAG_SUITE)
        record()
    with when("the bug is fixed and the suite runs, then the bug comes back and it runs again"):
        good = path.read_text().replace("int(text) + 1  # BUG: off by one", "int(text)")
        path.write_text(good)
        record()
        path.write_text(good.replace("return int(text)", "return int(text) - 1"))
        _, hist = record()
        runs = {r["id"]: r for r in hist.runs(hist.projects()[0]["id"])}
    with then("the fixing run lists the three newly passing scenarios", expected="parse, total, invoice"):
        assert sorted(runs[2]["fixed"]) == ["invoice", "order-total", "parse-amount"]
    with and_("the breaking run lists them as regressions", expected="3 regressed"):
        assert sorted(runs[3]["regressed"]) == ["invoice", "order-total", "parse-amount"]


@scenario("history-dead-run-is-abandoned", tags=["history"])
def test_a_run_whose_process_died_is_marked_abandoned(sdhome):
    """A run whose pytest process died mid-run is marked abandoned instead of 'running' forever."""
    from specdeck.history import History

    with given("a run that was started by a process that no longer exists"):
        hist = History(sdhome / "history.db")
        p = hist.register_project(sdhome, "demo")
        run = hist.start_run(p["id"], [{"id": "a", "nodeid": "t.py::test_a", "meta": {"name": "A"}}], pid=999999)
    with when("the viewer reads the board"):
        board = hist.board(p["id"])
    with then("the run is abandoned and nothing shows as live", expected="abandoned"):
        assert hist.runs(p["id"])[0]["status"] == "abandoned"
        assert board["active_run"] is None
        assert board["scenarios"][0]["status"] == "notrun" and board["scenarios"][0]["live"] is None
    with and_("the run id is kept for the history view"):
        assert board["last_run"]["id"] == run


@scenario("history-live-run-overlays-last-state", tags=["history", "live"])
def test_a_run_in_progress_keeps_last_results_visible(sdhome):
    """While a run is going, scenarios not reached yet keep their last result, marked as queued."""
    from specdeck.history import History
    import os

    with given("a finished run where scenario a passed"):
        hist = History(sdhome / "history.db")
        p = hist.register_project(sdhome, "demo")
        rows = [{"id": "a", "nodeid": "t.py::test_a", "meta": {}}, {"id": "b", "nodeid": "t.py::test_b", "meta": {}}]
        r1 = hist.start_run(p["id"], rows)
        hist.set_result(p["id"], r1, "a", "passed")
        hist.set_result(p["id"], r1, "b", "passed")
        hist.finish_run(p["id"], r1)
    with when("a new run starts and is running b"):
        r2 = hist.start_run(p["id"], rows, pid=os.getpid())
        hist.set_running(p["id"], r2, "b")
        board = hist.board(p["id"])
        by = {s["id"]: s for s in board["scenarios"]}
    with then("a still shows passed but queued, and b shows running", expected="a queued, b running"):
        assert (by["a"]["status"], by["a"]["live"]) == ("passed", "queued")
        assert by["b"]["live"] == "running"
    with and_("the board reports the active run and its progress"):
        assert board["active_run"]["id"] == r2 and board["active_run"]["done"] == 0
