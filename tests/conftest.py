import json
from pathlib import Path

import pytest
import yaml

pytest_plugins = ["pytester"]

# The plugin registers itself under the entry-point name "specdeck". Inner pytester
# runs block that name and load the module explicitly, so the tests behave the
# same whether or not the package is pip-installed.
PLUGIN_ARGS = ("-p", "no:specdeck", "-p", "specdeck.plugin")


@pytest.fixture
def run(pytester):
    def _run(*args):
        result = pytester.runpytest(*PLUGIN_ARGS, *args)
        spec_path = pytester.path / ".specdeck" / "spec.yaml"
        needs_path = pytester.path / ".specdeck" / "needs.json"
        spec = yaml.safe_load(spec_path.read_text()) if spec_path.exists() else None
        needs = json.loads(needs_path.read_text()) if needs_path.exists() else None
        return result, spec, needs

    return _run


def scenario_by_id(spec: dict, sid: str) -> dict:
    for s in spec["scenarios"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"{sid} not in {[s['id'] for s in spec['scenarios']]}")
