"""Partial-run merge, history pruning, run comparison and watch mode."""
import textwrap

import yaml

from specdeck import and_, given, scenario, then, when
from specdeck.history import History
from specdeck.watch import changed, snapshot


def scen(*ids):
    """Scenario rows in the shape start_run expects."""
    return [{"id": i, "name": i.upper(), "nodeid": f"tests/test_x.py::test_{i}"} for i in ids]


@scenario("merge-partial-run", group="maintenance", tags=["spec"], covers=["run.partial-safe"])
def test_partial_run_can_merge_instead_of_skipping(pytester, run):
    """`-k` with --specdeck-merge updates the scenarios that ran and keeps the rest."""
    with given("a full spec of two scenarios"):
        pytester.makepyfile(test_m='''
            from specdeck import scenario

            @scenario("one")
            def test_one(): pass

            @scenario("two")
            def test_two(): pass
        ''')
        run()
    with when("only one test is re-run with --specdeck-merge"):
        result, spec, _ = run("-k", "test_one", "--specdeck-merge")
    with then("both scenarios are still in the spec", expected="one and two present"):
        ids = [s["id"] for s in spec["scenarios"]]
        assert ids == ["one", "two"]
    with and_("only the scenario that ran was replaced", expected="merged 1 of 2"):
        out = " ".join(result.stdout.str().split())  # the line may wrap
        assert "merged 1 scenario(s) into spec.yaml (2 total)" in out
    with and_("the deselected scenario keeps its real outcome, not 'pending'"):
        two = next(s for s in spec["scenarios"] if s["id"] == "two")
        assert "status:passed" in two["tags"]


@scenario("merge-needs-a-spec", group="maintenance", tags=["spec"], depends_on=["merge-partial-run"], covers=["run.partial-safe"])
def test_merge_without_an_existing_spec_explains_itself(pytester, run):
    """Merging before any full run has nothing to merge into, and says so."""
    with given("a suite that has never been fully run"):
        pytester.makepyfile(test_m="def test_one(): pass\ndef test_two(): pass")
    with when("a partial run asks to merge"):
        result, _, _ = run("-k", "test_one", "--specdeck-merge")
    with then("it explains that a full run is needed first", expected="needs an existing spec.yaml"):
        assert "needs an existing" in result.stdout.str()


@scenario("partial-run-default", group="maintenance", tags=["spec"], depends_on=["merge-partial-run"], covers=["run.partial-safe"])
def test_partial_run_still_refuses_by_default(pytester, run):
    """Without --specdeck-merge a partial run leaves the spec alone, and offers the flag."""
    with given("an existing full spec"):
        pytester.makepyfile(test_m="def test_one(): pass\ndef test_two(): pass")
        run()
    with when("one test is re-run with no merge flag"):
        result, spec, _ = run("-k", "test_one")
    with then("the spec is untouched and the flag is suggested", expected="--specdeck-merge mentioned"):
        assert len(spec["scenarios"]) == 2
        assert "--specdeck-merge" in result.stdout.str()


@scenario("history-prune", group="maintenance", tags=["history"], covers=["history.prune"])
def test_prune_keeps_the_newest_runs(tmp_path):
    """Runs and results were never pruned; prune bounds them per project."""
    with given("a project with five recorded runs"):
        h = History(tmp_path / "h.db")
        pid = h.register_project(str(tmp_path), "proj")["id"]
        for _ in range(5):
            rid = h.start_run(pid, scen("a"))
            h.set_result(pid, rid, "a", "passed")
            h.finish_run(pid, rid)
    with when("prune keeps the newest two"):
        res = h.prune(keep_runs=2)
    with then("three runs are removed", expected="removed_runs == 3"):
        assert res["removed_runs"] == 3
    with and_("two remain"):
        assert len(h.runs(pid)) == 2
    h.close()


