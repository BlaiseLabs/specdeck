"""Coverage recording, and the agent document built from it.

What belongs in that document was settled by measurement (bench/agent_ab), so these
tests pin the two things the A/B showed matter: the blocking rule is stated, and the
spec is connected to the source code it exercises.
"""
import json

import yaml

from specdeck import and_, given, scenario, then, when
from specdeck.cli import main
from specdeck.context import build
from specdeck.tracing import _owners, index, to_functions

SRC = '''\
LIMIT = 50


def over(x):
    return x > LIMIT


def discount(qty):
    def tier(n):            # nested: its lines belong to discount.tier, not discount
        return 15 if n >= 100 else 0
    return tier(qty)


class Cart:
    def total(self, items):
        return sum(items)
'''


def _coverage_is_free() -> bool:
    """Is there room for a coverage session? Not when this suite is itself being measured."""
    import coverage

    return coverage.Coverage.current() is None

@scenario("tracing-owns-every-function", group="context", tags=["coverage"], covers=["context.names-the-code"])
def test_a_line_is_attributed_to_the_function_that_contains_it(tmp_path):
    """Lines map to the function that owns them, nested and inside classes; module level otherwise."""
    with given("a module with a constant, a function and a method"):
        p = tmp_path / "pricing.py"
        p.write_text(SRC)
    with when("its functions are located"):
        owners = _owners(str(p))
    with then("functions, methods and nested functions are all found, qualified",
              expected="Cart.total, discount, discount.tier, over"):
        assert sorted(n for _, _, n in owners) == ["Cart.total", "discount", "discount.tier", "over"]
    with and_("executed lines are attributed to their owner, the constant to module level"):
        at = lambda needle: next(i for i, t in enumerate(SRC.splitlines(), 1) if needle in t)  # noqa: E731
        per_test = {"t::a": {(str(p), at("return x > LIMIT"))}, "t::b": {(str(p), at("LIMIT = 50"))},
                    "t::c": {(str(p), at("return sum(items)"))}, "t::d": {(str(p), at("return 15 if"))}}
        got = to_functions(per_test, tmp_path)
        assert got["t::a"] == ["pricing.py:over"]
        assert got["t::b"] == ["pricing.py:(module level)"]
        assert got["t::c"] == ["pricing.py:Cart.total"]
    with and_("a line inside a nested function names the innermost one", expected="discount.tier"):
        assert got["t::d"] == ["pricing.py:discount.tier"]
    with and_("the functions a test spends most lines in come first", expected="Cart.total before over"):
        busy = {"t::e": {(str(p), at("return sum(items)")), (str(p), at("def total")),
                         (str(p), at("return x > LIMIT"))}}
        assert to_functions(busy, tmp_path)["t::e"] == ["pricing.py:Cart.total", "pricing.py:over"]
    with and_("functions with the same number of lines are ordered by name, so the map is stable"):
        tied = {"t::f": {(str(p), at("return sum(items)")), (str(p), at("return x > LIMIT"))}}
        assert to_functions(tied, tmp_path)["t::f"] == ["pricing.py:Cart.total", "pricing.py:over"]
    with and_("paths in `skip` are left out entirely", expected="[]"):
        assert to_functions(per_test, tmp_path, skip=("pricing",))["t::a"] == []
    with and_("a relative root resolves the same as an absolute one"):
        import os
        here = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert to_functions(per_test, ".")["t::a"] == ["pricing.py:over"]
        finally:
            os.chdir(here)


@scenario("tracing-records-a-real-run", group="context", tags=["coverage"], covers=["context.names-the-code"],
          depends_on=["tracing-owns-every-function"])
def test_a_run_records_which_scenario_executes_which_function(pytester):
    """--specdeck-coverage writes a map from scenario id to the source functions it ran."""
    with given("a source module and two tests that each use a different function"):
        pytester.makepyfile(pricing=SRC)
        pytester.makepyfile(test_p='''
            from specdeck import scenario
            import pricing

            @scenario("checks-the-boundary")
            def test_over():
                assert pricing.over(51)

            @scenario("checks-the-cart")
            def test_cart():
                assert pricing.Cart().total([1, 2]) == 3
        ''')
    with when("the suite runs with coverage recording on"):
        free = _coverage_is_free()   # false when this very run is being measured: see below
        result = pytester.runpytest("--specdeck-coverage")
        result.assert_outcomes(passed=2)
        written = pytester.path / ".specdeck" / "coverage.json"
    if not free:
        # Two coverage sessions cannot share a process. specdeck says so and carries on; the
        # mapping itself is proved by the scenario above, which needs no coverage at all.
        with then("under another coverage session it refuses out loud and writes nothing"):
            result.stdout.fnmatch_lines(["*specdeck: warning: coverage not recorded*"])
            assert not written.exists()
        return
    with then("each scenario is mapped to the function it ran, and only that one", expected="over / Cart.total"):
        cov = json.loads(written.read_text())
        assert "pricing.py:over" in cov["checks-the-boundary"]
        assert "pricing.py:Cart.total" in cov["checks-the-cart"]
        assert "pricing.py:Cart.total" not in cov["checks-the-boundary"]
    with and_("the import-time constant is attributed to nobody, since every test runs it"):
        assert all("(module level)" not in place for places in cov.values() for place in places)


