# tapedeck — the test suite, for an agent


## The test files

Verbatim, for when the question is how a test is wired rather than what it checks.

### tests/conftest.py

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKES = Path(__file__).resolve().parent / "_fakes"

for p in (str(ROOT), str(FAKES)):
    if p not in sys.path:
        sys.path.insert(0, p)

```
### tests/test_cli.py

```python
"""CLI tests — all offline. `record` uses the toy fakecord SDK; the rest run
against the integrated golden tape (skipped if it's missing)."""
import json
import sys
import textwrap
from pathlib import Path

import pytest
from specdeck import and_, given, scenario, then, when

from tapedeck.cli import main

TAPE = Path(__file__).resolve().parent / "fixtures_data" / "integrated.tape.json"
needs_tape = pytest.mark.skipif(not TAPE.exists(), reason="integrated golden tape missing")


def run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def _drop_fakecord():
    for mod in list(sys.modules):
        if mod == "fakecord" or mod.startswith("fakecord."):
            del sys.modules[mod]


@scenario("cli-record-outbound", tags=["cli"], depends_on=["capture-toy-chained"])
def test_record_captures_toy_script(tmp_path, capsys):
    """`tapedeck record` wraps an unmodified script and captures its outbound SDK calls."""
    with given("a plain script that uses the toy SDK and contains no tapedeck code"):
        script = tmp_path / "drive.py"
        script.write_text(
            textwrap.dedent(
                """
                import asyncio, fakecord
                from fakecord.ext import commands
                bot = commands.Bot(prefix="!", token="secrettoken12345")
                ch = bot.get_channel(7)
                asyncio.run(ch.send("hi"))
                """
            )
        )
        out_tape = tmp_path / "toy.tape.json"
        _drop_fakecord()
    with when("it is run under `tapedeck record fakecord <script>`"):
        rc, out = run(["record", "fakecord", str(script), "-o", str(out_tape)], capsys)
    with then("the command succeeds and writes a tape", expected="exit 0, tape file exists"):
        assert rc == 0
        assert out_tape.exists()
    with and_("the chained get_channel().send() call is on the tape"):
        tape = json.loads(out_tape.read_text())
        assert any("get_channel_result" in c for c in tape["mocked_modules"])
    with and_("the token passed to the constructor was redacted"):
        assert "secrettoken12345" not in out_tape.read_text()


@scenario(
    "cli-record-inbound-auto-tap",
    tags=["cli", "wedge", "zero-code-change"],
    depends_on=["cli-record-outbound", "capture-toy-timeline"],
)
def test_record_captures_inbound_events_zero_code_change(tmp_path, capsys):
    """Auto-tap: a plain script (no tapedeck imports) constructs a gateway bot,
    events are pushed to it, and `record` captures them as inbound entries."""
    with given("a plain bot script that constructs a gateway client and handles pushed events"):
        script = tmp_path / "bot_script.py"
        script.write_text(
            textwrap.dedent(
                """
                import asyncio
                import fakecord
                from fakecord.ext import commands

                bot = commands.Bot(prefix="!")

                @bot.event
                async def on_message(message):
                    ch = bot.get_channel(5)
                    await ch.send("pong: " + message.content)

                incoming = fakecord.Message(id=1, content="hi", channel_id=5, author="human")
                asyncio.run(bot.simulate([("ready", ()), ("message", (incoming,))]))
                """
            )
        )
        out_tape = tmp_path / "bot.tape.json"
        _drop_fakecord()
    with when("it is run under `tapedeck record` with no code changes"):
        rc, out = run(["record", "fakecord", str(script), "-o", str(out_tape)], capsys)
        assert rc == 0
        tape = json.loads(out_tape.read_text())
        inbound = [e for e in tape["timeline"] if e["dir"] == "in"]
    with then("the pushed 'ready' and 'message' events are captured as inbound entries", expected="fakecord:ready, fakecord:message"):
        assert {(e["source"], e["event"]) for e in inbound} >= {("fakecord", "ready"), ("fakecord", "message")}
    with and_("the handler's chained reply is recorded after the event that caused it"):
        sends = [e for e in tape["timeline"] if e["dir"] == "out" and e["call"].endswith("get_channel_result.send")]
        msg_seq = next(e["seq"] for e in inbound if e["event"] == "message")
        assert sends and sends[0]["seq"] > msg_seq


@needs_tape
@scenario("cli-inspect", tags=["cli"], depends_on=["wedge-tape-three-sdks"])
def test_inspect_lists_sdks_and_events(capsys):
    """`tapedeck inspect` summarises a tape: SDKs, inbound events, outbound calls."""
    with given("the integrated golden tape"):
        pass
    with when("`tapedeck inspect <tape>` runs"):
        rc, out = run(["inspect", str(TAPE)], capsys)
    with then("it succeeds and names all three SDKs", expected="discord, openai, twitchio in output"):
        assert rc == 0
        assert "discord" in out and "openai" in out and "twitchio" in out
    with and_("it lists the inbound gateway events"):
        assert "inbound events" in out


@needs_tape
@scenario("cli-verify-gate", tags=["cli", "ci"], depends_on=["wedge-tape-three-sdks", "wedge-tape-no-secrets"])
def test_verify_passes_on_golden_tape(capsys):
    """`tapedeck verify` is the CI gate: valid, secret-clean, replayable → exit 0."""
    with given("the committed integrated golden tape"):
        pass
    with when("`tapedeck verify <tape>` runs"):
        rc, out = run(["verify", str(TAPE)], capsys)
    with then("the gate passes", expected="exit 0 and 'OK'"):
        assert rc == 0
        assert "OK" in out


@scenario("cli-verify-detects-secret", tags=["cli", "security"], depends_on=["cli-verify-gate"])
def test_verify_fails_on_planted_secret(tmp_path, capsys):
    """A tape containing a credential-shaped value fails the gate with a non-zero exit."""
    with given("an otherwise valid tape with an 'oauth:' token planted in a return value"):
        bad = tmp_path / "bad.tape.json"
        bad.write_text(json.dumps({
            "session": {}, "metadata": {},
            "timeline": [{"seq": 0, "dir": "out", "call": "m.f"}],
            "mocked_modules": {"m": {"methods": {"f": {"return_value": "oauth:abcdef"}}}},
        }))
    with when("`tapedeck verify` runs on it"):
        rc, out = run(["verify", str(bad)], capsys)
    with then("the gate fails and names the problem", expected="exit 1, 'secret' in output"):
        assert rc == 1
        assert "secret" in out.lower()


