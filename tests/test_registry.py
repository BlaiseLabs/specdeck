"""A self-hosted rule registry: a git repository of rule cards, read from a directory or a git URL."""
import subprocess

import yaml

from specdeck import and_, given, scenario, then, when
from specdeck.cli import main

from .test_checks import repo, write

PROJECT_A = """\
requirements:
  - id: money.decimal
    statement: Money amounts shall be Decimal values, with no float in the shop package.
    rationale: A float refund was one cent short and the accounts would not reconcile.
    type: design-constraint
    check: {forbid_pattern: 'float\\(', paths: [shop/**]}
    tags: [python, money]
  - id: style.nice
    statement: The code shall be readable.
    rationale: Taste.
  - id: lint.local
    statement: The local linter shall pass.
    rationale: House rules.
    check: {command: "make lint"}
"""

PROJECT_B = """\
# Our rules. Comments and layout stay as they are.
requirements:
  - id: time.utc
    statement: Timestamps shall come from shop.clock.now().
    rationale: Naive timestamps put orders on the wrong day.

adherence:
  conflicts: If a request conflicts with a rule, stop and ask.
"""


@scenario("registry-roundtrip", group="registry", tags=["registry", "rules"], depends_on=["check-cli"],
          covers=["registry.self-hosted", "registry.publish", "registry.shareable-only", "registry.add"])
def test_a_rule_travels_through_a_self_hosted_registry(tmp_path, monkeypatch, capsys):
    """A registry is a git repository of rule cards. One project publishes a rule into it; another
    finds it, by directory or by git URL, adds it with its check and where it came from, and the
    rule then catches a break there. Only rules with a reason and a data check are shared."""
    monkeypatch.setenv("SPECDECK_HOME", str(tmp_path / "home"))
    reg = tmp_path / "rules-registry"

    with given("an empty registry, and a project with a checkable rule, a rule with no check, and one that runs a command"):
        assert main(["rules", "init", str(reg)]) == 0
        a = repo(tmp_path / "a", {"requirements.yaml": PROJECT_A})
        monkeypatch.chdir(a)
    with when("the project publishes each rule"):
        codes = [main(["rules", "publish", rid, "--registry", str(reg)])
                 for rid in ("money.decimal", "style.nice", "lint.local")]
        errors = capsys.readouterr().err
    with then("only the rule with a reason and a data check is published, as a commit in the registry",
              expected="0, 2, 2; rules/money.decimal.yaml committed"):
        assert codes == [0, 2, 2]
        assert "has no `check:`" in errors and "runs a `command`" in errors
        log = subprocess.run(["git", "-C", str(reg), "log", "--oneline"], capture_output=True, text=True).stdout
        assert "Rule money.decimal" in log
        card = yaml.safe_load((reg / "rules" / "money.decimal.yaml").read_text())
        assert card["check"] == {"forbid_pattern": "float\\(", "paths": ["shop/**"]} and "source" not in card

    url = reg.as_uri()          # file:// — fetched by cloning, as from a hosted repository
    with when("another project searches the registry by directory and by git URL, and adds the rule"):
        b = repo(tmp_path / "b", {"requirements.yaml": PROJECT_B, "shop/money.py": "x = 1\n"})
        monkeypatch.chdir(b)
        capsys.readouterr()
        main(["rules", "search", "money", "--registry", str(reg)])
        by_dir = capsys.readouterr().out
        main(["rules", "search", "float", "money", "--registry", url])
        by_url = capsys.readouterr().out
        added = main(["rules", "add", "money.decimal", "--registry", url])
        text = (b / "requirements.yaml").read_text()
    with then("both find it, with how it is checked", expected="money.decimal … checked by: no match for `float\\(`"):
        assert "money.decimal" in by_dir and "money.decimal" in by_url
        assert "checked by: no match for `float\\(` in added lines in shop/**" in by_url
        assert list((tmp_path / "home" / "registries").iterdir()), "the URL was cloned into the cache"
    with and_("the rule is added with its check and its source, and the file's own text is untouched"):
        assert added == 0
        data = yaml.safe_load(text)
        [entry] = [e for e in data["requirements"] if e["id"] == "money.decimal"]
        assert entry["check"]["forbid_pattern"] == "float\\(" and entry["source"].startswith(url + "@")
        assert text.startswith(PROJECT_B.split("\n\nadherence")[0]) and data["adherence"]["conflicts"]
        from specdeck.requirements import load
        assert load(b / "requirements.yaml").problems == []
        assert main(["rules", "add", "money.decimal", "--registry", url]) == 2   # already there

    with when("the publisher adds a rule to the registry, and the other project breaks the first one"):
        monkeypatch.chdir(a)
        write(a, {"requirements.yaml": PROJECT_A.replace("  - id: style.nice", """\
  - id: deps.none
    statement: No dependency shall be added without a review.
    rationale: Every dependency so far has broken an install on the till.
    check: {no_new_dependencies: true}
  - id: style.nice""")})
        main(["rules", "publish", "deps.none", "--registry", str(reg)])
        monkeypatch.chdir(b)
        capsys.readouterr()
        main(["rules", "search", "--registry", url])
        refreshed = capsys.readouterr().out
        write(b, {"shop/money.py": "x = float(1)\n"})
        broken = main(["check", "--fail"])
    with then("the next search through the URL sees the new rule", expected="deps.none listed"):
        assert "deps.none" in refreshed
    with and_("the added rule catches the break in the second project", expected="exit 1"):
        assert broken == 1
