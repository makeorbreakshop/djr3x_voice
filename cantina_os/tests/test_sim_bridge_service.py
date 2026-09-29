"""SimBridgeService forwards bus events to a real websocket client (no mocks on the wire)."""

import asyncio
import json
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
    name = "INTERACTIVE"


class _ModeManager:
    current_mode = _Mode()


@pytest.mark.asyncio
async def test_forwards_events_to_connected_sim():
    bus = AsyncIOEventEmitter()
    port = _free_port()
    svc = SimBridgeService(bus, {"SIM_BRIDGE_PORT": port}, _ModeManager())
    await svc.start()
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert hello == {"type": "hello", "mode": "INTERACTIVE"}

            for _ in range(50):
                if svc.client_count:
                    break
                await asyncio.sleep(0.01)
            bus.emit(EventTopics.VOICE_LISTENING_STARTED.value, {"conversation_id": "abc"})
            bus.emit(EventTopics.SPEECH_SYNTHESIS_AMPLITUDE.value, {"amplitude": 0.42, "conversation_id": "abc"})

            first = json.loads(await asyncio.wait_for(ws.recv(), 2))
            second = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert first["topic"] == "voice.listening.started"
            assert first["data"]["conversation_id"] == "abc"
            assert second["topic"] == "speech.synthesis.amplitude"
            assert second["data"]["amplitude"] == 0.42
    finally:
        await svc.stop()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
            got = [json.loads(await asyncio.wait_for(ws.recv(), 2)) for _ in show_topics]
            assert [m["topic"] for m in got] == [t.value for t, _ in show_topics]
            assert [m["data"] for m in got] == [p for _, p in show_topics]
    finally:
        await svc.stop()
