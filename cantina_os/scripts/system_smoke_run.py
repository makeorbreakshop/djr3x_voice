#!/usr/bin/env python
"""Drive a real CantinaOS through a scripted set of voice turns, with hardware mocked.

Why this exists
---------------
Until now the only ways to exercise CantinaOS end to end were (a) sit in front of the
machine, click the mouse and talk into a microphone, or (b) unit tests that stub the bus.
Neither answers "does a transcript actually make the music play on this box". This does:
it boots the real `CantinaOS`, with the real event bus, the real IntentRouterService,
CommandDispatcherService and MusicControllerService (driving real VLC), then injects
transcripts as `VOICE_LISTENING_STOPPED` payloads exactly as the capture services do, and
reports per-turn wall-clock timings from the bus traffic it observes.

What is real and what is not
----------------------------
Real:    event bus, Jev fast intent router (live typesafe.ai API), IntentRouterService,
         CommandDispatcherService, BrainService, TimelineExecutorService,
         MusicControllerService + python-vlc + the real audio/music library,
         LatencyTrackerService, MemoryService, YodaModeManagerService.
Mocked:  Arduino / eye LEDs (FORCE_MOCK_LED_CONTROLLER).
Skipped: the microphone capture service, the mouse trigger, and vision - all three need
         hardware and OS permissions this harness cannot supply. Transcripts are injected
         instead, which is the point.
Off by default: ElevenLabs TTS. It is a paid external API and it makes noise. Pass
         --with-tts to include it.

Usage
-----
    ../venv/bin/python scripts/system_smoke_run.py
    ../venv/bin/python scripts/system_smoke_run.py --with-tts --hold 8
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Mock the LED hardware before anything imports the service.
os.environ.setdefault("FORCE_MOCK_LED_CONTROLLER", "true")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(REPO), ".env"))

from cantina_os.core.event_topics import EventTopics  # noqa: E402
from cantina_os.llm.anthropic_provider import resolve_provider  # noqa: E402
from cantina_os.main import CantinaOS  # noqa: E402

# Services that need hardware or OS permissions this harness cannot provide.
SKIP_SERVICES = {"deepgram_direct_mic", "mouse_input", "vision"}

# Topics worth recording. Anything a "did the action land" question depends on.
WATCH = [
    "MUSIC_COMMAND",
    "EYE_COMMAND",
    "DJ_COMMAND",
    "DJ_MODE_CHANGED",
    "INTENT_DETECTED",
    "INTENT_CONSUMED",
    "INTENT_EXECUTION_RESULT",
    "CLI_COMMAND",
    "CLI_RESPONSE",
    "LLM_RESPONSE",
    "SPEECH_GENERATION_REQUEST",
    "SPEECH_GENERATION_STARTED",
    "SPEECH_GENERATION_COMPLETE",
    "MUSIC_PLAYBACK_STARTED",
    "MUSIC_PLAYBACK_STOPPED",
    "SYSTEM_MODE_CHANGE",
    "VOICE_LISTENING_STOPPED",
]

TURNS: List[Tuple[str, str, List[str]]] = [
    # (label, transcript, topics that must be seen for this turn to count as landed)
    ("play music", "hey Rex play some music", ["MUSIC_COMMAND"]),
    ("next track", "play the next track", ["MUSIC_COMMAND"]),
    ("stop music", "stop the music", ["MUSIC_COMMAND"]),
    ("dj mode on", "start dj mode", ["DJ_COMMAND"]),
    ("dj mode off", "stop dj mode", ["DJ_COMMAND"]),
    ("eye animation", "make your eyes red", ["EYE_COMMAND"]),
    # An LLM reply is the whole point of this turn, so it is the requirement. This row
    # read "n/a" for as long as there was no working LLM credential on the machine.
    ("pure chat", "what is your favourite cantina band", ["LLM_RESPONSE"]),
]


class Recorder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def hook(self, bus, name: str) -> None:
        topic = getattr(EventTopics, name, None)
        if topic is None:
            return
        value = getattr(topic, "value", topic)

        async def handler(payload=None, _name=name):
            self.events.append({"t": time.monotonic(), "topic": _name, "payload": payload})

        bus.on(value, handler)

    def since(self, t0: float) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["t"] >= t0]


async def run(args) -> int:
    print("=" * 78)
    print("CantinaOS system smoke run")
    print("=" * 78)

    provider = resolve_provider()
    have_typesafe = bool(os.getenv("TYPESAFE_API_KEY", "").strip())
    print(f"  LLM provider             : {provider.provider if provider else 'NONE'}"
          f"{f'  ({provider.base_url})' if provider and provider.base_url else ''}")
    print(f"  TYPESAFE_API_KEY present : {have_typesafe}  (Jev fast router)")
    print(f"  VLC app present          : {os.path.isdir('/Applications/VLC.app')}")
    print(f"  TTS enabled              : {args.with_tts}")
    print()

    os_ = CantinaOS()
    bus = os_.event_bus

    rec = Recorder()
    for name in WATCH:
        rec.hook(bus, name)

    # Patch the service order rather than the services themselves, so every service that
    # does start is the real one with its real config.
    original_create = os_._create_service
    skipped: List[str] = []

    def guarded_create(service_name: str):
        if service_name in SKIP_SERVICES or (
            service_name == "elevenlabs" and not args.with_tts
        ):
            skipped.append(service_name)
            return None
        return original_create(service_name)

    os_._create_service = guarded_create  # type: ignore[method-assign]

    print("Initializing services...")
    try:
        await os_._initialize_services()
    except Exception as e:
        print(f"  service init raised: {type(e).__name__}: {e}")

    started = sorted(k for k, v in os_._services.items() if v is not None)
    print(f"  started ({len(started)}): {', '.join(started)}")
    print(f"  skipped: {', '.join(sorted(set(skipped))) or 'none'}")
    print()

    # Put the system into a mode that accepts conversation.
    print("Engaging interactive mode...")
    bus.emit(
        EventTopics.SYSTEM_MODE_CHANGE.value,
        {"new_mode": "INTERACTIVE", "old_mode": "IDLE"},
    )
    await asyncio.sleep(1.5)

    results = []
    loop_ticks = 0

    async def heartbeat():
        """Detects an event-loop freeze - the bug asyncio.to_thread was meant to fix."""
        nonlocal loop_ticks
        while True:
            loop_ticks += 1
            await asyncio.sleep(0.01)

    hb = asyncio.create_task(heartbeat())

    for label, transcript, expect in TURNS:
        turn_id = f"smoke-{uuid.uuid4()}"
        print("-" * 78)
        print(f"TURN: {label}")
        print(f'  transcript: "{transcript}"')
        t0 = time.monotonic()
        ticks_before = loop_ticks

        # Both halves, exactly as a real capture service emits them: STARTED opens the
        # latency tracker's record, STOPPED delivers the transcript and closes the turn.
        bus.emit(
            EventTopics.VOICE_LISTENING_STARTED.value,
            {"conversation_id": turn_id, "timestamp": time.time()},
        )
        await asyncio.sleep(0.05)
        bus.emit(
            EventTopics.VOICE_LISTENING_STOPPED.value,
            {"transcript": transcript, "conversation_id": turn_id},
        )

        deadline = t0 + args.turn_timeout
        while time.monotonic() < deadline:
            seen = {e["topic"] for e in rec.since(t0)}
            if expect and all(x in seen for x in expect):
                break
            await asyncio.sleep(0.01)

        await asyncio.sleep(args.settle)
        evs = rec.since(t0)
        ticks = loop_ticks - ticks_before

        for e in evs:
            ms = (e["t"] - t0) * 1000
            print(f"    {ms:8.1f} ms  {e['topic']}")
        if not evs:
            print("    (no bus traffic)")

        seen = {e["topic"] for e in evs}
        landed = all(x in seen for x in expect) if expect else None
        first_action = next(
            (
                (e["t"] - t0) * 1000
                for e in evs
                if e["topic"] in ("MUSIC_COMMAND", "EYE_COMMAND", "DJ_COMMAND")
            ),
            None,
        )
        results.append(
            {
                "label": label,
                "landed": landed,
                "first_action_ms": first_action,
                "topics": sorted(seen),
                "loop_ticks": ticks,
            }
        )
        print(
            f"  -> landed={landed}  first action={first_action if first_action is None else f'{first_action:.0f} ms'}"
            f"  loop ticks during turn={ticks}"
        )

        if label == "play music" and args.hold:
            print(f"  holding {args.hold}s so VLC actually plays...")
            await asyncio.sleep(args.hold)

    hb.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await hb

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for r in results:
        mark = {True: "PASS", False: "FAIL", None: "n/a "}[r["landed"]]
        ms = "-" if r["first_action_ms"] is None else f"{r['first_action_ms']:.0f} ms"
        print(f"  [{mark}] {r['label']:<16} action at {ms:<9} loop ticks {r['loop_ticks']}")

    tracker = os_._services.get("latency_tracker")
    if tracker is not None:
        print()
        print("LatencyTrackerService (it recorded nothing at all before the")
        print("conversation_id unification landed). Legs stay None here by")
        print("construction: every leg is measured from transcription_complete,")
        print("which needs TRANSCRIPTION_FINAL, and this harness cannot emit that")
        print("without double-processing the turn - ClaudeService subscribes to it")
        print("as well as to VOICE_LISTENING_STOPPED. Legs need the real mic path.")
        metrics = getattr(tracker, "_conversation_metrics", {})
        print(f"  conversations recorded : {len(metrics)}")
        for cid in list(metrics)[:8]:
            s = tracker.get_conversation_summary(cid)
            if s:
                # Print every leg, not only the populated ones - a leg that is present and
                # None is a different (and more useful) fact than a leg that is missing.
                legs = {k: v for k, v in s.items() if k != "conversation_id"}
                print(f"    {cid[:22]}...  {legs}")

    print()
    print("Shutting down...")
    with contextlib.suppress(Exception):
        await os_._cleanup_services()

    failed = [r for r in results if r["landed"] is False]
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-tts", action="store_true", help="also start ElevenLabs (paid, audible)")
    ap.add_argument("--hold", type=float, default=6.0, help="seconds to let music actually play")
    ap.add_argument("--turn-timeout", type=float, default=15.0)
    ap.add_argument("--settle", type=float, default=1.5)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