@scenario("cli-diff", tags=["cli", "drift"], depends_on=["drift-identical"])
def test_diff_reports_drift_and_exit_code(tmp_path, capsys):
    """`tapedeck diff` prints the drift report and exits 1 only when drift exists."""
    with given("a tape and a copy of it with one extra recorded method"):
        base = {
            "session": {}, "metadata": {},
            "timeline": [{"seq": 0, "dir": "out", "call": "m.C_result.f"}],
            "mocked_modules": {"m.C_result": {"methods": {"f": {"async": False, "return_value": 1}}}},
        }
        a = tmp_path / "a.json"; a.write_text(json.dumps(base))
        b = tmp_path / "b.json"
        changed = json.loads(json.dumps(base))
        changed["mocked_modules"]["m.C_result"]["methods"]["g"] = {"async": False, "return_value": 2}
        b.write_text(json.dumps(changed))
    with when("a tape is diffed against itself"):
        same = run(["diff", str(a), str(a)], capsys)
    with then("no drift is reported and the exit code is 0", expected="(0, 'no drift — the recording is still true')"):
        assert same == (0, "no drift — the recording is still true\n")
    with when("it is diffed against the changed copy"):
        rc, out = run(["diff", str(a), str(b)], capsys)
    with then("the added method is reported and the exit code is 1", expected="exit 1, 'method added'"):
        assert rc == 1 and "method added" in out


@needs_tape
@scenario("cli-publish-pull", tags=["cli", "tape-library"], depends_on=["registry-publish-pull"])
def test_publish_then_pull_via_cli(tmp_path, capsys):
    """`tapedeck publish` adds a tape to a library; `tapedeck pull` fetches it back."""
    with given("an empty local tape library"):
        registry = tmp_path / "lib"
    with when("the golden tape is published as 'integrated'"):
        rc, _ = run(["publish", str(TAPE), str(registry), "integrated"], capsys)
        assert rc == 0
        published = (registry / "tapes" / "integrated.tape.json").resolve().as_uri()
    with and_("then pulled back by URL to a new path"):
        dest = tmp_path / "pulled.tape.json"
        rc, out = run(["pull", published, "-o", str(dest)], capsys)
    with then("the pull succeeds and the tape is written", expected="exit 0, file exists, SDKs listed"):
        assert rc == 0 and dest.exists()
        assert "discord" in out


@needs_tape
@scenario("cli-play", tags=["cli", "replay"], depends_on=["wedge-discord-replay-offline"])
def test_play_replays_discord_via_handler_file(tmp_path, capsys):
    """`tapedeck play` pumps recorded inbound events through a handler module, offline."""
    with given("a handler module defining make_handlers(client)"):
        handlers = tmp_path / "h.py"
        handlers.write_text(
            textwrap.dedent(
                """
                def make_handlers(client):
                    async def on_message(message):
                        ch = client.get_channel(0)
                        await ch.send("x")
                    return {"on_message": on_message}
                """
            )
        )
    with when("`tapedeck play` replays the golden tape's discord events through it"):
        rc, out = run(
            ["play", str(TAPE), "--source", "discord",
             "--client", "discord.Client_result", "--handlers", str(handlers)],
            capsys,
        )
    with then("it succeeds and reports pumped events", expected="exit 0, 'pumped' in output"):
        assert rc == 0
        assert "pumped" in out

```
### tests/test_dictlike_responses.py

```python
"""Dict-like SDK responses must survive capture AND replay.

Regression tests for three real bugs found capturing slack_sdk, whose
SlackResponse is iterable (pagination) *and* a mapping (``resp["ok"]``):

  1. _is_stream() saw __next__ and consumed it as an SSE stream.
  2. ObjectProxy had no __getitem__, so resp["ok"] raised TypeError on capture.
  3. TapeValue had no __getitem__, so the same access failed on replay.

Python resolves dunders on the TYPE, so __getattr__ forwarding is not enough —
these protocols must be declared explicitly.
"""
import os
import sys
import tempfile

from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp, NoNetwork, TapePlayer
from tapedeck.engine.proxies import _is_stream


class _PaginatedResponse:
    """Mirrors slack_sdk.SlackResponse: mapping + iterable for pagination."""

    def __init__(self, data):
        self.data = data
        self._done = False

    def __getitem__(self, key):
        return self.data[key]

    def __iter__(self):
        return self

    def __next__(self):
        if self._done:
            raise StopIteration
        self._done = True
        return self


class _PureStream:
    """Mirrors anthropic/openai Stream: consumable, NOT indexable."""

    def __init__(self, items):
        self._it = iter(items)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)


@scenario("proxy-stream-vs-mapping", tags=["proxy-fidelity"], depends_on=["stream-sync-capture"])
def test_indexable_iterable_is_not_treated_as_a_stream():
    """Only pure consumable iterators count as streams; indexable responses are data."""
    with given("a Slack-style response (iterable AND indexable) and a pure LLM stream"):
        paginated = _PaginatedResponse({"ok": True})
        stream = _PureStream([1, 2])
    with then("the indexable response is not treated as a stream", expected="_is_stream == False"):
        assert _is_stream(paginated) is False
    with and_("the pure iterator still is", expected="_is_stream == True"):
        assert _is_stream(stream) is True


def _drop():
    for m in list(sys.modules):
        if m == "fakeweb" or m.startswith("fakeweb."):
            del sys.modules[m]


