"""Rule checks: describing how a rule is checked, and detecting when a change breaks it (docs/RULE-CHECKS.md)."""
import json
import shutil
import subprocess

import pytest

from specdeck import and_, given, scenario, then, when
from specdeck.checks import run_all, validate
from specdeck.requirements import load


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root)})


def repo(root, files: dict):
    """A git repository with ``files`` committed."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def write(root, files: dict):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def results(root, rules_yaml: str, tests=None, base="HEAD"):
    (root / "requirements.yaml").write_text("requirements:\n" + rules_yaml)
    reqs = load(root / "requirements.yaml")
    assert reqs.problems == []
    return {rid: r for rid, r in run_all(reqs, root, base, tests).items()}


BASE = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["pyyaml"]\n',
    "shop/money.py": "from decimal import Decimal\nLEGACY = float('1.5')\n",
    "shop/gateway.py": "import urllib.request\n",
    "shop/orders.py": "def total(x):\n    return x\n",
    "migrations/0001.sql": "create table orders(id int);\n",
    "docs/schema.md": "orders\n",
}


@scenario("check-primitives", group="checks", tags=["checks", "rules"], covers=["rules.checks"])
def test_each_kind_of_check_detects_its_break(tmp_path):
    """Each kind of check says whether a change broke the rule, and where: patterns in added lines,
    imports (also through importlib), protected files, files that must change together, new
    dependencies, ruff rule codes, tests, and commands."""
    with given("a committed project, then an agent's change that breaks several rules"):
        root = repo(tmp_path / "p", BASE)
        write(root, {
            "shop/orders.py": "import importlib\nfrom datetime import datetime\n\ndef total(x):\n"
                              "    pd = importlib.import_module('pandas')\n    print(x)\n"
                              "    stamp = datetime.now()\n    return float(x)\n",
            "shop/report.py": "import requests\n",
            "migrations/0002.sql": "alter table orders add note text;\n",
            "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["pyyaml", "pandas>=2", "Requests"]\n',
        })
    with when("each rule's check is evaluated against the change"):
        r = results(root, """\
  - {id: money.float, statement: "Money shall not use float.", check: {forbid_pattern: 'float\\(', paths: [shop/**]}}
  - {id: io.gateway, statement: "Only the gateway shall do IO.", check: {forbid_import: [requests, pandas, urllib], except: [shop/gateway.py]}}
  - {id: migrations.frozen, statement: "Migrations shall stay unedited.", check: {protect: [migrations/0001.sql]}}
  - {id: schema.docs, statement: "Schema docs shall follow migrations.", check: {co_change: {changed: [migrations/**], requires: [docs/schema.md]}}}
  - {id: deps.none, statement: "No dependency shall be added.", check: {no_new_dependencies: {allow: [requests]}}}
  - {id: time.utc, statement: "Time shall be aware.", check: {ruff: [DTZ005], paths: [shop/**]}}
  - {id: done.tests, statement: "The suite shall pass.", check: {test: [tests/test_x.py::test_a]}}
  - {id: done.lint, statement: "The tree shall be clean.", check: {command: "test -f docs/schema.md"}}
""", tests={"tests/test_x.py::test_a": "failed"})
    with then("a float added in shop/ is found at its line; the old float(...) line is not the change's",
              expected="shop/orders.py:8 only"):
        assert r["money.float"].state == "broken"
        assert [e.split(":")[:2] for e in r["money.float"].evidence] == [["shop/orders.py", "8"]]
    with and_("an import through importlib counts, the gateway is exempt", expected="pandas and requests, not urllib"):
        assert r["io.gateway"].state == "broken"
        joined = " ".join(r["io.gateway"].evidence)
        assert "imports pandas" in joined and "imports requests" in joined and "gateway" not in joined
    with and_("the frozen migration is untouched, but schema docs did not follow the new one",
              expected="protect holds; co_change broken"):
        assert r["migrations.frozen"].state == "holds" and r["schema.docs"].state == "broken"
    with and_("pandas is a new dependency; requests is allowed", expected="adds pandas"):
        assert r["deps.none"].evidence == ["pyproject.toml: adds pandas"]
    if shutil.which("ruff"):
        with and_("ruff's DTZ005 finds the naive datetime.now() on an added line", expected="shop/orders.py:7"):
            assert r["time.utc"].state == "broken" and r["time.utc"].evidence[0].startswith("shop/orders.py:7:")
    with and_("a failing test breaks its rule; a command that succeeds holds"):
        assert r["done.tests"].state == "broken" and r["done.lint"].state == "holds"


@scenario("check-compose", group="checks", tags=["checks", "rules"], depends_on=["check-primitives"],
          covers=["rules.checks"])
def test_checks_compose_and_apply_only_when_relevant(tmp_path):
    """Checks combine with all / any / not; `when` limits a rule to changes that touch given paths,
    which is also whether the rule applied to the change (the denominator of its break rate). A
    check that cannot run is an error, never a break."""
    with given("a change that touches only shop/orders.py, adding a print"):
        root = repo(tmp_path / "p", BASE)
        write(root, {"shop/orders.py": "def total(x):\n    print(x)\n    return x\n"})
    with when("composed checks and a migration-only rule are evaluated"):
        r = results(root, """\
  - {id: a, statement: "A shall hold.", check: [{forbid_pattern: 'print\\('}, {protect: [docs/**]}]}
  - {id: b, statement: "B shall hold.", check: {any: [{forbid_pattern: 'print\\('}, {protect: [docs/**]}]}}
  - {id: c, statement: "C shall hold.", check: {not: {protect: [shop/**]}}}
  - {id: d, statement: "D shall hold.", check: {forbid_pattern: 'print\\(', when: {changed: [migrations/**]}}}
""")
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "x.py").write_text("print(1)\n")
        no_git = results(plain, """\
  - {id: e, statement: "E shall hold.", check: {protect: [x.py]}}
  - {id: f, statement: "F shall hold.", check: {forbid_pattern: 'print\\(', scope: files}}
""")
    with then("a list is all-of; any holds if one holds; not inverts", expected="a broken, b holds, c holds"):
        assert (r["a"].state, r["b"].state, r["c"].state) == ("broken", "holds", "holds")
    with and_("a rule scoped to migrations does not apply to this change", expected="holds, not applicable"):
        assert r["d"].state == "holds" and r["d"].applicable is False
    with and_("without git, a change check is an error, while a whole-file check still runs"):
        assert no_git["e"].state == "error" and "no change to check" in no_git["e"].error
        assert no_git["f"].state == "broken"


@scenario("check-validate", group="checks", tags=["checks", "rules", "validator"], covers=["rules.checks"])
def test_a_malformed_check_is_reported_and_skipped(tmp_path):
    """A check is data, so it can be validated before anything runs: unknown keys, two checks in one
    node, a regex that does not compile, or a list where a mapping is needed."""
    with given("four malformed checks"):
        bad = [{"forbid_pattern": "("}, {"protect": "docs/**"}, {"forbid_pattern": "x", "protect": ["a"]},
               {"forbid": ["x"]}]
    with when("they are validated"):
        problems = [validate(b) for b in bad]
    with then("each is reported in words", expected="does not compile; list of names; exactly one; unknown key"):
        assert "does not compile" in problems[0][0]
        assert "list of names" in problems[1][0]
        assert "exactly one of" in problems[2][-1]
        assert "unknown key" in problems[3][0]
    with and_("in a requirements file, the problem is reported and the check skipped"):
        f = tmp_path / "requirements.yaml"
        f.write_text("requirements:\n  - {id: r, statement: 'R shall hold.', check: {forbid_pattern: '('}}\n")
        reqs = load(f)
        assert any("r: check.forbid_pattern does not compile" in p for p in reqs.problems)
        assert run_all(reqs, tmp_path) == {}


RULES = """\
requirements:
  - id: money.decimal
    statement: Money amounts shall be Decimal values, with no float in the shop package.
    rationale: A float refund was one cent short.
    type: design-constraint
    check: {forbid_pattern: 'float\\(', paths: [shop/**]}
"""


@scenario("check-in-run", group="checks", tags=["checks", "rules"], depends_on=["check-primitives"],
          covers=["rules.check-status", "rules.breaks"])
def test_a_run_reports_a_broken_rule_and_logs_it(pytester, run, monkeypatch, capsys):
    """On each pytest run the checks are evaluated against the uncommitted change: a broken rule makes
    its requirement failing, is named with its evidence, goes first in AGENTS.md, and the run is
    logged, so `specdeck breaks` can say how often each rule broke."""
    from specdeck.cli import main

    with given("a committed project with a rule and its check, and a passing test"):
        root = pytester.path
        repo(root, {"shop/__init__.py": "", "shop/money.py": "from decimal import Decimal\n",
                    "requirements.yaml": RULES, "test_ok.py": "def test_ok():\n    assert True\n"})
    with when("an agent adds a float, the suite runs, then the float is removed and it runs again"):
        write(root, {"shop/money.py": "def refund(x):\n    return float(x) * 0.97\n"})
        broken, _, _ = run()
        rep = json.loads((root / ".specdeck" / "requirements.json").read_text())
        monkeypatch.chdir(root)
        main(["agents", "--out", "AGENTS.md"])
        agents = (root / "AGENTS.md").read_text()
        write(root, {"shop/money.py": "from decimal import Decimal\n"})
        run()
    with then("the run names the broken rule and where", expected="1 failing (money.decimal); shop/money.py:2"):
        broken.stdout.fnmatch_lines(["*requirements: 0 of 1 met, 1 failing (money.decimal)*",
                                     "*rule money.decimal broken: shop/money.py:2: return float(x) * 0.97*"])
        [d] = rep["requirements"]
        assert d["status"] == "failing" and d["check_result"]["state"] == "broken"
    with and_("AGENTS.md opens with the broken rule and says how it is checked"):
        assert "**Broken in the last run:** `money.decimal`" in agents
        assert "Checked by: no match for `float\\(` in added lines in shop/** — **broken in the last run**" in agents
    with and_("the breaks log has both runs: applied twice, broken once", expected="2 applied, 1 broken, 50%"):
        lines = [json.loads(x) for x in (root / ".specdeck" / "breaks.jsonl").read_text().splitlines()]
        assert [x["requirements"]["money.decimal"]["status"] for x in lines] == ["failing", "met"]
        from specdeck.checks import summarize
        [s] = summarize(root / ".specdeck" / "breaks.jsonl")
        assert (s["applied"], s["broken"], s["rate"]) == (2, 1, 0.5)
    with and_("`specdeck breaks` lists the rule with its rate and the evidence of its last break"):
        capsys.readouterr()
        main(["breaks"])
        out = capsys.readouterr().out
        assert "money.decimal" in out and "50%" in out and "shop/money.py:2" in out


@scenario("check-cli", group="checks", tags=["checks", "rules", "cli"], depends_on=["check-primitives"],
          covers=["rules.check-cli"])
def test_specdeck_check_runs_the_checks_without_the_tests(tmp_path, monkeypatch, capsys):
    """`specdeck check` evaluates the checks against a base revision without running tests: for CI, a
    pre-commit hook, or an agent's hook after each edit. With --fail it exits 1 on a broken rule."""
    from specdeck.cli import main

    with given("a committed project with a rule, and a change on top that breaks it"):
        root = repo(tmp_path / "p", {"shop/money.py": "x = 1\n", "requirements.yaml": RULES})
        write(root, {"shop/money.py": "x = float(1)\n"})
        monkeypatch.chdir(root)
    with when("specdeck check runs, with and without --fail"):
        plain = main(["check"])
        out = capsys.readouterr().out
        failing = main(["check", "--fail"])
        (root / "shop/money.py").write_text("x = 1\n")
        clean = main(["check", "--fail"])
    with then("it prints the broken rule with its evidence", expected="✖ money.decimal broken"):
        assert "✖ money.decimal" in out and "shop/money.py:1: x = float(1)" in out
    with and_("only --fail turns a break into exit code 1", expected="0, 1, then 0 once fixed"):
        assert (plain, failing, clean) == (0, 1, 0)
