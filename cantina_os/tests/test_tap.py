"""Phase 0 instrumentation: bus tap, session log, tap websocket auth, fixture record/replay."""

import asyncio
import json
import os
import socket
import stat
import threading
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from cantina_os.base_service import BaseService
from cantina_os.tap import auth, fixtures
from cantina_os.tap.bus_tap import SessionLog, TappedEmitter, jsonable
from cantina_os.tap.server import TapServer

TOKEN = "tap-test-token"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- bus tap


class _Svc(BaseService):
    def __init__(self, bus):
        super().__init__(service_name="svc_a", event_bus=bus)

    def direct(self):
        self._event_bus.emit("x.direct", {"a": 1})  # bypasses BaseService.emit


async def test_every_emit_is_recorded_with_source():
    bus = TappedEmitter()
    seen = []
    bus.add_sink(seen.append)
    got = []
    bus.on("x.direct", got.append)
    svc = _Svc(bus)
    svc.direct()
    await svc.emit("x.via_base", {"b": b"\x00" * 10})
    bus.emit("x.bare", None)
    await asyncio.sleep(0)
    assert got == [{"a": 1}]  # delivery unchanged
    assert [r["topic"] for r in seen] == ["x.direct", "x.via_base", "x.bare"]
    assert [r["seq"] for r in seen] == [1, 2, 3]
    assert seen[0]["source"] == "svc_a" and seen[1]["source"] == "svc_a"
    assert seen[2]["source"].startswith("module:")
    assert seen[1]["payload"] == {"b": {"__bytes__": 10}}
    assert all(r["t_mono"] > 0 and r["t_wall"] > 0 for r in seen)


def test_jsonable_bounds_and_survives_odd_values():
    out = jsonable({"s": "x" * 5000, "l": list(range(500)), "f": float("nan"), "o": object()})
    json.dumps(out)
    assert len(out["s"]) < 2100 and len(out["l"]) == 201 and out["f"] is None


def test_session_log_writes_header_and_lines(tmp_path):
    bus = TappedEmitter()
    log = SessionLog(tmp_path / "s.jsonl", {"session": "t"})
    bus.add_sink(log)
    bus.emit("a.b", {"k": 1})
    log.close()
    lines = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert lines[0]["kind"] == "header" and lines[1]["topic"] == "a.b" and lines[1]["payload"] == {"k": 1}


# ---------------------------------------------------------------- auth


def test_token_file_is_created_private(tmp_path):
    env = {"R3X_TOKEN_FILE": str(tmp_path / "d" / "tok")}
    t1 = auth.load_token(env)
    assert t1 and auth.load_token(env) == t1
    assert stat.S_IMODE(os.stat(env["R3X_TOKEN_FILE"]).st_mode) == 0o600
    assert auth.load_token({"R3X_TAP_TOKEN": "explicit"}) == "explicit"


def test_check_rules():
    origins = {"http://localhost:5391"}
    assert auth.check({}, "/?token=t", "t", origins) is None
    assert auth.check({"Authorization": "Bearer t"}, "/", "t", origins) is None
    assert auth.check({}, "/", "t", origins) == "bad or missing token"
    assert auth.check({}, "/?token=nope", "t", origins) == "bad or missing token"
    assert auth.check({"Origin": "http://evil.example"}, "/?token=t", "t", origins) == "origin not allowed"
    assert auth.check({"Origin": "http://localhost:5391"}, "/?token=t", "t", origins) is None


# ---------------------------------------------------------------- tap websocket


@pytest.fixture
async def tap():
    bus = TappedEmitter()
    server = TapServer(bus, "sess", port=_free_port(), token=TOKEN)
    assert await server.start()
    yield bus, server
    await server.stop()


async def test_tap_refuses_bad_token_and_origin(tap):
    _, server = tap
    with pytest.raises(InvalidStatus):
        async with connect(f"ws://127.0.0.1:{server.port}/"):
            pass
    with pytest.raises(InvalidStatus):
        async with connect(f"ws://127.0.0.1:{server.port}/?token={TOKEN}", origin="http://evil.example"):
            pass


async def test_tap_streams_every_topic_and_accepts_emits(tap):
    bus, server = tap
    inbound = []
    bus.on("music.command", inbound.append)
    async with connect(f"ws://127.0.0.1:{server.port}/",
                       additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
        hello = json.loads(await ws.recv())
        assert hello["kind"] == "hello" and hello["session"] == "sess"
        bus.emit("anything.at.all", {"n": 1})
        threading.Thread(target=lambda: bus.emit("from.thread", {})).start()
        msgs = [json.loads(await asyncio.wait_for(ws.recv(), 2)) for _ in range(2)]
        assert {m["topic"] for m in msgs} == {"anything.at.all", "from.thread"}
        assert all(m["kind"] == "event" and m["v"] == 1 and "seq" in m for m in msgs)

        await ws.send(json.dumps({"id": "c1", "topic": "music.command", "payload": {"action": "stop"}}))
        echoed = json.loads(await asyncio.wait_for(ws.recv(), 2))
        ack = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert echoed["topic"] == "music.command" and echoed["source"] == "tap"
        assert ack == {"v": 1, "kind": "ack", "re": "c1", "ok": True, "seq": echoed["seq"]}
        await ws.send("not json")
        assert json.loads(await asyncio.wait_for(ws.recv(), 2))["ok"] is False
    await asyncio.sleep(0)
    assert inbound == [{"action": "stop"}]


# ---------------------------------------------------------------- fixtures


class _FakeStream:
    def __init__(self, texts, final):
        self._texts, self._final = texts, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        yield from self._texts

    def get_final_message(self):
        return self._final


def _message(text):
    from anthropic.types import Message

    return Message.model_validate({
        "id": "m1", "type": "message", "role": "assistant", "model": "x", "stop_reason": "end_turn",
        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 2},
        "content": [{"type": "text", "text": text},
                    {"type": "tool_use", "id": "t1", "name": "play_music", "input": {"track": "a"}}]})


