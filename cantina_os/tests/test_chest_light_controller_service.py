"""ChestLightControllerService: lockstep with the eyes + machine status, on a real pyee bus.

Runs in mock mode (no board); asserts on CHEST_COMMAND, which carries every command the
board would receive (and what the 3D sim mirrors).
"""

import asyncio

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.chest_light_controller_service import (
    ChestLightControllerService,
    WINDOW_SUBSYSTEMS,
    health_mask,
)


async def settle():
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.fixture
async def chest():
    bus = AsyncIOEventEmitter()
    sent = []
    bus.on(EventTopics.CHEST_COMMAND.value, lambda p: sent.append(p["command"]))
    svc = ChestLightControllerService(bus, {"FORCE_MOCK_CHEST": "true"})
    svc.LOOP_HZ = 1000  # the test drives tick() itself
    await svc.start()
    svc._task.cancel()
    yield bus, svc, sent
    await svc.stop()


def emit(bus, topic, payload=None):
    bus.emit(getattr(topic, "value", topic), payload or {})


@pytest.mark.asyncio
async def test_boots_then_follows_mode(chest):
    bus, svc, sent = chest
    svc.tick()
    assert sent[:2] == ["R", "X1"]
    assert "H1FF" in sent  # all healthy, sent once; after that only changes go out
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "INTERACTIVE"})
    await settle()
    sent.clear()
    svc.tick()
    assert sent == ["X0", "SE"]


@pytest.mark.asyncio
async def test_conversation_in_lockstep_with_eyes(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "INTERACTIVE"})
    await settle()
    svc.tick()
    sent.clear()

    emit(bus, EventTopics.VOICE_LISTENING_STARTED)
    await settle(); svc.tick()
    emit(bus, EventTopics.VOICE_LISTENING_STOPPED)
    await settle(); svc.tick()
    emit(bus, EventTopics.SPEECH_GENERATION_STARTED)
    await settle(); svc.tick()
    for a in (0.8, 0.9, 0.7):
        emit(bus, EventTopics.SPEECH_SYNTHESIS_AMPLITUDE, {"amplitude": a})
        await settle(); svc.tick()
    emit(bus, EventTopics.SPEECH_GENERATION_COMPLETE)
    await settle(); svc.tick()

    patterns = [c for c in sent if c[0] == "S"]
    assert patterns == ["SL", "ST", "SS", "SF", "SE"]
    amps = [c for c in sent if c[0] == "M"]
    assert len(amps) >= 3 and amps[-1] == "M000"  # the end-of-speech reset is never dropped
    assert sent.index("M000") < sent.index("SF")


@pytest.mark.asyncio
async def test_listening_ignored_outside_interactive(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "IDLE"})
    await settle(); svc.tick()
    emit(bus, EventTopics.VOICE_LISTENING_STARTED)
    await settle(); svc.tick()
    assert "SL" not in sent and svc.target_pattern == "SI"


@pytest.mark.asyncio
async def test_health_windows_and_fault(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "IDLE"})
    await settle(); svc.tick()
    sent.clear()

    emit(bus, "service_status", {"service": "MusicControllerService", "status": "degraded"})
    await settle(); svc.tick()
    music_bit = [label for label, _ in WINDOW_SUBSYSTEMS].index("music")
    assert f"H{0x1FF & ~(1 << music_bit):03X}" in sent
    assert "X3" not in sent  # music down is not a fault

    emit(bus, "service_status", {"service": "ClaudeService", "status": "error"})
    await settle(); svc.tick()
    assert "X3" in sent  # the LLM down means R3X cannot converse

    emit(bus, "service_status", {"service": "ClaudeService", "status": "running"})
    emit(bus, "service_status", {"service": "MusicControllerService", "status": "running"})
    await settle(); svc.tick()
    assert sent[-2:] == ["X0", "H1FF"] or ("X0" in sent[-3:] and "H1FF" in sent[-3:])


@pytest.mark.asyncio
async def test_music_tempo(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.MUSIC_PLAYBACK_STARTED, {"track": {"title": "Cantina Band"}})
    await settle(); svc.tick()
    assert "B120" in sent
    emit(bus, EventTopics.MUSIC_PLAYBACK_STOPPED)
    await settle(); svc.tick()
    assert sent[-1] == "B000" or "B000" in sent[-3:]


def test_health_mask_ignores_services_that_never_reported():
    assert health_mask({}) == 0x1FF
    assert health_mask({"VisionService": "stopped"}) == 0x1FF  # shutdown is not a failure


@pytest.mark.asyncio
async def test_runtime_error_expires_but_startup_failure_latches(chest):
    bus, svc, sent = chest
    svc._fault_hold = 0.05
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "IDLE"})
    emit(bus, "service_status", {"service": "ClaudeService", "status": "error",
                                 "message": "Error processing with Claude: 401"})
    await settle(); svc.tick()
    assert "X3" in sent
    await asyncio.sleep(0.08); svc.tick()
    assert sent[-1] == "X0" or "X0" in sent[-2:]  # one failed request is not a dead LLM

    emit(bus, "service_status", {"service": "ClaudeService", "status": "error",
                                 "message": "Failed to start Claude service: no key"})
    await settle(); svc.tick()
    await asyncio.sleep(0.08); svc.tick()
    assert svc.system_state() == "X3"


# ---------------------------------------------------------------------------- show control

@pytest.mark.asyncio
async def test_show_override_holds_the_channel_then_status_resumes(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "INTERACTIVE"})
    await settle(); svc.tick()
    sent.clear()

    emit(bus, EventTopics.CHEST_OVERRIDE, {"command": "X2", "hold": 0.08})
    await settle()
    assert sent == ["X2"]  # out at once, not on the next tick
    # the status logic wants X3 now, but the show owns the X channel for the hold
    emit(bus, "service_status", {"service": "ClaudeService", "status": "error"})
    await settle(); svc.tick()
    assert "X3" not in sent
    emit(bus, EventTopics.VOICE_LISTENING_STARTED)  # other channels keep working
    await settle(); svc.tick()
    assert "SL" in sent

    await asyncio.sleep(0.1); svc.tick()
    assert sent[-1] == "X3" or "X3" in sent[-3:]  # back to status after the hold


@pytest.mark.asyncio
async def test_show_override_with_no_hold_stays_until_status_changes(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "INTERACTIVE"})
    await settle(); svc.tick()
    sent.clear()
    emit(bus, EventTopics.CHEST_OVERRIDE, {"command": "SF", "hold": 0})
    await settle()
    for _ in range(3):
        svc.tick()
    assert sent == ["SF"]  # status unchanged -> the override is left alone
    emit(bus, EventTopics.VOICE_LISTENING_STARTED)
    await settle(); svc.tick()
    assert sent[-1] == "SL"


@pytest.mark.asyncio
async def test_motion_freeze_holds_the_board_then_reasserts(chest):
    bus, svc, sent = chest
    emit(bus, EventTopics.SYSTEM_MODE_CHANGE, {"new_mode": "INTERACTIVE"})
    await settle(); svc.tick()
    sent.clear()
    emit(bus, EventTopics.MOTION_FREEZE, {"on": True})
    await settle()
    emit(bus, EventTopics.VOICE_LISTENING_STARTED)
    emit(bus, EventTopics.CHEST_OVERRIDE, {"command": "X2", "hold": 1})
    await settle(); svc.tick()
    assert sent == []  # frozen: nothing changes on the board
    emit(bus, EventTopics.MOTION_FREEZE, {"on": False})
    await settle(); svc.tick()
    assert {"X0", "H1FF", "SL", "B000"} <= set(sent)  # every channel re-asserted
