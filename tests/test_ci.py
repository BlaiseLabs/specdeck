"""The trust join with proofdeck, and the Markdown report for a pull request."""
import io
import json

import yaml

from specdeck import and_, given, scenario, then, when
from specdeck.ci import build, exit_code, render_markdown
from specdeck.cli import main
from specdeck.trust import join, summary_line

T = {k: f"tests/test_x.py::test_{k}" for k in ("a", "b", "c", "d")}


def _requirements(status_e="failing"):
    def req(rid, status, *tests):
        return {"id": rid, "says": f"{rid} says", "status": status,
                "scenarios": [{"id": t.rsplit("_", 1)[-1], "status": "passed", "nodeid": t} for t in tests]}
    reqs = [req("a.checked", "met", T["a"]), req("b.unproven", "met", T["b"]),
            req("c.mocked", "met", T["c"]), req("d.untouched", "met", T["d"]),
            req("e.failing", status_e, T["a"])]
    counts = {"met": 4, "failing": 1, "blocked": 0, "not-run": 0, "untested": 0, "total": 5}
    return {"file": "requirements.yaml", "counts": counts, "requirements": reqs, "unknown": [], "problems": []}


def _proofdeck():
    line = lambda n, status, ran, killed=(): {"path": "src/m.py", "line": n, "status": status,  # noqa: E731
                                              "ran_by": list(ran), "killed_by": list(killed)}
    everyone = list(T.values())
    return {"lines": [line(10, "checked", [T["a"]], [T["a"]]),
                      line(20, "ran-only", [T["b"]]),
                      line(30, "mock-only", [T["c"]], [T["c"]]),
                      dict(line(1, "ran-only", everyone), at_import=True),     # a constant nobody checks
                      dict(line(2, "import-time", everyone), at_import=True)],
            "tests": [{"nodeid": T["a"], "fakes": []}, {"nodeid": T["b"], "fakes": []},
                      {"nodeid": T["c"], "fakes": ["db"]}],
            "findings": []}


@scenario("trust-join-verdicts", group="ci", tags=["trust", "proofdeck"], covers=["trust.proven-not-passing"])
def test_each_met_requirement_gets_a_verdict_from_proofdecks_evidence():
    """Joined on the node id, a met requirement is checked, unproven, mock-only or untouched."""
    with given("four met requirements whose tests ran different changed lines"):
        reqs, proof = _requirements(), _proofdeck()
    with when("the two reports are joined"):
        result = join(reqs, proof)
        by = {r["id"]: r for r in result["requirements"]}
    with then("a line noticed by a test that fakes nothing is checked", expected="checked"):
        assert by["a.checked"]["verdict"] == "checked" and by["a.checked"]["checked_by_own_test"]
    with and_("a line the test ran but whose mutant survived is unproven", expected="unproven"):
        assert by["b.unproven"]["verdict"] == "unproven"
        assert by["b.unproven"]["lines"]["unproven"] == ["src/m.py:20"]
    with and_("a line only noticed through fakes is mock-only, and the requirement is met through fakes"):
        assert by["c.mocked"]["verdict"] == "mock-only" and by["c.mocked"]["through_fakes"]
        assert by["c.mocked"]["fakes"] == ["db"]
    with and_("a requirement whose tests ran nothing that changed is untouched", expected="untouched"):
        assert by["d.untouched"]["verdict"] == "untouched"
    with and_("lines every test ran only because the import ran them count for nobody"):
        assert all("src/m.py:1" not in r["lines"]["unproven"] for r in result["requirements"])
    with and_("the counts cover met requirements only", expected="met 4, untrusted 2"):
        c = result["counts"]
        assert (c["met"], c["checked"], c["unproven"], c["mock-only"], c["untouched"]) == (4, 1, 1, 1, 1)
        assert c["untrusted"] == 2 and c["through_fakes"] == 1
        assert "2 met" not in summary_line(result) and "1 unproven" in summary_line(result)
    with and_("the worst verdicts come first"):
        assert [r["verdict"] for r in result["requirements"]][:2] == ["unproven", "mock-only"]