def test_claude_and_tts_record_then_replay(tmp_path):
    rec = fixtures.FixtureStore("record", tmp_path)
    real = SimpleNamespace(messages=SimpleNamespace(
        stream=lambda **kw: _FakeStream(["Hel", "lo"], _message("Hello")),
        create=lambda **kw: _message("summary")))
    client = rec.wrap_anthropic(real, "claude")
    req = {"messages": [{"role": "user", "content": "hi"}], "tool_choice": {"type": "none"}}
    with client.messages.stream(**req) as s:
        assert list(s.text_stream) == ["Hel", "lo"]
        s.get_final_message()
    client.messages.create(messages=[{"role": "user", "content": "sum"}])
    audio = [(b"\x01\x02" * 50, {"chars": ["a"]}), (b"\x03\x04" * 20, None)]
    assert list(rec.tts("Hello", "m", lambda: iter(audio))) == audio

    rep = fixtures.FixtureStore("replay", tmp_path)
    boom = SimpleNamespace(messages=SimpleNamespace(stream=None, create=None))  # never called
    client = rep.wrap_anthropic(boom, "claude")
    with client.messages.stream(**req) as s:
        assert list(s.text_stream) == ["Hel", "lo"]
        final = s.get_final_message()
    assert final.content[1].name == "play_music" and final.content[1].input == {"track": "a"}
    assert client.messages.create(messages=[{"role": "user", "content": "sum"}]).content[0].text == "summary"
    assert list(rep.tts("Hello", "m", lambda: 1 / 0)) == audio
    with pytest.raises(RuntimeError):
        client.messages.stream(messages=[{"role": "user", "content": "unrecorded"}])


async def test_jev_client_records_and_replays(tmp_path, monkeypatch):
    from cantina_os.llm.jev_client import JevClient

    body = {"model": "jev", "answers": {"q": {"choice": "a", "confidence": 0.9, "noul": 0.8}},
            "usage": {"input_tokens": 3, "output_tokens": 1}}

    class _Resp:
        status_code = 200

        def json(self):
            return body

    class _Http:
        async def post(self, url, json):
            return _Resp()

    fixtures.configure("record", tmp_path)
    try:
        c = JevClient(api_key="k")
        c._client = _Http()
        live = await c.classify({"utterance": "play"}, {"q": {"type": "choice"}})
        fixtures.configure("replay", tmp_path)
        c._client = SimpleNamespace(post=None)  # a network call would fail loudly
        again = await c.classify({"utterance": "play"}, {"q": {"type": "choice"}})
        assert again is not None and again.answers.keys() == live.answers.keys()
        assert again.answers["q"].confidence == live.answers["q"].confidence
    finally:
        fixtures.configure("")


# ---------------------------------------------------------------- trace compare


def _trace(events):
    """events: (ms, topic, payload) -> tap records."""
    return [{"seq": i, "t_mono": ms / 1000, "t_wall": ms / 1000, "topic": t, "source": "x", "payload": p}
            for i, (ms, t, p) in enumerate(events)]


def test_trace_compare_catches_a_changed_action_and_tolerates_ties():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "trace_compare", Path(__file__).parents[1] / "scripts" / "trace_compare.py")
    tc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tc)
    base = [(0, "voice.listening.started", {"conversation_id": "a"}),
            (50, "voice.listening.stopped", {"transcript": "play", "conversation_id": "a"}),
            (250, "music.command", {"action": "play"}), (260, "plan.ended", {}), (270, "intent.execution.result", {}),
            (300, "speech.synthesis.amplitude", {"amplitude": 0.3})]
    exp = tc.turns(_trace(base))
    swapped = base[:3] + [(262, "intent.execution.result", {}), (265, "plan.ended", {})]  # a near-tie swap
    assert tc.compare(exp, tc.turns(_trace(swapped))) == []
    changed = base[:2] + [(250, "music.command", {"action": "stop"})] + base[3:]
    assert any("actions differ" in d for d in tc.compare(exp, tc.turns(_trace(changed))))
    late = base[:2] + [(1900, "music.command", {"action": "play"})] + base[3:]
    assert any("timing first_action" in d for d in tc.compare(exp, tc.turns(_trace(late))))