@scenario("tracing-is-off-by-default", group="context", tags=["coverage", "gate"], depends_on=["tracing-records-a-real-run"])
def test_nothing_is_recorded_unless_asked(pytester):
    """Tracing costs time, so a plain run neither records nor writes a map."""
    with given("a suite with one test"):
        pytester.makepyfile(test_q='from specdeck import scenario\n\n@scenario("q")\ndef test_q(): pass\n')
    with when("it runs without the flag"):
        pytester.runpytest().assert_outcomes(passed=1)
    with then("no coverage map is written", expected="no file"):
        assert not (pytester.path / ".specdeck" / "coverage.json").exists()


@scenario("context-states-the-blocking-rule", group="context", tags=["agents"], covers=["context.states-the-rule"])
def test_the_document_says_what_depends_on_actually_means():
    """The A/B's largest single finding: without this sentence every reader applies the wrong
    rule and sorts blocked-from-failed at 40% instead of 100%."""
    with given("a spec with a dependency"):
        spec = {"feature": "demo", "scenarios": [
            {"id": "a", "name": "A", "logic": ["pytest tests/t.py::test_a"]},
            {"id": "b", "name": "B", "depends_on": ["a"], "logic": ["pytest tests/t.py::test_b"]}]}
    with when("the agent document is built"):
        doc = build(spec)
    with then("it says a prerequisite only blocks if it ran earlier", expected="ran **earlier**"):
        assert "ran **earlier**" in doc and "never reorders" in doc
    with and_("the rule is the last thing in the document, nearest whatever is asked next"):
        # measured: with the rule first and the coverage notes last, sorting real failures
        # from blocked ones fell from 100% exact to 70%
        assert doc.rstrip().endswith("it runs normally, and can fail on its own.")
    with and_("it says a later prerequisite does not block at all"):
        assert "runs **later**" in doc and "not skipped at all" in doc
    with and_("every scenario is numbered, so a coverage map can refer to it in one token"):
        body = yaml.safe_load(doc.split("```yaml", 1)[1].split("```", 1)[0])
        assert [s["n"] for s in body["scenarios"]] == [1, 2]


@scenario("context-maps-code-to-scenarios", group="context", tags=["agents"], covers=["context.names-the-code"],
          depends_on=["context-states-the-blocking-rule"])
def test_the_document_connects_source_functions_to_the_scenarios_that_run_them():
    """Without this an agent holding a diff has no bridge from `file.py:31` to any scenario."""
    with given("a spec of two scenarios and the coverage they recorded"):
        spec = {"feature": "demo", "scenarios": [
            {"id": "a", "name": "A", "logic": ["pytest tests/t.py::test_a"]},
            {"id": "b", "name": "B", "logic": ["pytest tests/t.py::test_b"]}]}
        cov = {"a": ["src/pricing.py:over"], "b": ["src/pricing.py:over", "src/cart.py:Cart.total"]}
    with when("the document is built with the map"):
        doc = build(spec, cov)
    with then("each function lists the scenario numbers that run it, under its file",
              expected="src/pricing.py: / over: [1,2]"):
        assert "src/pricing.py:\n  over: [1,2]" in doc
        assert "src/cart.py:\n  Cart.total: [2]" in doc
    with and_("it warns that running a line is not checking it, which is proofdeck's question"):
        assert "not checking it" in doc and "proofdeck" in doc
    with and_("without a map the document is still valid, just shorter"):
        assert "Cart.total" not in build(spec)
    with and_("building does not modify the spec it was handed", expected="no `n` added to the caller's dict"):
        assert "n" not in spec["scenarios"][0] and "n" not in spec
    with and_("the scenarios keep the order of keys they were written in, for a readable diff"):
        body = doc.split("```yaml", 1)[1]
        assert body.index("id: a") < body.index("name: A") < body.index("logic:")