@scenario(
    "proxy-dictlike-roundtrip",
    tags=["proxy-fidelity", "replay"],
    depends_on=["proxy-stream-vs-mapping", "replay-toy-offline"],
)
def test_dictlike_response_roundtrips_through_capture_and_replay(tmp_path):
    """resp["key"] works on a captured response and again on its replayed value."""
    mod = tmp_path / "fakeweb.py"
    mod.write_text(
        "class Response:\n"
        "    def __init__(self, data):\n"
        "        self.data = data\n"
        "    def __getitem__(self, key):\n"
        "        return self.data[key]\n"
        "    def __iter__(self):\n"
        "        return iter([self])\n"
        "class Client:\n"
        "    def __init__(self, token=None):\n"
        "        self.token = token\n"
        "    def auth_test(self):\n"
        "        return Response({'ok': True, 'team': 'T1', 'user': 'u1'})\n"
    )
    sys.path.insert(0, str(tmp_path))
    _drop()
    app = GatewayApp()
    session = app.capture_session(["fakeweb"])
    try:
        with given("a proxied toy web client whose responses are dict-like and iterable"):
            import fakeweb

            client = fakeweb.Client(token="secret-token-abcdef")
        with when("auth_test() is called through the proxy"):
            resp = client.auth_test()
        with then("the captured response is still indexable", expected="resp['ok'] is True, resp['team'] == 'T1'"):
            assert resp["ok"] is True
            assert resp["team"] == "T1"
        tape = app.export_tape(session, str(tmp_path / "w.tape.json"), {"sdk": "fakeweb"})
    finally:
        app.uninstall(session)
        _drop()
        sys.path.remove(str(tmp_path))

    with when("the same call is replayed from the tape with the network blocked"):
        c = TapePlayer(tape).object_at("fakeweb.Client_result")
        with NoNetwork():
            r = c.auth_test()
    with then("the replayed value is indexable too", expected="r['ok'] is True, r['team'] == 'T1'"):
        assert r["ok"] is True
        assert r["team"] == "T1"
    with and_("membership and .get() behave like a mapping"):
        assert "team" in r
        assert r.get("missing", "fallback") == "fallback"

```
### tests/test_drift.py

```python
"""diff_tapes: the drift check that turns a static tape into a living contract."""
import copy

from specdeck import and_, given, scenario, then, when

from tapedeck import diff_tapes


def _tape():
    return {
        "timeline": [
            {"seq": 0, "dir": "in", "source": "discord", "event": "message"},
            {"seq": 1, "dir": "out", "call": "m.C_result.send"},
        ],
        "mocked_modules": {
            "m.C_result": {"methods": {"send": {"async": True, "return_value": {"__type__": "Msg", "id": 1}}}}
        },
        "nested_schemas": {"Msg": {"id": "int"}},
    }


@scenario("drift-identical", tags=["drift"], depends_on=["capture-toy-chained"])
def test_identical_tapes_have_no_drift():
    """Re-recording the same behaviour reports no drift — the recording is still true."""
    with given("a tape and a byte-identical re-recording of it"):
        t = _tape()
        again = copy.deepcopy(t)
    with when("the two are diffed"):
        drift = diff_tapes(t, again)
    with then("no drift is reported", expected="empty drift report"):
        assert drift == []


@scenario("drift-removed-method", tags=["drift"], depends_on=["drift-identical"])
def test_detects_removed_method():
    """An SDK method that stops being called (or stops existing) is reported as removed."""
    with given("a re-recording where the 'send' method is no longer on the tape"):
        old, new = _tape(), _tape()
        del new["mocked_modules"]["m.C_result"]["methods"]["send"]
    with when("the tapes are diffed"):
        drift = diff_tapes(old, new)
    with then("the report names the removed method", expected="'method removed' entry"):
        assert any("method removed" in d for d in drift)


@scenario("drift-return-shape", tags=["drift"], depends_on=["drift-identical"])
def test_detects_return_shape_change():
    """A new field in an SDK response is drift — the shape the code relies on changed."""
    with given("a re-recording where send() now returns an extra 'flags' field"):
        old, new = _tape(), _tape()
        new["mocked_modules"]["m.C_result"]["methods"]["send"]["return_value"] = {
            "__type__": "Msg",
            "id": 1,
            "flags": 0,  # new field the SDK started returning
        }
    with when("the tapes are diffed"):
        drift = diff_tapes(old, new)
    with then("the report flags the changed return shape", expected="'return shape changed' entry"):
        assert any("return shape changed" in d for d in drift)


@scenario("drift-inbound-events", tags=["drift", "gateway"], depends_on=["drift-identical"])
def test_detects_new_and_missing_inbound_events():
    """Gateway events that vanish or newly appear are reported by source and name."""
    with given("a re-recording where 'message' no longer arrives but 'typing' does"):
        old, new = _tape(), _tape()
        new["timeline"][0]["event"] = "typing"
    with when("the tapes are diffed"):
        drift = diff_tapes(old, new)
    with then("the missing event is reported", expected="no longer seen: discord:message"):
        assert any("no longer seen: discord:message" in d for d in drift)
    with and_("the new event is reported", expected="new inbound event: discord:typing"):
        assert any("new inbound event: discord:typing" in d for d in drift)


@scenario("drift-schema-fields", tags=["drift"], depends_on=["drift-identical"])
def test_detects_schema_field_changes():
    """A recorded object type growing a field is drift in the extracted schema."""
    with given("a re-recording where the Msg schema gained an 'author' field"):
        old, new = _tape(), _tape()
        new["nested_schemas"]["Msg"]["author"] = "str"
    with when("the tapes are diffed"):
        drift = diff_tapes(old, new)
    with then("the added field is reported", expected="'fields added' entry for Msg"):
        assert any("fields added" in d for d in drift)

```
### tests/test_integrated_replay.py

```python
"""Offline replay of the integrated golden tape (OpenAI + Discord + Twitch).

This is the CI-facing half of the wedge demo: it needs no network and no
credentials. It replays the tape captured live by tests/_live/capture_integrated.py
and asserts the three SDKs replay from recorded ground truth — including the
Discord gateway `message` event, which HTTP-level recorders cannot capture.

Regenerate the tape with real credentials:
    python tests/_live/capture_integrated.py
"""
import json
from pathlib import Path

import pytest
from specdeck import and_, given, scenario, then, when

from tapedeck import NoNetwork, TapePlayer, diff_transcript, load_tape

TAPE = Path(__file__).resolve().parent / "fixtures_data" / "integrated.tape.json"

pytestmark = pytest.mark.skipif(
    not TAPE.exists(),
    reason="integrated golden tape missing — run tests/_live/capture_integrated.py",
)

DISCORD_CLIENT = "discord.Client_result"


