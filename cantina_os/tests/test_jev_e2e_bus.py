"""
End-to-end latency test for the Jev fast intent router, on the real CantinaOS event bus.

## What is real here

* the real `pyee` event bus (`EventBus`),
* the real `JevIntentService`, calling the **real** Jev API over the network,
* the real `IntentRouterService`,
* the real `CommandDispatcherService`, with commands registered exactly as `main.py` does,
* the real `FastRouterGate` rendezvous.

## What is mocked

* **the microphone** — `VOICE_LISTENING_STOPPED` is injected directly, which is byte-identical
  to what `DeepgramDirectMicService` emits at the end of a click-to-stop recording;
* **the hardware** — `MusicControllerService` and `EyeLightControllerService` are replaced by
  probes that record the `MUSIC_COMMAND` / `EYE_COMMAND` they receive. VLC and the Arduino are
  not driven.
* **Anthropic** — a stub client that records the kwargs it was called with (so `tool_choice`
  can be asserted) and returns a canned reply after a realistic delay.

The measured number is therefore *transcript event emitted → MUSIC_COMMAND observed*, which is
precisely the interval the audit measured at 1,899 ms on the Claude path.

Requires `TYPESAFE_API_KEY`; skipped without it. Costs ~$0.0003 per run.
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import pytest
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.core.event_topics import EventTopics
from cantina_os.core.fast_router_gate import GATE
from cantina_os.services.command_dispatcher_service import CommandDispatcherService
from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.services.jev_intent_service import JevIntentService

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("TYPESAFE_API_KEY"),
    reason="TYPESAFE_API_KEY not set; the E2E test calls the real Jev API",
)

#: Warm, measured: p50 197 ms transcript -> MUSIC_COMMAND, of which ~195 ms is the Jev network
#: round trip and 2-3 ms is all of CantinaOS. The network floor is why the brief's ~100 ms is
#: not reachable on the non-speculative path; see test_speculative_classification_beats_the_
#: cold_path for the case that is. This ceiling leaves room for the p95 tail while still
#: catching a regression back into second-scale territory.
LATENCY_CEILING_MS = 600.0


@pytest.fixture(autouse=True)
def mock_external_apis():
    """Override conftest's autouse fixture, which raises on deepgram-sdk 5.x. See
    test_jev_intent_router.py for the detail."""
    yield {}


class BusProbe:
    """Records every payload seen on a topic, with a monotonic timestamp."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def handler(self, payload: Dict[str, Any]) -> None:
        self.events.append({"at": time.perf_counter(), "payload": payload})

    @property
    def count(self) -> int:
        return len(self.events)

    def first_at(self) -> Optional[float]:
        return self.events[0]["at"] if self.events else None


class StubAnthropicMessages:
    """Stands in for `client.messages`, recording how it was called."""

    def __init__(self, delay_s: float = 0.25) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._delay_s = delay_s

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        # Simulate a real round trip so the test exercises the same ordering production sees.
        time.sleep(self._delay_s)

        class _Text:
            type = "text"
            text = "Oh YEAH! That track is already spinning!"

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
    def __init__(self, delay_s: float = 0.25) -> None:
        self.messages = StubAnthropicMessages(delay_s)


@pytest.fixture
async def rig():
    """Bring up the real dispatch chain with hardware replaced by probes."""
    GATE.reset()
    bus = AsyncIOEventEmitter()

    probes = {
        "music": BusProbe(),
        "eye": BusProbe(),
        "dj": BusProbe(),
        "intent_detected": BusProbe(),
        "intent_consumed": BusProbe(),
        "cli": BusProbe(),
    }
    bus.on(EventTopics.MUSIC_COMMAND.value, probes["music"].handler)
    bus.on(EventTopics.EYE_COMMAND.value, probes["eye"].handler)
    bus.on(EventTopics.DJ_COMMAND.value, probes["dj"].handler)
    bus.on(EventTopics.INTENT_DETECTED.value, probes["intent_detected"].handler)
    bus.on(EventTopics.INTENT_CONSUMED.value, probes["intent_consumed"].handler)
    bus.on(EventTopics.CLI_COMMAND.value, probes["cli"].handler)

    dispatcher = CommandDispatcherService(bus)
    router = IntentRouterService(bus)
    jev = JevIntentService(bus, {"JEV_SPECULATE": False})

    for service in (dispatcher, router, jev):
        await service.start()

    # Exactly the registrations main.py performs for the commands under test.
    for command, service_name, topic in [
        ("play music", "music_controller", EventTopics.MUSIC_COMMAND),
        ("stop music", "music_controller", EventTopics.MUSIC_COMMAND),
        ("eye pattern", "eye_controller", EventTopics.EYE_COMMAND),
        ("dj start", "brain_service", EventTopics.DJ_COMMAND),
        ("dj stop", "brain_service", EventTopics.DJ_COMMAND),
        ("dj next", "brain_service", EventTopics.DJ_COMMAND),
    ]:
        dispatcher.register_command(command, service_name, topic)

    await asyncio.sleep(0.2)  # let subscriptions settle

    # Pre-warm the Jev connection, which production does on `engage` via SYSTEM_MODE_CHANGED.
    # Without this the first measurement pays a ~200 ms TLS handshake that a real session
    # would already have paid, and the number stops being representative.
    await jev._client.prewarm()

    yield {"bus": bus, "probes": probes, "jev": jev, "router": router, "dispatcher": dispatcher}

    for service in (jev, router, dispatcher):
        try:
            await service.stop()
        except Exception:
            pass
    GATE.reset()