@scenario("context-cli", group="context", tags=["agents", "cli"], depends_on=["context-maps-code-to-scenarios"])
def test_the_command_builds_the_document_from_what_a_run_left_behind(tmp_path, capsys, monkeypatch):
    """`specdeck context` reads .specdeck/ and writes one file; a missing spec is exit 2."""
    with given("a spec and a coverage map on disk"):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".specdeck").mkdir()
        (tmp_path / ".specdeck" / "spec.yaml").write_text(yaml.safe_dump(
            {"feature": "demo", "scenarios": [{"id": "a", "name": "A", "logic": ["pytest t.py::test_a"]}]}))
        (tmp_path / ".specdeck" / "coverage.json").write_text(json.dumps({"a": ["src/m.py:f"]}))
    with when("the document is written to a path whose directories do not exist yet"):
        assert main(["context", "--out", "out/docs/ctx.md"]) == 0
        doc = (tmp_path / "out" / "docs" / "ctx.md").read_text()
    with then("it carries the rule, the spec and the map", expected="all three"):
        assert "ran **earlier**" in doc and "feature: demo" in doc and "f: [1]" in doc
    with and_("without a recorded map the document is still written, and says so", expected="exit 0"):
        (tmp_path / ".specdeck" / "coverage.json").unlink()
        assert main(["context", "--out", "out/docs/bare.md"]) == 0
        bare = (tmp_path / "out" / "docs" / "bare.md").read_text()
        assert "ran **earlier**" in bare and "f: [1]" not in bare
        assert "--specdeck-coverage" in capsys.readouterr().err
    with and_("a missing spec is a usage error, not a traceback", expected="2"):
        assert main(["context", "--spec", "nope.yaml"]) == 2
        assert "no spec at nope.yaml" in capsys.readouterr().err


@scenario("tracing-unavailable-is-not-fatal", group="context", tags=["coverage"],
          depends_on=["tracing-records-a-real-run"])
def test_a_recorder_that_cannot_start_stays_inert(monkeypatch):
    """When coverage.py is missing or already in use, recording is refused and the suite goes on."""
    from specdeck import tracing

    with given("a Recorder whose coverage backend refuses to start"):
        rec = tracing.Recorder(["/tmp"])

        class Boom:
            def __init__(self, **kw):
                raise RuntimeError("already measuring")

        monkeypatch.setattr("coverage.Coverage", Boom)
    with when("it is started"):
        try:
            rec.start()
            raised = None
        except tracing.Unavailable as exc:
            raised = exc
    with then("Unavailable is raised with the reason", expected="already measuring"):
        assert raised is not None and "already measuring" in str(raised)
    with and_("it is left holding nothing, so switch and stop are safe no-ops"):
        rec.switch("t::a")
        assert rec.stop() == {}

@scenario("tracing-says-when-it-cannot-record", group="context", tags=["coverage"],
          depends_on=["tracing-unavailable-is-not-fatal"])
def test_a_run_that_cannot_record_coverage_says_so_instead_of_going_quiet(pytester, monkeypatch):
    """Asking for a map and silently getting none is the worst outcome: the document would
    just be missing its most useful half. Under another coverage session, say why."""
    with given("a suite run inside a process that is already measuring coverage"):
        import coverage

        pytester.makepyfile(test_c='from specdeck import scenario\n\n@scenario("c")\ndef test_c(): pass\n')
        # say another session holds coverage, rather than starting a real second one:
        # nesting real sessions inside pytester is what breaks, and it is not the point here
        monkeypatch.setattr(coverage.Coverage, "current", staticmethod(lambda: object()))
    with when("coverage recording is asked for anyway"):
        result = pytester.runpytest("--specdeck-coverage")
    with then("the run still passes", expected="1 passed"):
        result.assert_outcomes(passed=1)
    with and_("it says coverage was not recorded, and why", expected="already measuring"):
        result.stdout.fnmatch_lines(["*specdeck: warning: coverage not recorded*already measuring*"])
    with and_("no half-written map is left behind"):
        assert not (pytester.path / ".specdeck" / "coverage.json").exists()


@scenario("context-collapses-hub-functions", group="context", tags=["agents"],
          covers=["context.hubs-are-not-evidence"], depends_on=["context-maps-code-to-scenarios"])
