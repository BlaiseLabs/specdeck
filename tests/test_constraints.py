"""specdeck's design constraints (ISO/IEC/IEEE 29148:2018 §9.6.16), checked against its own source."""
import ast
import sys
import tomllib
from pathlib import Path

from specdeck import and_, given, scenario, then, when

ROOT = Path(__file__).resolve().parent.parent


@scenario("constraint-dependencies", group="constraints", tags=["constraint"], covers=["constraint.dependencies"])
def test_specdeck_installs_with_pytest_and_pyyaml_alone():
    """Only pytest and PyYAML are declared, and every import at the top of a specdeck module is one
    of those or the standard library. Anything else is imported where its feature runs."""
    with given("specdeck's pyproject.toml and its modules"):
        declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
        modules = sorted((ROOT / "specdeck").glob("*.py"))
    with when("the declared dependencies and the module-level imports are read"):
        names = {d.split(">")[0].split("=")[0].split("<")[0].strip().lower() for d in declared}
        outside = []
        for m in modules:
            for node in ast.parse(m.read_text()).body:
                if isinstance(node, ast.Import):
                    tops = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    tops = [(node.module or "").split(".")[0]]
                else:
                    continue
                outside += [f"{m.name}: {t}" for t in tops
                            if t not in sys.stdlib_module_names and t not in ("pytest", "yaml", "specdeck")]
    with then("the declared dependencies are pytest and PyYAML", expected="{'pytest', 'pyyaml'}"):
        assert names == {"pytest", "pyyaml"}
    with and_("no module imports anything else when it is loaded", expected="[]"):
        assert outside == []