@pytest.fixture(scope="module")
def tape():
    return load_tape(str(TAPE))


@scenario("wedge-tape-three-sdks", tags=["wedge", "golden-tape"], depends_on=["replay-toy-offline"])
def test_tape_has_all_three_sdks(tape):
    """The golden tape was captured live from all three SDKs in one session."""
    with given("the committed integrated golden tape"):
        containers = set(tape["mocked_modules"])
    with then("openai, discord and twitchio all recorded calls", expected="containers for all three"):
        assert any(c.startswith("openai") for c in containers)
        assert any(c.startswith("discord") for c in containers)
        assert any(c.startswith("twitchio") for c in containers)


@scenario("wedge-discord-inbound", tags=["wedge", "gateway"], depends_on=["wedge-tape-three-sdks"])
def test_real_gateway_inbound_events_present(tape):
    """The wedge: server-pushed Discord gateway events an HTTP recorder never sees."""
    with given("the inbound events recorded from the real Discord gateway"):
        discord_events = {
            e["event"] for e in tape["timeline"] if e["dir"] == "in" and e["source"] == "discord"
        }
    with then("a real 'message' push is on the tape", expected="message in inbound events"):
        assert "message" in discord_events
    with and_("so is 'ready'"):
        assert "ready" in discord_events


@scenario("wedge-twitch-inbound", tags=["wedge", "gateway"], depends_on=["wedge-tape-three-sdks"])
def test_twitch_eventsub_inbound_present(tape):
    """Wedge proof #2: Twitch EventSub websocket pushes (chat.message + welcome)."""
    with given("the inbound events recorded from the real Twitch EventSub socket"):
        twitch_events = {
            e["event"] for e in tape["timeline"] if e["dir"] == "in" and e["source"] == "twitchio"
        }
    with then("the socket welcome was captured", expected="websocket_welcome"):
        assert "websocket_welcome" in twitch_events
    with and_("our own chat line echoed back as a 'message' event", expected="message"):
        assert "message" in twitch_events


@scenario("wedge-openai-replays", tags=["wedge", "llm"], depends_on=["wedge-tape-three-sdks"])
def test_openai_completion_replays(tape):
    """The recorded mini-model completion replays offline through the normal client chain."""
    with given("a tape-backed OpenAI client"):
        client = TapePlayer(tape).object_at("openai.OpenAI_result")
    with when("chat.completions.create() is called with no network"):
        resp = client.chat.completions.create()
        content = resp.choices[0].message.content
    with then("the recorded reply text comes back", expected="non-empty recorded string"):
        assert isinstance(content, str) and content


@scenario("wedge-tape-no-secrets", tags=["wedge", "security"], depends_on=["wedge-tape-three-sdks"])
def test_no_secrets_in_tape(tape):
    """Redaction ran and no value-shaped credential survived on the committed tape."""
    with given("the committed tape as raw JSON"):
        raw = json.dumps(tape)
    with then("redaction markers are present", expected="[REDACTED] in tape"):
        assert "[REDACTED]" in raw
    with and_("no credential-shaped value appears (redacted key *names* like client_secret are fine)"):
        for needle in ("oauth:", "sk-proj-", "sk-ant-", "Bearer eyJ"):
            assert needle not in raw


@scenario(
    "wedge-discord-replay-offline",
    tags=["wedge", "gateway", "replay"],
    depends_on=["wedge-discord-inbound", "replay-toy-offline"],
)
def test_discord_gateway_message_replays_offline(tape):
    """Feed the recorded Discord `message` event into a bot handler with no network."""
    seen = {}

    def make_handlers(client):
        async def on_message(message):
            # message.content is real recorded gateway data; get_channel/send
            # resolve by tape path (the arg value is not used for resolution).
            seen["content"] = message.content
            channel = client.get_channel(0)
            reply = await channel.send("ignored on replay")
            seen["reply"] = reply.content

        return {"on_message": on_message}

    from tapedeck import GatewayApp

    with given("a handler that echoes the recorded message through a tape-backed client"):
        app = GatewayApp()
    with when("the recorded inbound events are pumped with the network blocked"):
        with NoNetwork():
            result = app.replay_session(tape, "discord", make_handlers, DISCORD_CLIENT)
    with then("at least one event reached the handler", expected=">= 1 event pumped"):
        assert result["events_pumped"] >= 1
    with and_("the handler saw the real recorded message content"):
        assert seen["content"]
    with and_(
        "send() returned the RECORDED reply, not the handler's argument",
        expected="reply == the line the bot originally sent",
        decision_context=[("tape", "recorded return value"), ("handler argument", "ignored")],
    ):
        assert seen["reply"] == seen["content"]
    with and_("every replayed outbound call was one actually recorded on the tape"):
        for call in result["transcript"]:
            assert call["call"].startswith("discord.")


@scenario("wedge-twitch-send-replays", tags=["wedge", "replay"], depends_on=["wedge-twitch-inbound"])
def test_twitch_send_message_resolves_from_tape(tape):
    """The Helix send_message call resolves from the tape through the partial-user chain."""
    import asyncio

    with given("a tape-backed Twitch client"):
        player = TapePlayer(tape)
        client = player.object_at("twitchio.Client_result")
    with when("create_partialuser().send_message() is awaited offline"):
        pu = client.create_partialuser()
        asyncio.run(pu.send_message())  # recorded async → returns a coroutine
    with then("send_message appears in the replay transcript", expected="…send_message in transcript"):
        assert any(t["call"].endswith("send_message") for t in player.transcript)

```
### tests/test_registry.py

```python
"""Publish → index → pull round-trip against a local tape library (offline).

Mirrors the PythonSDKMocks "tape library": publish copies a validated tape into a
`tapes/` dir and updates `tapes/index.json`; pull downloads by name/URL. Here the
"download" is served from a file:// URL so the test needs no network.
"""
import json
from pathlib import Path

import pytest
from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp

SRC = Path(__file__).resolve().parent / "fixtures_data" / "integrated.tape.json"

pytestmark = pytest.mark.skipif(not SRC.exists(), reason="integrated golden tape missing")