def test_a_function_most_of_the_suite_runs_is_counted_not_listed():
    """Measured: handed the list for a function 47 of 88 scenarios run, an agent answered
    "which tests break" with 87 test names (truth: none); without the map it named 9. The
    list is the same for every change in that function, so it is noise that reads as evidence."""
    with given("twelve scenarios, one function run by nine of them and one run by two"):
        numbers = {f"s{i}": i for i in range(1, 13)}
        per_scenario = {f"s{i}": ["m.py:hook"] for i in range(1, 10)}
        per_scenario["s1"].append("m.py:narrow")
        per_scenario["s2"].append("m.py:narrow")
        for i in range(10, 13):
            per_scenario[f"s{i}"] = ["m.py:other"]
    with when("the map is built"):
        mapped = index(per_scenario, numbers, total=12)
    with then("the widely-run function is a count, not a list", expected="('most', 9, 12)"):
        assert mapped["m.py"]["hook"] == ("most", 9, 12)
    with and_("the narrow one is still listed, because there the list is the answer"):
        assert mapped["m.py"]["narrow"] == [1, 2]
    with and_("a short list is never collapsed, however small the suite", expected="[1, 2] of 2"):
        assert index({"a": ["m.py:f"], "b": ["m.py:f"]}, {"a": 1, "b": 2}, total=2)["m.py"]["f"] == [1, 2]
    with and_("the document renders the count and warns against reading it as a long answer"):
        spec = {"feature": "d", "scenarios": [{"id": f"s{i}", "name": f"S{i}",
                                               "logic": [f"pytest t.py::test_{i}"]} for i in range(1, 13)]}
        doc = build(spec, per_scenario)
        assert "hook: most (9 of 12)" in doc and "narrow: [1,2]" in doc
        assert "Do not read a large count as" in doc and "far *fewer* tests" in doc


@scenario("context-focus-trims-the-irrelevant", group="context", tags=["agents", "scale"],
          covers=["context.scales-to-a-diff"], depends_on=["context-collapses-hub-functions"])
def test_a_document_written_for_a_diff_keeps_only_what_touches_it():
    """On a large suite most scenarios have nothing to do with the files a reviewer changed.
    They stay, in brief, because depends_on reaches across the suite — but their steps go."""
    with given("two scenarios, one running the changed file and one not"):
        spec = {"feature": "d", "scenarios": [
            {"id": "near", "name": "Near", "depends_on": [], "tags": ["x", "status:passed"],
             "steps": [{"keyword": "Given", "text": "something long and detailed"}],
             "logic": ["pytest t.py::test_near"]},
            {"id": "far", "name": "Far", "depends_on": ["near"], "tags": ["y", "status:passed"],
             "steps": [{"keyword": "Given", "text": "something else long and detailed"}],
             "logic": ["pytest t.py::test_far"]}]}
        cov = {"near": ["src/changed.py:f"], "far": ["src/other.py:g"]}
    with when("the document is written for a change to src/changed.py"):
        doc = build(spec, cov, focus=["src/changed.py"])
    with then("the scenario that runs it keeps its steps", expected="the Given survives"):
        assert "something long and detailed" in doc
    with and_("the one that does not loses its steps but keeps id, name, dependency and node id"):
        assert "something else long and detailed" not in doc
        assert "id: far" in doc and "depends_on" in doc and "pytest t.py::test_far" in doc
    with and_("its status is kept but its other tags are not, and the trimming is explained"):
        assert "status:passed" in doc and "- y" not in doc
        assert "listed in brief" in doc
    with and_("the map covers only the changed file", expected="no src/other.py"):
        assert "src/changed.py" in doc and "src/other.py" not in doc
    with and_("without focus everything is in full"):
        assert "something else long and detailed" in build(spec, cov)


@scenario("context-profiles-choose-the-material", group="context", tags=["agents", "scale"],
          covers=["context.strategies"], depends_on=["context-focus-trims-the-irrelevant"])