async def _inject_transcript(bus: AsyncIOEventEmitter, transcript: str) -> float:
    """Emit what DeepgramDirectMicService emits when the click stops recording."""
    started = time.perf_counter()
    bus.emit(
        EventTopics.VOICE_LISTENING_STOPPED.value,
        {"transcript": transcript, "conversation_id": "e2e-test"},
    )
    return started


async def _wait_for(probe: BusProbe, timeout_s: float = 5.0) -> bool:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if probe.count:
            return True
        await asyncio.sleep(0.002)
    return False


# ------------------------------------------------------------------------------------------
# The headline measurement
# ------------------------------------------------------------------------------------------

async def test_transcript_to_music_command_latency(rig):
    """transcript → MUSIC_COMMAND on the real bus, through the real dispatch chain."""
    bus, probes = rig["bus"], rig["probes"]

    started = await _inject_transcript(bus, "hey Rex play some music")
    assert await _wait_for(probes["music"]), "no MUSIC_COMMAND was ever emitted"

    first_at = probes["music"].first_at()
    assert first_at is not None
    elapsed_ms = (first_at - started) * 1000

    payload = probes["music"].events[0]["payload"]
    print(
        f"\n=== transcript -> MUSIC_COMMAND: {elapsed_ms:.0f} ms ===\n"
        f"    payload: {json.dumps(payload, default=str)[:200]}\n"
        f"    (audit baseline on the Claude path: 1899 ms)"
    )

    assert elapsed_ms < LATENCY_CEILING_MS, f"regressed to {elapsed_ms:.0f} ms"
    assert probes["intent_detected"].count == 1
    assert probes["intent_detected"].events[0]["payload"]["source"] == "jev_fast_router"


async def test_latency_breakdown_over_repeated_turns(rig):
    """Where the time actually goes, over 5 warm turns.

    Splits the interval into the Jev API round trip (reported by the client) and everything
    CantinaOS does with the answer, so a future regression can be attributed rather than
    guessed at. The audit measured the whole bus + tool-routing chain at 15 ms; this checks
    that is still true.
    """
    bus, probes, jev = rig["bus"], rig["probes"], rig["jev"]
    totals: List[float] = []

    for _ in range(5):
        probes["music"].events.clear()
        started = await _inject_transcript(bus, "hey Rex play some music")
        assert await _wait_for(probes["music"])
        totals.append((probes["music"].first_at() - started) * 1000)
        await asyncio.sleep(0.05)

    api = jev._stats["latency_ms"][-5:]
    ordered = sorted(totals)
    overhead = [t - a for t, a in zip(totals, api)]
    print(
        "\n=== warm transcript -> MUSIC_COMMAND over 5 turns ===\n"
        f"    total     p50={ordered[len(ordered) // 2]:.0f} ms  "
        f"min={ordered[0]:.0f}  max={ordered[-1]:.0f}\n"
        f"    jev api   {[f'{a:.0f}' for a in api]}\n"
        f"    cantinaOS {[f'{o:.0f}' for o in overhead]} ms "
        "(bus + intent router + dispatcher)\n"
        f"    audit baseline on the Claude path: 1899 ms"
    )
    # CantinaOS's own handling must stay trivial next to the network call.
    assert max(overhead) < 120, f"bus/routing overhead grew to {max(overhead):.0f} ms"