@scenario("registry-publish-pull", tags=["tape-library"], depends_on=["wedge-tape-three-sdks"])
def test_publish_then_pull_roundtrip(tmp_path):
    """A tape published to a local library is indexed and can be pulled back intact."""
    with given("an empty local tape library and the integrated golden tape"):
        registry = tmp_path / "PythonSDKMocks"
        app = GatewayApp()
    with when("the tape is published under the name 'integrated'"):
        entry = app.publish_tape(str(SRC), str(registry), "integrated")
    with then("the index entry describes the tape", expected="sdks include discord/openai/twitchio, >=1 inbound event"):
        assert set(entry["sdks"]) >= {"discord", "openai", "twitchio"}
        assert entry["inbound_events"] >= 1
    with and_("the library holds both the index and the tape file"):
        index = json.loads((registry / "tapes" / "index.json").read_text())
        assert "integrated" in index
        published = registry / "tapes" / "integrated.tape.json"
        assert published.exists()
    with when("the published tape is pulled back by URL"):
        dest = tmp_path / "pulled.tape.json"
        tape = app.pull_tape(published.resolve().as_uri(), str(dest))
    with then("the pulled tape is written and identical in content", expected="same mocked_modules as the source"):
        assert dest.exists()
        assert set(tape["mocked_modules"]) == set(json.loads(SRC.read_text())["mocked_modules"])

```
### tests/test_rich_client_capture.py

```python
"""Regressions for two bugs that produced a silently-incomplete Telegram tape.

1. Callable CLIENT objects were routed to CallableWrapper (added for Slack's
   parameterized decorators), which proxies no attributes — so aiogram's Bot,
   which defines __call__, lost every method call: `bot.send_message(...)` ran
   but was never recorded. Only genuine functions belong in CallableWrapper;
   rich objects stay ObjectProxy (which forwards __call__ itself).

2. Snapshot truncation dropped the payload: pydantic-style models declare huge
   field counts (aiogram Message: 127 fields, ~5 populated) and `text` sat past
   the cap, so captured messages had no body. Populated fields now come first.
"""
import sys

from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp
from tapedeck.engine.serialize import Redactor, snapshot


class _ManyFieldModel:
    """Mirrors a pydantic SDK model: mostly-None optional fields, data late."""

    def __init__(self):
        for i in range(60):
            setattr(self, f"opt_{i:02d}", None)
        self.text = "the payload"  # declared last, would be truncated before
        self.chat_id = 99


@scenario("proxy-callable-client", tags=["proxy-fidelity"], depends_on=["slack-decorator-callable"])
def test_callable_client_keeps_attribute_proxying(tmp_path):
    """A client that defines __call__ (like aiogram's Bot) keeps recording its method calls."""
    mod = tmp_path / "fakeclient.py"
    mod.write_text(
        "class Bot:\n"
        "    def __init__(self, token=None):\n"
        "        self.token = token\n"
        "    def __call__(self, method):\n"
        "        return {'invoked': method}\n"
        "    def send_message(self, chat_id, text):\n"
        "        return {'ok': True, 'chat_id': chat_id, 'text': text}\n"
    )
    sys.path.insert(0, str(tmp_path))
    for m in [m for m in sys.modules if m.startswith("fakeclient")]:
        del sys.modules[m]
    app = GatewayApp()
    session = app.capture_session(["fakeclient"])
    try:
        with given("a proxied client class that is itself callable"):
            import fakeclient

            bot = fakeclient.Bot(token="secret-token-123456")
        with then("the instance is an attribute-proxying ObjectProxy, not a bare CallableWrapper", expected="ObjectProxy"):
            assert type(bot).__name__ == "ObjectProxy"
        with when("a method and the object itself are both called"):
            resp = bot.send_message(chat_id=1, text="hi")
            invoked = bot("some.method")
        with and_("both calls work as they would on the real object"):
            assert resp["ok"] is True
            assert invoked["invoked"] == "some.method"
        tape = app.export_tape(session, str(tmp_path / "c.tape.json"), {"sdk": "fakeclient"})
    finally:
        app.uninstall(session)
        sys.path.remove(str(tmp_path))
        for m in [m for m in sys.modules if m.startswith("fakeclient")]:
            del sys.modules[m]

    with then("the method call is on the tape (the bug: it silently was not)", expected="fakeclient.Bot_result.send_message recorded"):
        calls = [e["call"] for e in tape["timeline"] if e["dir"] == "out"]
        assert "fakeclient.Bot_result.send_message" in calls
    with and_("the __call__ invocation is recorded too"):
        assert any(c.endswith("__call__") for c in calls)


@scenario("snapshot-populated-fields-first", tags=["proxy-fidelity"], depends_on=["capture-toy-chained"])
def test_populated_fields_survive_truncation():
    """Snapshotting a model with dozens of None fields keeps the few that carry data."""
    with given("a pydantic-style model with 60 None fields and the payload declared last"):
        model = _ManyFieldModel()
    with when("it is snapshotted under the field cap"):
        snap = snapshot(model, Redactor([]), {})
    with then("the populated fields survive", expected="text == 'the payload', chat_id == 99"):
        assert snap["text"] == "the payload"
        assert snap["chat_id"] == 99

```
### tests/test_slack_shape_tap.py

```python
"""Auto-tap generalizes to Slack Bolt's dispatch shape — proven offline against
a toy 'fakebolt' SDK before any real Slack app setup (same de-risking pattern as
test_stream_roundtrip.py, which proved streaming ahead of Anthropic credentials).

Two things real discord.py-shaped fakes never exercised:
  1. dispatch(req) takes ONE rich object, not (event_name: str, *rest).
  2. @app.event("message") is a parameterized decorator (a callable that returns
     a callable) — requires wrapped results to stay callable.
"""
import os
import sys
import tempfile

from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp
from tapedeck.engine.proxies import unwrap


def _drop():
    for m in list(sys.modules):
        if m == "fakebolt" or m.startswith("fakebolt."):
            del sys.modules[m]