def test_each_profile_carries_what_it_promises_and_nothing_else(tmp_path):
    """Measured: no single document is best at everything. Giving the test files the dependency
    rule took 'which tests break' from 0.93 to 0.85; loading the generated document with
    coverage instructions took root-cause sorting from 100% to 70%. So it is a choice."""
    from specdeck import profiles

    with given("a spec of one scenario, its coverage, and the test file behind it"):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_a.py").write_text("def test_a():\n    assert mix(1) == 2\n")
        spec = {"feature": "d", "scenarios": [
            {"id": "a", "name": "A", "depends_on": [], "tags": ["status:passed"],
             "steps": [{"keyword": "Given", "text": "a recorded step"}],
             "logic": ["pytest tests/test_a.py::test_a"]}]}
        cov = {"a": ["src/m.py:f"]}
    with when("every profile is rendered"):
        docs = {n: build(spec, cov, profile=n, root=str(tmp_path)) for n in profiles.PROFILES}
    with then("brief keeps the index and map but drops the recorded steps", expected="no Given"):
        assert "id: a" in docs["brief"] and "f: [1]" in docs["brief"]
        assert "a recorded step" not in docs["brief"]
    with and_("lean keeps the steps and drops the map"):
        assert "a recorded step" in docs["lean"] and "f: [1]" not in docs["lean"]
    with and_("balanced carries both"):
        assert "a recorded step" in docs["balanced"] and "f: [1]" in docs["balanced"]
    with and_("wiring is the test files alone — no scenarios, no map, and no rule to dilute them"):
        assert "assert mix(1) == 2" in docs["wiring"]
        assert "id: a" not in docs["wiring"] and "ran **earlier**" not in docs["wiring"]
    with and_("everything carries the lot"):
        assert "assert mix(1) == 2" in docs["everything"] and "a recorded step" in docs["everything"]
    with and_("naming the question picks the specialist, and they are different documents"):
        breaks, stuck = profiles.for_task("breaks"), profiles.for_task("stuck")
        assert breaks.test_sources and not breaks.rule      # the rule costs 0.93 -> 0.85 here
        assert stuck.rule and not stuck.test_sources        # and is worth 40% -> 100% there
        assert profiles.for_task(None).name == profiles.DEFAULT
    with and_("each one states what it measured, so the choice is informed, and an unknown name is refused"):
        assert all(p.measured for p in profiles.PROFILES.values())
        try:
            profiles.get("fastest")
            raise AssertionError("should have refused")
        except KeyError as exc:
            assert "balanced" in str(exc)


@scenario("context-states-the-why", group="context", tags=["agents", "requirements"],
          covers=["context.states-the-why"], depends_on=["context-profiles-choose-the-material"])
def test_the_document_says_what_each_requirement_is_for_and_which_tests_serve_it(tmp_path, monkeypatch, capsys):
    """The reasons live in requirements.yaml, not in the tests, so the document has to carry them."""
    from specdeck import profiles

    with given("a spec and the requirements report a run left behind, one test serving two requirements"):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".specdeck").mkdir()
        spec = {"feature": "d", "scenarios": [
            {"id": "a", "name": "A", "tags": ["status:passed", "req:r.one"], "logic": ["pytest t.py::test_a"]},
            {"id": "b", "name": "B", "tags": ["status:passed", "req:r.one", "req:r.two"], "logic": ["pytest t.py::test_b"]}]}
        report = {"requirements": [
            {"id": "r.one", "says": "Totals are rounded half up.", "why": "Finance reconciles\n  to the cent.",
             "status": "met", "scenarios": [{"id": "a", "nodeid": "t.py::test_a"}, {"id": "b", "nodeid": "t.py::test_b"}]},
            {"id": "r.two", "says": "Refunds never exceed the charge.", "status": "met",
             "scenarios": [{"id": "b", "nodeid": "t.py::test_b"}]},
            {"id": "r.three", "says": "Nobody has written a test for this yet.", "status": "untested", "scenarios": []}]}
        (tmp_path / ".specdeck" / "spec.yaml").write_text(yaml.safe_dump(spec))
        (tmp_path / ".specdeck" / "requirements.json").write_text(json.dumps(report))
    with when("the default document and the intent document are built"):
        full = build(spec, None, requirements=report)
        intent = build(spec, None, profile="intent", requirements=report)
    with then("each requirement appears with what it says and why", expected="says and why, on one line"):
        for doc in (full, intent):
            assert "Says: Totals are rounded half up." in doc and "Why: Finance reconciles to the cent." in doc
    with and_("a test serving two requirements names the other one, so 'only this one' can be read off"):
        assert "`t.py::test_b` — scenario `b` (also covers r.two)" in intent
        assert "`t.py::test_a` — scenario `a`\n" in intent
        assert "Tests: none" in intent
    with and_("the intent document is the requirements alone", expected="no scenarios, no rule"):
        assert "## Scenarios" not in intent and "ran **earlier**" not in intent
        assert "## Scenarios" in full
    with and_("asking why picks it, and the command finds the report next to the spec"):
        assert profiles.for_task("why").name == "intent"
        assert main(["context", "--task", "why", "--out", "why.md"]) == 0
        assert "Refunds never exceed the charge." in (tmp_path / "why.md").read_text()
    with and_("without a report the intent document is refused rather than written empty", expected="2"):
        (tmp_path / ".specdeck" / "requirements.json").unlink()
        assert main(["context", "--task", "why"]) == 2
        assert "no requirements report" in capsys.readouterr().err