@scenario("history-forget", group="maintenance", tags=["history"], depends_on=["history-prune"], covers=["history.forget"])
def test_forget_removes_a_project(tmp_path):
    """A project you no longer care about can be dropped entirely."""
    with given("a recorded project"):
        h = History(tmp_path / "h.db")
        pid = h.register_project(str(tmp_path), "gone")["id"]
        rid = h.start_run(pid, scen("a"))
        h.finish_run(pid, rid)
    with when("it is forgotten"):
        ok = h.forget("gone")
    with then("it is gone from the project list", expected="no projects left"):
        assert ok and h.projects() == []
    h.close()


@scenario("history-compare", group="maintenance", tags=["history"], depends_on=["history-prune"], covers=["history.compare"])
def test_compare_two_runs(tmp_path):
    """Comparing non-adjacent runs sorts scenarios into fixed, regressed, new and gone."""
    with given("two runs where one scenario was fixed, one regressed and one appeared"):
        h = History(tmp_path / "h.db")
        pid = h.register_project(str(tmp_path), "proj")["id"]
        r1 = h.start_run(pid, scen("a", "b"))
        h.set_result(pid, r1, "a", "failed")
        h.set_result(pid, r1, "b", "passed")
        h.finish_run(pid, r1)
        r2 = h.start_run(pid, scen("a", "b", "c"))
        h.set_result(pid, r2, "a", "passed")
        h.set_result(pid, r2, "b", "failed")
        h.set_result(pid, r2, "c", "passed")
        h.finish_run(pid, r2)
    with when("the two runs are compared"):
        res = h.compare(pid, r1, r2)
    with then("each scenario lands in the right bucket", expected="a fixed, b regressed, c new"):
        assert [x["id"] for x in res["fixed"]] == ["a"]
        assert [x["id"] for x in res["regressed"]] == ["b"]
        assert [x["id"] for x in res["new"]] == ["c"]
    h.close()


@scenario("watch-detects-changes", group="maintenance", tags=["watch"], covers=["watch.rerun-on-change"])
def test_watch_snapshot_notices_edits(tmp_path):
    """Watch mode compares cheap stat() snapshots; no OS watcher, no dependency."""
    with given("a snapshot of a directory"):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "skip.txt").write_text("not watched")
        before = snapshot(tmp_path)
    with then("only watched suffixes are tracked", expected="a.py tracked, skip.txt not"):
        assert any(p.endswith("a.py") for p in before)
        assert not any(p.endswith("skip.txt") for p in before)
    with when("a file is edited and another added"):
        (tmp_path / "a.py").write_text("x = 2")
        (tmp_path / "b.py").write_text("y = 1")
        after = snapshot(tmp_path)
    with and_("the change list names both", expected="a.py and b.py"):
        names = {p.rsplit("/", 1)[-1] for p in changed(before, after)}
        assert names == {"a.py", "b.py"}


@scenario("watch-ignores-noise", group="maintenance", tags=["watch"], depends_on=["watch-detects-changes"], covers=["watch.rerun-on-change"])
def test_watch_ignores_generated_directories(tmp_path):
    """specdeck's own output must not trigger a re-run loop."""
    with given("a .specdeck directory and a __pycache__ beside the source"):
        (tmp_path / ".specdeck").mkdir()
        (tmp_path / ".specdeck" / "spec.yaml").write_text("feature: x")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "m.py").write_text("cached")
    with when("the directory is snapshotted"):
        snap = snapshot(tmp_path)
    with then("neither is watched", expected="no .specdeck or __pycache__ entries"):
        assert not any(".specdeck" in p or "__pycache__" in p for p in snap)


@scenario("watch-runs-once", group="maintenance", tags=["watch"], depends_on=["watch-detects-changes"], covers=["watch.rerun-on-change"])
def test_watch_runs_the_suite_immediately(tmp_path):
    """Watch mode runs once up front rather than waiting for the first edit."""
    from specdeck.watch import watch

    calls = []
    with when("watch is started with once=True and an injected runner"):
        rc = watch(tmp_path, ["-q"], runner=lambda cmd: calls.append(cmd) or 0, once=True)
    with then("the suite ran exactly once, with --specdeck-live added", expected="1 call including --specdeck-live"):
        assert rc == 0 and len(calls) == 1
        assert "--specdeck-live" in calls[0]