@scenario("slack-decorator-callable", tags=["gateway-shape", "slack"], depends_on=["capture-toy-chained"])
def test_parameterized_decorator_stays_callable():
    """@app.event("message") — a decorator factory — still works through the proxy."""
    _drop()
    app = GatewayApp()
    session = app.capture_session(["fakebolt"])
    try:
        with given("a proxied Slack-Bolt-shaped App"):
            import fakebolt

            bolt_app = fakebolt.App(token="xoxb-fake")
        with when("a handler is registered with the parameterized decorator @app.event('message')"):

            @bolt_app.event("message")  # this line used to crash before the fix
            def on_message(event):
                return event["text"]

        with then("registration succeeds (no 'ObjectProxy is not callable') and the handler is stored"):
            assert "message" in unwrap(bolt_app)._handlers
    finally:
        app.uninstall(session)
        _drop()


@scenario(
    "slack-single-object-dispatch",
    tags=["gateway-shape", "slack", "wedge"],
    depends_on=["slack-decorator-callable", "capture-toy-timeline"],
)
def test_single_object_dispatch_captures_readable_event_and_chained_reply():
    """dispatch(req) with ONE rich request object yields a readable event label and full payload."""
    _drop()
    app = GatewayApp()
    session = app.capture_session(["fakebolt"])
    try:
        with given("a proxied Bolt app whose 'message' handler replies via the proxied client"):
            import fakebolt

            bolt_app = fakebolt.App(token="xoxb-fake")

            @bolt_app.event("message")
            def on_message(event):
                bolt_app.client.chat_postMessage(channel="C123", text="pong: " + event["text"])

        with when("the SDK delivers a single BoltRequest through its internal dispatch path"):
            real = unwrap(bolt_app)
            req = fakebolt.BoltRequest(
                body={"type": "event_callback", "event": {"type": "message", "text": "hi"}}
            )
            real.deliver(req)  # SDK-internal invocation path, like SocketModeHandler
        out = os.path.join(tempfile.mkdtemp(), "bolt.tape.json")
        tape = app.export_tape(session, out, {"sdk": "fakebolt"})
    finally:
        app.uninstall(session)
        _drop()

    inbound = [e for e in tape["timeline"] if e["dir"] == "in"]
    with then("exactly one inbound event is recorded with a label derived from the request body", expected="fakebolt:message"):
        assert len(inbound) == 1
        assert inbound[0]["event"] == "message"
        assert inbound[0]["source"] == "fakebolt"
    with and_("the whole request object is the payload (not just args after the first)"):
        assert inbound[0]["payload"]["body"]["event"]["text"] == "hi"
    with and_("the chained reply is recorded after the event that caused it", expected="chat_postMessage(channel=C123, text='pong: hi')"):
        sends = [
            e for e in tape["timeline"] if e["dir"] == "out" and e["call"].endswith("chat_postMessage")
        ]
        assert sends and sends[0]["args"] == []
        assert sends[0]["kwargs"] == {"channel": "C123", "text": "pong: hi"}
        assert sends[0]["seq"] > inbound[0]["seq"]

```
### tests/test_stream_roundtrip.py

```python
"""Streaming capture + offline replay (sync and async), on a toy LLM SDK.

Proves tapedeck records each SSE-style chunk of a streamed response and replays
the exact sequence offline — the capability the Anthropic tape needs and that
HTTP cassettes (vcrpy) fail on.
"""
import asyncio
import json
import os
import sys

import pytest
from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp, NoNetwork, TapePlayer
from tapedeck.engine.proxies import unwrap

CREATE = "fakellm.Client_result.messages.create"


def _drop():
    for m in list(sys.modules):
        if m == "fakellm" or m.startswith("fakellm."):
            del sys.modules[m]


def _capture(consume):
    """Install the hook, run `consume(module)`, return the tape."""
    _drop()
    os.environ["FAKELLM_KEY"] = "sk-fakellm-secret-123456"
    app = GatewayApp()
    session = app.capture_session(["fakellm"])
    try:
        import fakellm

        consume(fakellm)
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "llm.tape.json")
        return app.export_tape(session, path, {"sdk": "fakellm"})
    finally:
        app.uninstall(session)
        _drop()


@scenario("stream-sync-capture", tags=["streaming", "llm"], depends_on=["capture-toy-chained"])
def test_sync_stream_captures_chunks():
    """Each chunk of a streamed response is recorded as the app consumes it."""
    collected = []

    def consume(fakellm):
        client = fakellm.Client(api_key=os.environ["FAKELLM_KEY"])
        for event in client.messages.create(model="m", messages=[], stream=True):
            collected.append(getattr(unwrap(event), "text", unwrap(event).type))

    with given("a proxied toy LLM client"):
        pass
    with when("messages.create(stream=True) is iterated to exhaustion"):
        tape = _capture(consume)
        cfg = tape["mocked_modules"]["fakellm.Client_result.messages"]["methods"]["create"]
    with then("the call is marked as a stream", expected="stream: true"):
        assert cfg["stream"] is True
    with and_("every chunk was recorded", expected="6 chunks: message_start + 4 deltas + message_stop"):
        assert len(cfg["chunks"]) == 6
    with and_("chunks reached the app unchanged", expected="first delta == 'hello '"):
        assert collected[1] == "hello "
    with and_("the api key never landed on the tape"):
        assert "sk-fakellm-secret-123456" not in json.dumps(tape)


@scenario("stream-sync-replay", tags=["streaming", "llm", "replay"], depends_on=["stream-sync-capture"])
def test_sync_stream_replays_offline():
    """The recorded chunk sequence replays offline in the exact original order."""
    with given("a tape of a streamed completion"):
        tape = _capture(
            lambda fakellm: list(
                fakellm.Client(api_key="x").messages.create(model="m", messages=[], stream=True)
            )
        )
        client = TapePlayer(tape).object_at("fakellm.Client_result")
    with when("the stream is iterated again with the network blocked"):
        with NoNetwork():
            events = list(client.messages.create(model="m", messages=[], stream=True))
        texts = [getattr(e, "text", None) for e in events]
    with then("the start/stop markers replay without text", expected="2 textless chunks"):
        assert texts.count(None) == 2  # message_start + message_stop have no text
    with and_("the deltas reassemble the original text", expected="hello world from fakellm "):
        assert "".join(t for t in texts if t) == "hello world from fakellm "