async def test_speculative_classification_beats_the_cold_path(rig):
    """A finalized STT segment arriving before the click lets dispatch happen at ~0 ms.

    This is the mechanism that gets under the ~200 ms floor the network imposes: the API
    latency is paid while the user is still talking.
    """
    bus, probes = rig["bus"], rig["probes"]
    jev = rig["jev"]
    jev._speculate_enabled = True

    transcript = "hey Rex play some music"

    # A new turn starts, then Deepgram finalizes the segment (this fires during recording).
    bus.emit(EventTopics.VOICE_LISTENING_STARTED.value, {"conversation_id": "spec"})
    await asyncio.sleep(0.01)
    await jev._speculate(transcript, debounce=False)

    # Wait for the speculative answer to land, as it would while the user finishes speaking.
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline and not jev._speculative:
        await asyncio.sleep(0.005)
    assert jev._speculative, "no speculative answer was cached"

    # Now the click stops recording and the authoritative transcript arrives.
    started = await _inject_transcript(bus, transcript)
    assert await _wait_for(probes["music"])
    elapsed_ms = (probes["music"].first_at() - started) * 1000

    print(f"\n=== speculative cache hit: transcript -> MUSIC_COMMAND {elapsed_ms:.1f} ms ===")
    assert jev._stats["speculative_hits"] >= 1, "the cache was not used"
    assert elapsed_ms < 60, f"cache hit still took {elapsed_ms:.0f} ms"


async def test_stop_music_latency(rig):
    bus, probes = rig["bus"], rig["probes"]
    started = await _inject_transcript(bus, "stop the music")
    assert await _wait_for(probes["music"])
    elapsed_ms = (probes["music"].first_at() - started) * 1000
    print(f"\n=== transcript -> stop MUSIC_COMMAND: {elapsed_ms:.0f} ms ===")
    assert elapsed_ms < LATENCY_CEILING_MS


async def test_dj_mode_reaches_the_dj_command_topic(rig):
    """DJ mode is newly reachable from voice — Claude has no tool for it at all."""
    bus, probes = rig["bus"], rig["probes"]
    started = await _inject_transcript(bus, "start DJ mode")
    assert await _wait_for(probes["dj"]), "no DJ_COMMAND emitted"
    elapsed_ms = (probes["dj"].first_at() - started) * 1000
    print(f"\n=== transcript -> DJ_COMMAND: {elapsed_ms:.0f} ms ===")
    assert elapsed_ms < LATENCY_CEILING_MS


async def test_eye_animation_reaches_the_eye_command_topic(rig):
    bus, probes = rig["bus"], rig["probes"]
    started = await _inject_transcript(bus, "make your eyes blue")
    assert await _wait_for(probes["eye"]), "no EYE_COMMAND emitted"
    elapsed_ms = (probes["eye"].first_at() - started) * 1000
    print(f"\n=== transcript -> EYE_COMMAND: {elapsed_ms:.0f} ms ===")
    assert elapsed_ms < LATENCY_CEILING_MS


async def test_conversation_dispatches_nothing_on_the_real_bus(rig):
    """The safety property, end to end: chat must reach no hardware topic at all."""
    bus, probes = rig["bus"], rig["probes"]
    await _inject_transcript(bus, "what's your favorite planet")
    await asyncio.sleep(2.0)  # generous: well beyond the Jev round trip

    assert probes["music"].count == 0, "conversation triggered a music command"
    assert probes["eye"].count == 0
    assert probes["dj"].count == 0
    assert probes["intent_detected"].count == 0
    assert probes["intent_consumed"].count == 0


async def test_intent_consumed_is_emitted_for_dedup(rig):
    """The bus-visible record other services can observe."""
    bus, probes = rig["bus"], rig["probes"]
    await _inject_transcript(bus, "stop the music")
    assert await _wait_for(probes["intent_consumed"])
    payload = probes["intent_consumed"].events[0]["payload"]
    assert payload["intent_name"] == "stop_music"
    assert payload["source"] == "jev_fast_router"
    assert payload["tier"] == "cheap"


# ------------------------------------------------------------------------------------------
# Claude's reply path still runs, and does not re-trigger the tool
# ------------------------------------------------------------------------------------------

