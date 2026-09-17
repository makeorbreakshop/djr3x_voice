"""
One transcript in, exactly one spoken reply out.

## The defect this pins (observed live 2026-09-17 10:57:24)

Brandon said "Yeah. Go ahead and play some music for me." The Jev fast router dispatched
`play_music` in 0.7 ms and the music started. Then **two** Claude replies were produced and
both were sent to TTS:

1. `IntentRouterService` emitted `INTENT_EXECUTION_RESULT`; `ClaudeService`'s subscription
   fired `_get_verbal_response_for_intent`, whose reply was
   *"Now spinning up "Cantina Band" for you. Classic choice!"* — plan
   `3c029dce-3e0f-40e6-b5fe-05c2cb41819f`, which began speaking at 10:57:25.418.
2. The main turn, carrying the `<action_already_taken>` block, replied
   *"Oh YEAH! Now we're talking! Cantina Band is SPINNING..."* — plan
   `44c2918a-fe8c-4bc7-a15a-a2b30468b2d5`, which **cancelled plan (1) mid-speech** at
   10:57:25.885 ("Cancelling existing plan on layer foreground").

The user heard R3X start a sentence and interrupt itself. The main-turn reply is the one to
keep — it is the one that knows the action already happened — so the legacy verbal-response
path must be suppressed for a turn the fast router executed.

## What is real here

The real bus, the real `IntentRouterService`, the real `ClaudeService` with a stub Anthropic
client. `PLAN_READY` is produced by a stand-in for `ElevenLabsService`, which emits exactly one
plan per complete `LLM_RESPONSE` — the same 1:1 rule the real service applies
(`elevenlabs_service.py`: "Complete response received (N chars). Creating timeline plan.").
Using a stand-in keeps the test offline; the count under test is the reply count, not TTS.
"""

import asyncio
import time
import uuid
from typing import Any, Dict, List

import pytest
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.core.fast_router_gate import GATE, ActionTaken
from cantina_os.services.claude_service.claude_service import ClaudeService
from cantina_os.services.intent_router_service import IntentRouterService

TRANSCRIPT = "Yeah. Go ahead and play some music for me."


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x."""
    yield {}


class StubAnthropicMessages:
    """Stands in for `client.messages`, recording every request it was given."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        time.sleep(0.02)

        class _Text:
            type = "text"
            text = "Oh YEAH! Cantina Band is SPINNING right now!"

        class _Usage:
            input_tokens = 100
            output_tokens = 12
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0

        class _Response:
            content = [_Text()]
            usage = _Usage()

        return _Response()


class StubAnthropic:
    def __init__(self) -> None:
        self.messages = StubAnthropicMessages()


class Probe:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def handler(self, payload: Dict[str, Any]) -> None:
        self.events.append(payload)

    @property
    def count(self) -> int:
        return len(self.events)


@pytest.fixture
async def rig():
    GATE.reset()
    bus = AsyncIOEventEmitter()

    llm_final = Probe()
    plan_ready = Probe()

    def on_llm_response(payload: Any) -> None:
        data = payload if isinstance(payload, dict) else payload.model_dump()
        if data.get("is_complete"):
            llm_final.handler(data)
            # Stand-in for ElevenLabsService: one timeline plan per complete reply.
            bus.emit(
                EventTopics.PLAN_READY.value,
                {"plan_id": str(uuid.uuid4()), "text": data.get("text", "")},
            )

    bus.on(EventTopics.LLM_RESPONSE.value, on_llm_response)
    bus.on(EventTopics.PLAN_READY.value, plan_ready.handler)

    router = IntentRouterService(bus)
    claude = ClaudeService(
        bus,
        {
            "ANTHROPIC_API_KEY": "test-key-not-used",
            "STREAMING": False,
            "FAST_ROUTER_WAIT_S": 2.0,
        },
    )
    await router.start()
    await claude.start()
    stub = StubAnthropic()
    claude._client = stub  # type: ignore[assignment]
    await asyncio.sleep(0.2)

    yield {
        "bus": bus,
        "claude": claude,
        "router": router,
        "stub": stub,
        "llm_final": llm_final,
        "plan_ready": plan_ready,
    }

    await asyncio.sleep(0.2)
    for service in (claude, router):
        try:
            await service.stop()
        except Exception:
            pass
    GATE.reset()


async def _fast_routed_turn(bus: AsyncIOEventEmitter) -> None:
    """Replay what JevIntentService does on a speculative cache hit.

    Byte-for-byte the two things it publishes: `INTENT_DETECTED` tagged with its provenance,
    and the `ActionTaken` record on the gate that `ClaudeService` blocks on.
    """
    GATE.register_router()
    bus.emit(
        EventTopics.VOICE_LISTENING_STOPPED.value,
        {"transcript": TRANSCRIPT, "conversation_id": "turn-1"},
    )
    await asyncio.sleep(0)
    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": "play_music",
            "parameters": {"track": None},
            "confidence": 0.97,
            "original_text": TRANSCRIPT,
            "conversation_id": "turn-1",
            "source": "jev_fast_router",
        },
    )
    GATE.resolve(
        TRANSCRIPT,
        ActionTaken(
            intent_name="play_music",
            parameters={"track": None},
            confidence=0.97,
            transcript=TRANSCRIPT,
            at=time.time(),
        ),
    )


async def test_fast_routed_action_produces_exactly_one_spoken_reply(rig):
    """One transcript in → exactly one final LLM_RESPONSE and one PLAN_READY out."""
    await _fast_routed_turn(rig["bus"])
    await asyncio.sleep(1.5)

    assert rig["llm_final"].count == 1, (
        "expected exactly one spoken reply for this turn, got "
        f"{rig['llm_final'].count}: "
        f"{[e['text'][:60] for e in rig['llm_final'].events]}"
    )
    assert rig["plan_ready"].count == 1, (
        f"expected exactly one timeline plan, got {rig['plan_ready'].count}"
    )


async def test_the_surviving_reply_is_the_main_turn_reply(rig):
    """The kept reply must be the one that saw `<action_already_taken>`.

    The legacy path's request carries the verbal-feedback persona and a bare
    "Intent executed: ..." user message; the main turn's carries the action block. Asserting on
    the request, not the canned response text, is what distinguishes them.
    """
    await _fast_routed_turn(rig["bus"])
    await asyncio.sleep(1.5)

    calls = rig["stub"].messages.calls
    assert len(calls) == 1, (
        f"expected one Anthropic request for this turn, got {len(calls)}: "
        f"{[str(c.get('messages'))[:80] for c in calls]}"
    )
    sent = str(calls[0].get("messages"))
    assert "<action_already_taken>" in sent
    assert "Intent executed:" not in sent


async def test_claude_owned_tool_call_still_gets_its_verbal_response(rig):
    """The legacy path is only suppressed for fast-router turns.

    When Claude calls the tool itself there is no `<action_already_taken>` main-turn reply to
    confirm the action, so `INTENT_EXECUTION_RESULT` must still produce one. Deleting the call
    unconditionally would make Claude's own tool calls silent.
    """
    bus = rig["bus"]
    bus.emit(
        EventTopics.INTENT_DETECTED.value,
        {
            "intent_name": "play_music",
            "parameters": {"track": "cantina"},
            "confidence": 1.0,
            "original_text": "play the cantina song",
            "conversation_id": "turn-2",
            # No `source`: this is a Claude tool call, exactly as ClaudeService emits it.
        },
    )
    await asyncio.sleep(1.0)

    assert rig["llm_final"].count == 1, (
        "a Claude-owned tool call must still be narrated; got "
        f"{rig['llm_final'].count} replies"
    )