@scenario("stream-async-roundtrip", tags=["streaming", "llm", "replay", "async"], depends_on=["stream-sync-replay"])
def test_async_stream_captures_and_replays():
    """Async streaming (await create(stream=True), then async-for) round-trips too."""

    def consume(fakellm):
        async def drive():
            client = fakellm.AsyncClient(api_key="x")
            stream = await client.messages.create(model="m", messages=[], stream=True)
            return [getattr(unwrap(e), "text", None) async for e in stream]

        asyncio.run(drive())

    with given("an async toy LLM client driven through a proxied capture"):
        tape = _capture(consume)
        cfg = tape["mocked_modules"]["fakellm.AsyncClient_result.messages"]["methods"]["create"]
    with then("the call is recorded as an async stream with all chunks", expected="stream+async, 6 chunks"):
        assert cfg["stream"] is True and cfg["async"] is True and len(cfg["chunks"]) == 6

    client = TapePlayer(tape).object_at("fakellm.AsyncClient_result")

    async def replay():
        with NoNetwork():
            stream = await client.messages.create(model="m", messages=[], stream=True)
            return [getattr(e, "text", None) async for e in stream]

    with when("the async stream is awaited and iterated offline"):
        texts = asyncio.run(replay())
    with and_("the deltas reassemble the original text", expected="hello world from fakellm "):
        assert "".join(t for t in texts if t) == "hello world from fakellm "

```
### tests/test_telegram_shape_tap.py

```python
"""Auto-tap generalizes to both Telegram conventions — proven offline before any
BotFather token exists (same de-risking pattern as the Slack and streaming work).

Neither aiogram nor python-telegram-bot has a `dispatch` method, so the original
hardcoded tap would have silently attached to NOTHING and captured zero inbound
events. These tests pin the two entry points and the label derivation:
  aiogram  feed_update(bot, update) -> event is args[1], label from .event_type
  PTB      process_update(update)   -> event is args[0], label from populated field
"""
import os
import sys
import tempfile

from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp
from tapedeck.engine.proxies import unwrap


def _drop():
    for m in list(sys.modules):
        if m == "faketg" or m.startswith("faketg."):
            del sys.modules[m]


def _run(build_and_drive):
    _drop()
    app = GatewayApp()
    session = app.capture_session(["faketg"])
    try:
        import faketg

        build_and_drive(faketg)
        out = os.path.join(tempfile.mkdtemp(), "tg.tape.json")
        return app.export_tape(session, out, {"sdk": "faketg"})
    finally:
        app.uninstall(session)
        _drop()


@scenario("telegram-aiogram-feed-update", tags=["gateway-shape", "telegram", "wedge"], depends_on=["capture-toy-timeline"])
def test_aiogram_shape_event_is_second_arg():
    """aiogram's feed_update(bot, update) is tapped and the label comes from the update, not the bot."""

    def drive(faketg):
        dp = faketg.Dispatcher()
        bot = faketg.Bot(token="123:fake")  # proxied handle from user code

        @dp.message()
        def on_message(injected_bot, message):
            # NOTE: uses the closure `bot` (proxied), not `injected_bot`.
            # Objects the SDK hands a handler are REAL/unproxied, so calls on
            # them bypass capture — see "Known limits" in README.md.
            bot.send_message(chat_id=message.chat_id, text="pong: " + message.text)

        update = faketg.AiogramUpdate(1, message=faketg.Message("hi"))
        unwrap(dp).deliver(unwrap(bot), update)  # SDK-internal poll loop

    with given("an aiogram-shaped Dispatcher (feed_update, no dispatch method) and a proxied Bot"):
        pass
    with when("the poll loop feeds an Update as the SECOND argument"):
        tape = _run(drive)
        inbound = [e for e in tape["timeline"] if e["dir"] == "in"]
    with then("one inbound event is recorded, labelled from the Update's event_type", expected="message"):
        assert len(inbound) == 1
        assert inbound[0]["event"] == "message"
    with and_("the payload is the Update, not the Bot (so no token in it)"):
        assert inbound[0]["payload"]["message"]["text"] == "hi"
        assert "token" not in str(inbound[0]["payload"])
    with and_("the reply sent through the proxied bot is recorded after the event", expected="send_message(text='pong: hi')"):
        sends = [e for e in tape["timeline"] if e["dir"] == "out" and e["call"].endswith("send_message")]
        assert sends and sends[0]["kwargs"]["text"] == "pong: hi"
        assert sends[0]["seq"] > inbound[0]["seq"]


@scenario("telegram-ptb-process-update", tags=["gateway-shape", "telegram"], depends_on=["telegram-aiogram-feed-update"])
def test_ptb_shape_label_from_populated_field():
    """python-telegram-bot's process_update(update) is tapped; the label is inferred from the populated field."""

    def drive(faketg):
        bot = faketg.Bot(token="123:fake")
        application = faketg.Application(bot=unwrap(bot))

        def on_message(b, update):
            b.send_message(chat_id=update.message.chat_id, text="ack")

        application.add_handler("message", on_message)
        update = faketg.PTBUpdate(7, message=faketg.Message("hello"))
        unwrap(application).deliver(update)  # SDK-internal update fetcher

    with given("a PTB-shaped Application (process_update) whose Update has no .type or .event_type"):
        pass
    with when("the update fetcher processes an Update with only 'message' populated"):
        tape = _run(drive)
        inbound = [e for e in tape["timeline"] if e["dir"] == "in"]
    with then("the event is labelled by the populated field", expected="message"):
        assert len(inbound) == 1
        assert inbound[0]["event"] == "message"
    with and_("the message text is on the payload"):
        assert inbound[0]["payload"]["message"]["text"] == "hello"


@scenario("telegram-callback-query-label", tags=["gateway-shape", "telegram"], depends_on=["telegram-ptb-process-update"])
def test_callback_query_update_labels_distinctly():
    """A different populated field (callback_query) yields a different label."""

    def drive(faketg):
        application = faketg.Application()

        def on_cb(b, update):
            return None

        application.add_handler("callback_query", on_cb)
        unwrap(application).deliver(faketg.PTBUpdate(9, callback_query={"data": "btn"}))

    with given("a PTB-shaped Application"):
        pass
    with when("an Update with only callback_query populated is processed"):
        tape = _run(drive)
        inbound = [e for e in tape["timeline"] if e["dir"] == "in"]
    with then("the event is labelled 'callback_query'", expected="callback_query"):
        assert inbound[0]["event"] == "callback_query"

