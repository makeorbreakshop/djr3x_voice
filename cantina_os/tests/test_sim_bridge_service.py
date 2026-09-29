"""SimBridgeService against a real websocket client and a real pyee bus (no mocks on the wire)."""

import asyncio
import json
import logging
import socket

import pytest
from pyee.asyncio import AsyncIOEventEmitter
from websockets.asyncio.client import connect

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.sim_bridge_service import SimBridgeService


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Mode:
    def __init__(self, name):
        self.name = name


class _ModeManager:
    def __init__(self, name="INTERACTIVE"):
        self.current_mode = _Mode(name)


class _Track:
    name = "Cantina Band"


class _Music:
    tracks = {"Cantina Band": object(), "Mad About Me": object()}
    current_track = _Track()


async def _recv_until(ws, pred, timeout=2.0):
    """Next message matching ``pred`` - log lines interleave with events on the wire."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        msg = json.loads(await asyncio.wait_for(ws.recv(), max(remaining, 0.01)))
        if pred(msg):
            return msg


async def _wait_for(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


def _capture(bus, topic):
    seen = []
    bus.on(topic.value if hasattr(topic, "value") else topic, lambda p=None: seen.append(p))
    return seen


@pytest.fixture
async def bridge():
    bus = AsyncIOEventEmitter()
    port = _free_port()
    svc = SimBridgeService(bus, {"SIM_BRIDGE_PORT": port}, _ModeManager(), _Music())
    await svc.start()
    yield bus, port, svc
    await svc.stop()


async def test_hello_carries_a_snapshot(bridge):
    bus, port, svc = bridge
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert hello["type"] == "hello"
        assert hello["mode"] == "INTERACTIVE"
        assert hello["music"]["tracks"] == ["Cantina Band", "Mad About Me"]
        assert hello["music"]["current"] == "Cantina Band"
        assert isinstance(hello["logs"], list) and isinstance(hello["events"], list)


async def test_forwards_events_to_connected_sim(bridge):
    bus, port, svc = bridge
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)  # hello
        await _wait_for(lambda: svc.client_count)
        bus.emit(EventTopics.VOICE_LISTENING_STARTED.value, {"conversation_id": "abc"})
        bus.emit(EventTopics.SPEECH_SYNTHESIS_AMPLITUDE.value, {"amplitude": 0.42, "conversation_id": "abc"})

        is_event = lambda m: m["type"] == "event"
        first = await _recv_until(ws, is_event)
        second = await _recv_until(ws, is_event)
        assert first["topic"] == "voice.listening.started"
        assert first["data"]["conversation_id"] == "abc"
        assert second["topic"] == "speech.synthesis.amplitude"
        assert second["data"]["amplitude"] == 0.42


async def test_amplitude_is_not_replayed_to_late_clients(bridge):
    bus, port, svc = bridge
    bus.emit(EventTopics.SPEECH_SYNTHESIS_AMPLITUDE.value, {"amplitude": 0.5})
    bus.emit(EventTopics.LLM_RESPONSE.value, {"text": "hi", "is_complete": True})
    await asyncio.sleep(0.05)
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 2))
        topics = [e["topic"] for e in hello["events"]]
        assert "llm.response" in topics
        assert "speech.synthesis.amplitude" not in topics


async def test_logs_stream_to_the_panel(bridge):
    bus, port, svc = bridge
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)
        logging.getLogger("cantina_os.test").warning("panel log check")
        msg = await _recv_until(ws, lambda m: m["type"] == "log" and m["msg"] == "panel log check")
        assert msg["level"] == "WARNING"


async def test_cli_command_is_emitted_like_the_terminal(bridge):
    bus, port, svc = bridge
    seen = _capture(bus, EventTopics.CLI_COMMAND)
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)
        await ws.send(json.dumps({"type": "cmd", "id": "1", "action": "cli", "text": "p cantina band"}))
        ack = await _recv_until(ws, lambda m: m["type"] == "ack")
        assert ack == {"type": "ack", "id": "1", "ok": True, "message": "play music cantina band"}
        await _wait_for(lambda: seen)
        assert seen[0]["command"] == "play"
        assert seen[0]["args"] == ["music", "cantina", "band"]
        assert seen[0]["raw_input"] == "play music cantina band"


async def test_quit_is_refused_from_the_panel(bridge):
    bus, port, svc = bridge
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)
        await ws.send(json.dumps({"type": "cmd", "id": "q", "action": "cli", "text": "quit"}))
        ack = await _recv_until(ws, lambda m: m["type"] == "ack")
        assert ack["ok"] is False


async def test_say_injects_a_turn_like_the_capture_service(bridge):
    bus, port, svc = bridge
    started = _capture(bus, EventTopics.VOICE_LISTENING_STARTED)
    stopped = _capture(bus, EventTopics.VOICE_LISTENING_STOPPED)
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)
        await ws.send(json.dumps({"type": "cmd", "id": "s", "action": "say", "text": "what's playing?"}))
        ack = await _recv_until(ws, lambda m: m["type"] == "ack")
        assert ack["ok"] is True
        await _wait_for(lambda: stopped)
        assert stopped[0]["transcript"] == "what's playing?"
        assert stopped[0]["has_transcript"] is True
        assert stopped[0]["conversation_id"] == started[0]["conversation_id"] == ack["message"]


async def test_push_to_talk_starts_and_stops_the_mic(bridge):
    bus, port, svc = bridge
    # Stand-in for DeepgramDirectMicService: acknowledge a start, report a stop.
    stops = _capture(bus, EventTopics.MIC_RECORDING_STOP)
    bus.on(EventTopics.MIC_RECORDING_START.value,
           lambda p=None: bus.emit(EventTopics.VOICE_LISTENING_STARTED.value, {"conversation_id": "c1"}))
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)
        await ws.send(json.dumps({"type": "cmd", "id": "a", "action": "ptt", "state": "start"}))
        ack = await _recv_until(ws, lambda m: m["type"] == "ack")
        assert ack["ok"] is True and ack["message"] == "listening"

        await ws.send(json.dumps({"type": "cmd", "id": "b", "action": "ptt", "state": "stop"}))
        ack = await _recv_until(ws, lambda m: m["type"] == "ack")
        assert ack["ok"] is True
        await _wait_for(lambda: stops)


async def test_push_to_talk_engages_interactive_first():
    bus = AsyncIOEventEmitter()
    port = _free_port()
    modes = _ModeManager("IDLE")
    svc = SimBridgeService(bus, {"SIM_BRIDGE_PORT": port}, modes)
    requests = _capture(bus, EventTopics.SYSTEM_SET_MODE_REQUEST)

    def _set_mode(p=None):
        modes.current_mode = _Mode(p["mode"])

    bus.on(EventTopics.SYSTEM_SET_MODE_REQUEST.value, _set_mode)
    bus.on(EventTopics.MIC_RECORDING_START.value,
           lambda p=None: bus.emit(EventTopics.VOICE_LISTENING_STARTED.value, {"conversation_id": "c2"}))
    await svc.start()
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await asyncio.wait_for(ws.recv(), 2)
            await ws.send(json.dumps({"type": "cmd", "id": "a", "action": "ptt", "state": "start"}))
            ack = await _recv_until(ws, lambda m: m["type"] == "ack")
            assert ack["ok"] is True
            assert requests[0] == {"mode": "INTERACTIVE"}
    finally:
        await svc.stop()


async def test_panel_connection_is_reported_for_the_mouse_service(bridge):
    bus, port, svc = bridge
    statuses = _capture(bus, EventTopics.SERVICE_STATUS_UPDATE)
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await asyncio.wait_for(ws.recv(), 2)
        await ws.send(json.dumps({"type": "hello", "role": "panel"}))
        await _wait_for(lambda: statuses)
        assert statuses[-1]["service_name"] == "control_panel"
        assert statuses[-1]["status"] == "RUNNING"
    await _wait_for(lambda: statuses[-1]["status"] == "STOPPED")


async def test_port_in_use_is_not_fatal():
    bus = AsyncIOEventEmitter()
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        svc = SimBridgeService(bus, {"SIM_BRIDGE_PORT": port})
        await svc.start()  # must not raise
        assert svc.is_started
        await svc.stop()


async def test_disabled_does_not_listen():
    bus = AsyncIOEventEmitter()
    port = _free_port()
    svc = SimBridgeService(bus, {"SIM_BRIDGE_ENABLED": "false", "SIM_BRIDGE_PORT": port})
    await svc.start()
    with pytest.raises(OSError):
        async with connect(f"ws://127.0.0.1:{port}", open_timeout=0.5):
            pass
    await svc.stop()


@pytest.mark.asyncio
async def test_forwards_every_show_topic():
    bus = AsyncIOEventEmitter()
    port = _free_port()
    svc = SimBridgeService(bus, {"SIM_BRIDGE_PORT": port}, _ModeManager())
    await svc.start()
    show_topics = [
        (EventTopics.SHOW_PERFORM, {"id": "nod", "params": None, "source": "claude", "conversation_id": "c1"}),
        (EventTopics.SHOW_STOP, {"all": True}),
        (EventTopics.SHOW_STARTED, {"id": "nod", "kind": "clip", "source": "claude", "run_id": "r1"}),
        (EventTopics.SHOW_MOTION, {"run_id": "r1", "clip": "nod", "intensity": 1.0, "speed": 1.0,
                                   "start_at": 1.5, "layer": "gesture", "owns": None}),
        (EventTopics.STAGE_LIGHTS, {"cue": "red_flash", "mode": None, "fade": 0.1, "hold": 2.0, "rig": None}),
        (EventTopics.CHEST_OVERRIDE, {"command": "X2", "hold": 1.5}),
        (EventTopics.SHOW_SFX, {"id": "airhorn"}),
        (EventTopics.EYE_COMMAND, {"pattern": "happy", "color": "#ffb000"}),
        (EventTopics.MOTION_FREEZE, {"on": True}),
        (EventTopics.SHOW_ENDED, {"id": "nod", "kind": "clip", "source": "claude", "run_id": "r1", "reason": "done"}),
    ]
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await asyncio.wait_for(ws.recv(), 2)  # hello
            for _ in range(50):
                if svc.client_count:
                    break
                await asyncio.sleep(0.01)
            for topic, payload in show_topics:
                bus.emit(topic.value, payload)
            got = [await _recv_until(ws, lambda m: m["type"] == "event") for _ in show_topics]
            assert [m["topic"] for m in got] == [t.value for t, _ in show_topics]
            assert [m["data"] for m in got] == [p for _, p in show_topics]
    finally:
        await svc.stop()


async def test_stop_removes_the_log_handler(bridge):
    bus, port, svc = bridge
    handler = svc._log_handler
    assert handler in logging.getLogger().handlers
    await svc.stop()
    assert handler not in logging.getLogger().handlers