@scenario("trust-join-nodeid-from-spec", group="ci", tags=["trust"], depends_on=["trust-join-verdicts"])
def test_node_ids_fall_back_to_the_spec_when_the_report_predates_them():
    """A requirements report without node ids still joins, via the spec's logic lines."""
    with given("a report whose scenarios carry no nodeid, and the spec that names them"):
        reqs = _requirements()
        for r in reqs["requirements"]:
            for s in r["scenarios"]:
                del s["nodeid"]
        spec = {"scenarios": [{"id": k, "logic": [f"pytest {T[k]}"]} for k in T]}
    with when("joined with the spec"):
        result = join(reqs, _proofdeck(), spec=spec)
    with then("the verdicts are the same", expected="unproven for b"):
        assert {r["id"]: r["verdict"] for r in result["requirements"]}["b.unproven"] == "unproven"


def _spec():
    def sc(i, status, deps=()):
        return {"id": i, "name": i.upper(), "group": "g", "tags": [f"status:{status}"], "depends_on": list(deps),
                "steps": [], "logic": [f"pytest tests/test_x.py::test_{i}"]}
    return {"feature": "demo", "groups": [{"id": "g", "name": "G"}],
            "scenarios": [sc("a", "passed"), sc("b", "failed", ["a"]), sc("c", "skipped", ["b"]), sc("d", "passed")]}


@scenario("report-reads-the-spec", group="ci", tags=["report"], covers=["report.pr-comment"])
def test_statuses_come_from_the_spec_tags_and_blocks_cascade():
    """A skip under a failure is a block, blocks cascade, and a scenario with no status tag is not run."""
    with given("b failed; c skipped under it; e skipped under c but LISTED BEFORE c; and an untagged scenario"):
        spec = _spec()
        spec["scenarios"].insert(0, {"id": "e", "name": "E", "group": "g", "tags": ["status:skipped", 7],
                                     "depends_on": ["c"], "steps": [], "logic": ["pytest tests/test_x.py::test_e"]})
        spec["scenarios"].append({"id": "f", "name": "F", "group": "g", "tags": ["g"], "depends_on": [],
                                  "steps": [], "logic": ["pytest tests/test_x.py::test_f"]})
        spec["scenarios"].append({"id": "g", "name": "G", "group": "g", "tags": ["status:pending"], "depends_on": [],
                                  "steps": [], "logic": ["pytest tests/test_x.py::test_g"]})
    with when("the report model is built without proofdeck"):
        model = build(spec, _requirements(), None)
        md = render_markdown(model)
    with then("c and e are blocked, through two levels and against document order", expected="blocked: e, c"):
        assert [s["id"] for s in model["blocked"]] == ["e", "c"]
    with and_("the untagged scenario and the pending one count as not run", expected="2 not run"):
        assert model["counts"]["notrun"] == 2 and "2 not run" in md
    with and_("without proofdeck there is no proof column and no changed-lines section"):
        assert model["trust"] is None and model["changed"] is None
        assert "| requirement | status |\n|---|---|" in md and "proof" not in md and "Changed lines" not in md


@scenario("report-pr-markdown", group="ci", tags=["report"], covers=["report.pr-comment"])
def test_the_report_is_one_markdown_document_with_fix_first_and_requirements():
    """One Markdown document: counts, the root causes to fix first, who is blocked, requirements with proof."""
    with given("a spec with one failure blocking one scenario, requirements and proofdeck evidence"):
        model = build(_spec(), _requirements(), _proofdeck())
    with when("it is rendered"):
        md = render_markdown(model)
    with then("the headline counts the skipped-under-a-failure scenario as blocked", expected="2 of 4 · 1 failing · 1 blocked"):
        assert "**2 of 4 scenarios pass** · 1 failing · 1 blocked." in md
    with and_("fix-first names the root cause and what it holds up", expected="b holds up 1"):
        assert "### Fix first" in md and "- `b` B — holds up 1" in md
    with and_("the blocked scenario points at its root cause", expected="c ← b"):
        assert "- `c` ← `b`" in md
    with and_("every requirement is a table row with its proof"):
        assert "| `b.unproven` b.unproven says | ✔ met | ✖ **unproven**" in md
        assert "`src/m.py:20`" in md and "≈ **mock-only**" in md and "every covering test fakes `db`" in md
        assert "| `e.failing` e.failing says | ✖ failing | — |" in md
    with and_("the proofdeck line totals are summarised"):
        assert ("5 changed source lines: 1 checked, 2 ran but not checked, 1 only checked through fakes, "
                "1 run at import time, not checked." in md)


@scenario("report-gates", group="ci", tags=["report", "gate"], depends_on=["report-pr-markdown"],
          covers=["gate.optional"])