```
### tests/test_toy_roundtrip.py

```python
"""Offline round-trip on the toy gateway SDK: capture → tape → replay.

Proves the wedge mechanics without network: inbound dispatch events land on the
timeline in order, outbound chained calls record under their access path, the
tape redacts secrets, and replay re-drives the same handlers offline with
values sourced from the tape.
"""
import asyncio
import json
import os
import sys

import pytest
from specdeck import and_, given, scenario, then, when

from tapedeck import GatewayApp, NoNetwork, diff_transcript
from tapedeck.engine.proxies import unwrap

BOT_PATH = "fakecord.ext.commands.Bot_result"


def _drop(name):
    for mod in list(sys.modules):
        if mod == name or mod.startswith(name + "."):
            del sys.modules[mod]


def make_handlers(client, replies=None):
    replies = replies if replies is not None else []

    async def on_message(message):
        channel = client.get_channel(123)
        reply = await channel.send(f"pong: {message.content}")
        replies.append(reply)

    return {"on_message": on_message}


@pytest.fixture()
def tape(tmp_path):
    _drop("fakecord")
    os.environ["FAKECORD_TOKEN"] = "supersecrettoken123"
    app = GatewayApp()
    session = app.capture_session(["fakecord"])
    try:
        import fakecord
        from fakecord.ext import commands

        bot = commands.Bot(prefix="!", token=os.environ["FAKECORD_TOKEN"])
        app.tap(session, bot, "fakecord")
        for fn in make_handlers(bot).values():
            unwrap(bot).event(fn)
        incoming = fakecord.Message(id=7, content="!ping", channel_id=123, author="human")
        asyncio.run(unwrap(bot).simulate([("ready", ()), ("message", (incoming,))]))
        return app.export_tape(session, str(tmp_path / "toy.tape.json"), {"sdk": "fakecord"})
    finally:
        app.uninstall(session)
        _drop("fakecord")


@scenario("capture-toy-timeline", tags=["engine", "wedge"])
def test_timeline_orders_inbound_before_resulting_outbound(tape):
    """Inbound gateway events land on the timeline before the outbound calls they cause."""
    with given("a tape captured from the toy gateway SDK with a pushed 'message' event"):
        events = [(e["dir"], e.get("event") or e.get("call")) for e in tape["timeline"]]
    with when("the inbound 'message' and the outbound 'send' are located on the timeline"):
        in_message = next(
            i for i, e in enumerate(tape["timeline"]) if e["dir"] == "in" and e["event"] == "message"
        )
        out_send = next(
            i for i, e in enumerate(tape["timeline"]) if e["dir"] == "out" and e["call"].endswith(".send")
        )
    with then("send comes after the inbound event that caused it", expected="in:message before out:send"):
        assert in_message < out_send, f"send must follow the inbound event: {events}"
    with and_("the 'ready' event was captured as well"):
        assert any(e["dir"] == "in" and e["event"] == "ready" for e in tape["timeline"])


@scenario("capture-toy-chained", tags=["engine"], depends_on=["capture-toy-timeline"])
def test_chained_async_call_recorded_under_access_path(tape):
    """A chained async call records under its access path, with schema and constructor binding."""
    with given("the toy tape"):
        methods = tape["mocked_modules"][f"{BOT_PATH}.get_channel_result"]["methods"]
    with then(
        "channel.send is recorded as async under Bot_result.get_channel_result",
        expected="async send returning content 'pong: !ping'",
    ):
        assert methods["send"]["async"] is True
        assert methods["send"]["return_value"]["content"] == "pong: !ping"
    with and_(
        "the constructor result binds back to its class",
        expected=f"{BOT_PATH} -> fakecord.ext.commands.Bot",
    ):
        assert tape["hierarchical_bindings"][BOT_PATH] == "fakecord.ext.commands.Bot"
    with and_("the Message schema was extracted"):
        assert "Message" in tape["nested_schemas"]


@scenario("capture-toy-redaction", tags=["engine", "security"], depends_on=["capture-toy-timeline"])
def test_tape_contains_no_secrets(tape):
    """The bot token never lands on the tape; a [REDACTED] marker proves the scrub ran."""
    with given("a capture session whose environment held FAKECORD_TOKEN"):
        raw = json.dumps(tape)
    with then("the token value is absent from the tape", expected="no 'supersecrettoken123' anywhere"):
        assert "supersecrettoken123" not in raw
    with and_("a [REDACTED] marker is present"):
        assert "[REDACTED]" in raw


@scenario("replay-toy-offline", tags=["replay", "wedge"], depends_on=["capture-toy-timeline", "capture-toy-chained"])
def test_replay_offline_matches_recording(tape):
    """Replaying the tape re-drives the same handler offline; every value comes from the tape."""
    replies = []
    with given("the toy tape and a fresh handler bound to a tape-backed client"):
        app = GatewayApp()
    with when("the recorded inbound events are pumped through the handler with the network blocked"):
        with NoNetwork():
            result = app.replay_session(
                tape, "fakecord", lambda client: make_handlers(client, replies), BOT_PATH
            )
    with then("exactly the handled event was pumped", expected="1 event pumped"):
        assert result["events_pumped"] == 1  # only on_message is handled
    with and_("the replayed outbound calls match the recording in order", expected="no transcript differences"):
        problems = diff_transcript(result["recorded_out"], result["transcript"], BOT_PATH)
        assert problems == []
    with and_("the reply value came from the tape, not the handler's argument", expected="pong: !ping"):
        assert replies[0].content == "pong: !ping"


@scenario("replay-drift-resolution", tags=["replay", "drift"], depends_on=["replay-toy-offline"])
def test_replay_flags_uncaptured_calls_as_drift(tape):
    """A call that was never captured raises ResolutionError — drift surfaces at the call site."""
    from tapedeck import ResolutionError, TapePlayer

    with given("a tape-backed client"):
        client = TapePlayer(tape).object_at(BOT_PATH)
    with then("accessing a method that is not on the tape raises ResolutionError", expected="ResolutionError"):
        with pytest.raises(ResolutionError):
            client.get_chanel  # typo — not on the tape

```
