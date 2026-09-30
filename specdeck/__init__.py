"""specdeck — the spec is generated from the tests, never written by hand.

Public API (everything else lives in :mod:`specdeck.plugin`):

    from specdeck import scenario, given, when, then

    @scenario("replay-offline", group="replay", depends_on=["capture-toy"])
    def test_replay(tape):
        with given("a tape captured from the toy SDK"):
            player = TapePlayer(tape)
        with when("the handler is replayed with no network"):
            out = replay(player)
        with then("the reply comes from the tape", expected="pong: !ping"):
            assert out.content == "pong: !ping"

Steps are recorded *as they execute*, so a step appears in the spec only if the
test actually ran it. A failing ``then`` records ``expected_label`` /
``observed_label`` (and any ``decision_context``) into the spec's assertion block.
Un-annotated tests still become scenarios, with metadata derived from the module,
function name and docstring — so an existing suite gets a spec with zero edits.
"""
from .plugin import __version__, and_, given, scenario, step, then, when
from .requirements import RequirementError, example, examples

__all__ = ["scenario", "given", "when", "then", "and_", "step", "example", "examples", "RequirementError",
           "__version__"]