@pytest.fixture
async def claude_rig(rig):
    """Add a real ClaudeService with a stubbed Anthropic client to the rig."""
    from cantina_os.services.claude_service.claude_service import ClaudeService

    bus = rig["bus"]
    claude = ClaudeService(
        bus,
        {
            "ANTHROPIC_API_KEY": "test-key-not-used",
            "STREAMING": False,  # exercise the non-streaming path; both take the same branch
            "FAST_ROUTER_WAIT_S": 3.0,
        },
    )
    await claude.start()
    stub = StubAnthropic(delay_s=0.2)
    claude._client = stub  # type: ignore[assignment]
    await asyncio.sleep(0.2)

    yield {**rig, "claude": claude, "stub": stub}

    # Let any in-flight turn finish before tearing down, otherwise pytest reports
    # "Task was destroyed but it is pending" for ClaudeService's handler.
    await asyncio.sleep(0.3)
    try:
        await claude.stop()
    except Exception:
        pass


async def test_claude_speaks_but_cannot_call_the_tool_again(claude_rig):
    """The dedup contract: Claude still replies, with tools forbidden.

    This is the regression that would otherwise restart the music a second time after the
    fast router already started it.
    """
    bus, probes, stub = claude_rig["bus"], claude_rig["probes"], claude_rig["stub"]
    llm = BusProbe()
    bus.on(EventTopics.LLM_RESPONSE.value, llm.handler)

    started = await _inject_transcript(bus, "hey Rex play some music")

    # The action happens first and fast...
    assert await _wait_for(probes["music"]), "no MUSIC_COMMAND emitted"
    action_ms = (probes["music"].first_at() - started) * 1000

    # ...and the spoken reply follows, later.
    assert await _wait_for(llm, timeout_s=10.0), "Claude never produced a reply"
    reply_ms = (llm.first_at() - started) * 1000

    print(
        f"\n=== action at {action_ms:.0f} ms, spoken reply at {reply_ms:.0f} ms "
        f"(action leads by {reply_ms - action_ms:.0f} ms) ==="
    )
    assert action_ms < reply_ms, "the action must not wait on the spoken reply"

    # The critical assertion: Claude was called with tools forbidden.
    # CHANGED 2026-09-17: the main turn used to be identified by max_tokens == 1024, which is
    # now the spoken-reply budget (SPOKEN_REPLY_MAX_TOKENS). Identify it by what actually makes
    # it the main turn instead: it is the call that carries the tool schemas.
    turn_calls = [c for c in stub.messages.calls if c.get("tools")]
    assert turn_calls, f"ClaudeService never made its main turn call; saw {len(stub.messages.calls)}"
    main_call = turn_calls[0]
    assert main_call.get("tool_choice") == {"type": "none"}, (
        f"tool_choice was {main_call.get('tool_choice')!r}; Claude could re-trigger the tool"
    )

    # And it was told what had already happened.
    prompt = json.dumps(main_call["messages"], default=str)
    assert "<action_already_taken>" in prompt
    assert "play_music" in prompt

    # Exactly one music command for the turn.
    assert probes["music"].count == 1, f"tool fired {probes['music'].count} times"


async def test_claude_keeps_its_tools_when_the_router_declines(claude_rig):
    """Conversation must reach Claude with tools *enabled* and no action context.

    Without this the router would silently disarm Claude on every turn.
    """
    bus, probes, stub = claude_rig["bus"], claude_rig["probes"], claude_rig["stub"]

    await _inject_transcript(bus, "what's your favorite planet")

    deadline = time.perf_counter() + 10.0
    while time.perf_counter() < deadline and not stub.messages.calls:
        await asyncio.sleep(0.01)
    assert stub.messages.calls, "Claude was never called for a conversational turn"

    main_call = stub.messages.calls[0]
    assert "tool_choice" not in main_call, "tools were wrongly suppressed on a chat turn"
    prompt = json.dumps(main_call["messages"], default=str)
    assert "<action_already_taken>" not in prompt
    assert probes["music"].count == 0


async def test_claude_path_is_not_blocked_while_it_waits(claude_rig):
    """The gate wait must not freeze the event loop — other services keep running.

    Regression guard for the audit's central finding: a 1,817 ms window in which not one of 22
    services logged a line.
    """
    bus = claude_rig["bus"]
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(200):
            ticks += 1
            await asyncio.sleep(0.005)

    beat = asyncio.create_task(heartbeat())
    await _inject_transcript(bus, "hey Rex play some music")
    await asyncio.sleep(1.0)
    beat.cancel()

    # ~200 ticks are possible in 1 s; anything above a few dozen proves the loop kept turning
    # through both the Jev call and the (stubbed, blocking) Claude call.
    print(f"\n=== event loop ticked {ticks} times during the turn ===")
    assert ticks > 50, f"event loop was starved: only {ticks} ticks"
