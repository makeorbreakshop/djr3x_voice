"""
After R3X speaks, a DJ transition plan must still run.

## The defect this pins (found 2026-09-29)

Every spoken reply reaches the timeline as a one-step ``speak`` plan with no ``layer``
(``ElevenLabsService._create_speech_timeline_plan``), so it lands on ``foreground``.
``_handle_plan_ready`` pauses every lower layer for a foreground plan by clearing its
``asyncio.Event``. Nothing ever set it again. BrainService emits DJ transition plans on
``ambient``, and ``_run_plan`` begins with ``await self._layer_events[layer].wait()`` - so after
the first spoken reply, every later DJ transition blocked before its first step, forever and
silently (the plan was "started"; it just never did anything).

The fix derives the pause from what is running: a lower layer stays paused only while a
pausing plan's task is alive, re-checked when any plan ends (completed, failed, cancelled or
replaced) and when speech completes.

Everything here runs on a real pyee ``AsyncIOEventEmitter`` with the real service subscribed;
only ElevenLabs is played by the test (it answers ``TTS_GENERATE_REQUEST`` with the
``SPEECH_GENERATION_COMPLETE`` the real service emits).
"""

import asyncio
import uuid
from typing import Any, Dict, List

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.services.timeline_executor_service.timeline_executor_service import (
    TimelineExecutorService,
)


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture; nothing external is touched here."""
    yield {}


def _t(topic: EventTopics) -> str:
    return topic.value if hasattr(topic, "value") else str(topic)


def _speech_plan(text: str, conversation_id: str) -> Dict[str, Any]:
    """Exactly what ElevenLabsService emits for a normal spoken reply (no layer key)."""
    plan_id = str(uuid.uuid4())
    return {
        "plan_id": plan_id,
        "plan": {
            "plan_id": plan_id,
            "steps": [{"step_type": "speak", "text": text, "id": conversation_id, "duration": None}],
        },
    }


def _dj_transition_plan() -> Dict[str, Any]:
    """A DJ transition on ``ambient``, as BrainService._emit_validated_plan(layer="ambient")."""
    plan_id = str(uuid.uuid4())
    return {
        "plan_id": plan_id,
        "plan": {
            "plan_id": plan_id,
            "layer": "ambient",
            "steps": [
                {"step_type": "music_duck", "duck_level": 0.3, "fade_duration_ms": 10},
                {"step_type": "music_unduck", "fade_duration_ms": 10},
            ],
        },
    }


class FakeElevenLabs:
    """Answers each TTS request with a completion, like the real service."""

    def __init__(self, bus: AsyncIOEventEmitter, delay_s: float = 0.05) -> None:
        self.bus = bus
        self.delay_s = delay_s
        self.requests: List[Dict[str, Any]] = []
        bus.on(_t(EventTopics.TTS_GENERATE_REQUEST), self._on_request)

    async def _on_request(self, payload: Dict[str, Any]) -> None:
        self.requests.append(payload)
        await asyncio.sleep(self.delay_s)
        self.bus.emit(
            _t(EventTopics.SPEECH_GENERATION_COMPLETE),
            {
                "success": True,
                "text": payload.get("text", ""),
                "audio_length_seconds": 1.0,
                "clip_id": payload.get("clip_id"),
                "conversation_id": payload.get("conversation_id"),
            },
        )


@pytest.fixture
async def rig():
    bus = AsyncIOEventEmitter()
    svc = TimelineExecutorService(bus)
    await svc.start()
    tts = FakeElevenLabs(bus)
    ended: Dict[str, str] = {}
    bus.on(_t(EventTopics.PLAN_ENDED), lambda p: ended.setdefault(p["plan_id"], p["status"]))
    yield svc, bus, tts, ended
    await svc.stop()


async def _wait_for(pred, timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


async def test_a_dj_transition_still_runs_after_r3x_speaks(rig):
    svc, bus, tts, ended = rig

    speech = _speech_plan("Welcome to the cantina!", "conv-1")
    bus.emit(_t(EventTopics.PLAN_READY), speech)
    assert await _wait_for(lambda: ended.get(speech["plan_id"]) == "completed"), (
        f"the spoken reply itself never finished: {ended}"
    )
    assert tts.requests, "the speak step never asked ElevenLabs for audio"

    dj = _dj_transition_plan()
    bus.emit(_t(EventTopics.PLAN_READY), dj)
    assert await _wait_for(lambda: ended.get(dj["plan_id"]) == "completed"), (
        "a DJ transition plan on the ambient layer never ran after R3X spoke once: "
        f"ambient paused={not svc._layer_events['ambient'].is_set()}, ended={ended}"
    )


async def test_ambient_waits_while_r3x_is_speaking_then_runs(rig):
    """The pause is still real: it holds for the length of the reply, then lifts."""
    svc, bus, tts, ended = rig
    tts.delay_s = 0.4

    speech = _speech_plan("Hold on, I'm talking.", "conv-2")
    bus.emit(_t(EventTopics.PLAN_READY), speech)
    await _wait_for(lambda: tts.requests)
    assert not svc._layer_events["ambient"].is_set(), "a spoken reply no longer pauses ambient"

    dj = _dj_transition_plan()
    bus.emit(_t(EventTopics.PLAN_READY), dj)
    await asyncio.sleep(0.1)
    assert dj["plan_id"] not in ended, "the DJ transition ran over R3X's speech"

    assert await _wait_for(lambda: ended.get(dj["plan_id"]) == "completed")
    assert ended.get(speech["plan_id"]) == "completed"


async def test_a_cancelled_reply_releases_the_pause(rig):
    """Speech that never completes (ElevenLabs down, plan replaced) must not strand ambient."""
    svc, bus, tts, ended = rig
    tts.delay_s = 60.0  # never answers within the test

    speech = _speech_plan("This one gets cut off.", "conv-3")
    bus.emit(_t(EventTopics.PLAN_READY), speech)
    await _wait_for(lambda: tts.requests)
    assert not svc._layer_events["ambient"].is_set()

    # DJ mode stopping cancels every plan (TimelineExecutorService._handle_dj_mode_changed).
    bus.emit(_t(EventTopics.DJ_MODE_CHANGED), {"is_active": False})
    assert await _wait_for(lambda: svc._layer_events["ambient"].is_set()), (
        "cancelling the speaking plan left the ambient layer paused"
    )


async def test_a_replaced_reply_keeps_ambient_paused_until_the_new_one_ends(rig):
    """The old plan's cancellation must not lift a pause the new plan still needs."""
    svc, bus, tts, ended = rig
    tts.delay_s = 0.3

    first = _speech_plan("First.", "conv-4a")
    bus.emit(_t(EventTopics.PLAN_READY), first)
    await _wait_for(lambda: len(tts.requests) == 1)
    second = _speech_plan("Second.", "conv-4b")
    bus.emit(_t(EventTopics.PLAN_READY), second)
    assert await _wait_for(lambda: ended.get(first["plan_id"]) == "cancelled")
    assert not svc._layer_events["ambient"].is_set(), (
        "the replaced plan's cleanup resumed ambient while the new reply is still speaking"
    )
    assert await _wait_for(lambda: ended.get(second["plan_id"]) == "completed")
    assert svc._layer_events["ambient"].is_set()
