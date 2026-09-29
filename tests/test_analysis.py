"""Where a suite is stuck: root causes, load-bearing scenarios, and progress towards each goal."""
from specdeck import and_, given, scenario, then, when
from specdeck.history import analyze


def S(id, status, deps=()):
    return {"id": id, "status": status, "depends_on": list(deps)}


GRAPH = [
    S("diff", "failed"),
    S("arid", "passed"),
    S("mutants", "blocked", ["diff", "arid"]),
    S("status", "blocked", ["mutants"]),
    S("report", "blocked", ["status"]),
    S("detect", "failed"),
    S("accept", "blocked", ["report", "detect"]),
    S("lonely", "passed"),
]


@scenario("analysis-frontier", tags=["analysis"])
def test_root_causes_are_failures_with_no_failing_prerequisite():
    """'Stuck at' lists only the root-cause failures, the biggest blocker first."""
    with given("two independent failures, one of which blocks a long chain"):
        scen = [dict(s) for s in GRAPH]
    with when("the board is analysed"):
        a = analyze(scen)
    with then("both failures are root causes, the one holding up more work first", expected="['diff', 'detect']"):
        assert a["frontier"] == ["diff", "detect"]
    with and_("each blocked scenario points at the root causes it waits on"):
        by = {s["id"]: s for s in scen}
        assert by["mutants"]["blocked_by"] == ["diff"]
        assert sorted(by["accept"]["blocked_by"]) == ["detect", "diff"]


@scenario("analysis-failure-behind-a-failure", tags=["analysis"], depends_on=["analysis-frontier"])
def test_a_failure_behind_another_failure_is_not_a_root_cause():
    """A failing scenario whose prerequisite also fails is a symptom, not a place to start."""
    with given("b fails, and it depends on a, which also fails"):
        scen = [S("a", "failed"), S("b", "failed", ["a"])]
    with then("only a is listed", expected="['a']"):
        assert analyze(scen)["frontier"] == ["a"]


@scenario("analysis-critical", tags=["analysis"])
def test_load_bearing_scenarios_are_ranked_by_everything_downstream():
    """'Load-bearing' ranks scenarios by how many others depend on them, directly or not."""
    with when("the graph is analysed"):
        scen = [dict(s) for s in GRAPH]
        a = analyze(scen)
        by = {s["id"]: s for s in scen}
    with then("diff and arid each carry four scenarios, ahead of mutants", expected="diff, arid, mutants"):
        assert a["critical"][:3] == ["diff", "arid", "mutants"]
        assert by["diff"]["downstream"] == 4
    with and_("a scenario nothing depends on is not listed"):
        assert "lonely" not in a["critical"]


@scenario("analysis-goals", tags=["analysis"])
def test_goals_show_progress_over_everything_they_rely_on():
    """Each end of a branch is a goal, with progress over itself and all its prerequisites."""
    with when("the graph is analysed"):
        goals = analyze([dict(s) for s in GRAPH])["goals"]
    with then("the one goal is 'accept', stuck, with 1 of 7 passing", expected="accept stuck 1/7"):
        assert [(g["id"], g["state"], g["counts"]["passed"], g["total"]) for g in goals] == [("accept", "stuck", 1, 7)]
    with and_("it names where it is stuck", expected="diff, detect"):
        assert goals[0]["stuck_at"] == ["diff", "detect"]
    with and_("a goal whose whole branch passes is complete"):
        done = analyze([S("a", "passed"), S("b", "passed", ["a"])])["goals"]
        assert done[0]["state"] == "complete"