def test_the_report_exits_zero_unless_a_gate_is_asked_for():
    """Exit codes are opt-in: failing scenarios, unmet requirements, untrusted proof."""
    with given("the same model"):
        model = build(_spec(), _requirements(), _proofdeck())
    with then("by default the exit code is 0", expected="0"):
        assert exit_code(model) == 0
    with and_("each gate fails on its own condition", expected="1, 1, 1"):
        assert exit_code(model, fail_on_failing=True) == 1
        assert exit_code(model, fail_on_unmet=True) == 1
        assert exit_code(model, fail_on_untrusted=True) == 1
    with and_("an empty spec, a requirements file with problems and a diff with nothing to check all render"):
        reqs = dict(_requirements(), problems=["requirements.yaml: entry 3 needs `says:`"], requirements=[])
        md = render_markdown(build({"feature": "x", "scenarios": []}, reqs, {"lines": [], "tests": [], "findings": []}))
        assert "No scenarios in the spec." in md and "- ⚠ requirements.yaml: entry 3 needs `says:`" in md
        assert "No changed source lines to check." in md
        assert summary_line(join(reqs, _proofdeck())) == "trust: no requirement is met, nothing to check"
    with and_("a green model passes every gate", expected="0"):
        green = build({"feature": "x", "scenarios": [_spec()["scenarios"][0]]},
                      {"counts": {"met": 1, "total": 1}, "requirements": [], "unknown": [], "problems": []},
                      {"lines": [], "tests": [], "findings": []})
        assert exit_code(green, fail_on_failing=True, fail_on_unmet=True, fail_on_untrusted=True) == 0


@scenario("report-cli", group="ci", tags=["report", "cli"], depends_on=["report-pr-markdown", "trust-join-verdicts"])
def test_the_commands_read_the_files_a_run_leaves_behind(tmp_path, capsys, monkeypatch):
    """`specdeck trust` and `specdeck report` work from .specdeck/ plus a proofdeck JSON file."""
    with given("a run's spec and requirements report, and proofdeck's JSON, on disk"):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".specdeck").mkdir()
        (tmp_path / ".specdeck" / "spec.yaml").write_text(yaml.safe_dump(_spec()))
        (tmp_path / ".specdeck" / "requirements.json").write_text(json.dumps(_requirements()))
        (tmp_path / "proof.json").write_text(json.dumps(_proofdeck()))
    with when("trust is asked for"):
        code = main(["trust", "proof.json"])
        text = capsys.readouterr().out
    with then("it prints the summary and the untrusted lines, exit 0", expected="0"):
        assert code == 0 and text.startswith("trust: of 4 met requirements, 1 checked, 1 unproven, 1 mock-only")
        assert "unproven  src/m.py:20" in text
    with and_("--fail-on-untrusted makes it exit 1", expected="1"):
        assert main(["trust", "proof.json", "--fail-on-untrusted"]) == 1
        capsys.readouterr()
    with and_("report writes the Markdown to a file and gates on request"):
        assert main(["report", "--proofdeck", "proof.json", "--out", "out/report.md"]) == 0
        md = (tmp_path / "out" / "report.md").read_text()
        assert md.startswith("## specdeck — demo") and "✖ **unproven**" in md
        assert main(["report", "--proofdeck", "proof.json", "--fail-on-failing"]) == 1
        capsys.readouterr()
    with and_("both commands speak JSON, and trust reads proofdeck from stdin", expected="verdict keys / counts"):
        monkeypatch.setattr("sys.stdin", io.StringIO((tmp_path / "proof.json").read_text()))
        assert main(["trust", "-", "--format", "json"]) == 0
        assert json.loads(capsys.readouterr().out)["counts"]["untrusted"] == 2
        assert main(["report", "--proofdeck", "proof.json", "--format", "json"]) == 0
        assert json.loads(capsys.readouterr().out)["counts"]["blocked"] == 1
    with and_("a missing file is a usage error, exit 2, not a traceback", expected="2, 2, 2"):
        assert main(["trust", "nope.json"]) == 2
        assert main(["report", "--spec", "nope.yaml"]) == 2
        assert main(["report", "--proofdeck", "nope.json"]) == 2
        err = capsys.readouterr().err
        assert "no proofdeck report at nope.json" in err and "no spec at nope.yaml" in err
