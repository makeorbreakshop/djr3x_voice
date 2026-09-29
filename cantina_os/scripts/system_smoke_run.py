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

Show system (``--show``)
------------------------
Runs three show scenarios instead of the voice turns, printing every show/department event
with its millisecond offset:

1. ``show <id>`` typed at the CLI -> dispatcher -> timeline -> timed department events;
2. ``dj start`` -> BrainService -> ``dj_intro`` on the show plan layer;
3. a real Claude turn ("say hi to everyone and get hyped") -> inline tags stripped from the
   spoken text -> ``show.perform {source: claude}`` after speech start.

Without ``--with-tts`` a speech *stub* stands in for ElevenLabs: it answers LLM_RESPONSE and
TTS_GENERATE_REQUEST with SPEECH_GENERATION_STARTED / _COMPLETE exactly as the real service
emits them (duration = chars / 15 s). If the repo's ``show/`` folder has no content yet, a
small temporary show set is written to a temp dir and used via SHOW_DIR (never into show/).
Run it with ``env -u ANTHROPIC_BASE_URL`` from an agent shell (CLAUDE.md section 9).

    env -u ANTHROPIC_BASE_URL ../venv/bin/python scripts/system_smoke_run.py --show
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

import json  # noqa: E402
import tempfile  # noqa: E402

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
    "SPEECH_CACHE_READY",
    "DJ_NEXT_TRACK_SELECTED",
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

        if label == "dj mode on":
            # BrainService caches the next track's commentary from a free-running 15 s poll,
            # and the mp3 step blocks the event loop for seconds. Left to chance it lands on a
            # later turn at a different point each run, so a replay could not reproduce the
            # trace. Let it finish here (recorded runs, R3X_FIXTURES, rely on this).
            t_dj = time.monotonic()
            def _cached_next() -> bool:
                picked = [e["t"] for e in rec.since(t0) if e["topic"] == "DJ_NEXT_TRACK_SELECTED"]
                return bool(picked) and any(e["topic"] == "SPEECH_CACHE_READY" for e in rec.since(picked[0]))

            while time.monotonic() - t_dj < 40 and not _cached_next():
                await asyncio.sleep(0.1)
            await asyncio.sleep(args.settle)

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


# ============================================================================ show scenarios

SHOW_WATCH = [
    "CLI_COMMAND", "SHOW_COMMAND", "SHOW_PERFORM", "SHOW_STARTED", "SHOW_ENDED", "SHOW_MOTION",
    "SHOW_SFX", "STAGE_LIGHTS", "CHEST_OVERRIDE", "EYE_COMMAND", "AUDIO_DUCKING_START",
    "AUDIO_DUCKING_STOP", "TTS_GENERATE_REQUEST", "PLAN_READY", "DJ_MODE_CHANGED",
    "LLM_RESPONSE", "SPEECH_GENERATION_STARTED", "SPEECH_GENERATION_COMPLETE",
    "VOICE_LISTENING_STOPPED", "INTENT_DETECTED", "MUSIC_COMMAND",
]


def _write_temp_show_set(root: str) -> None:
    """A tiny show set for the smoke run when show/ has no content yet. NOT written to show/."""
    def item(folder, data):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        with open(os.path.join(root, folder, data["id"] + ".json"), "w") as f:
            json.dump(data, f, indent=1)

    add = lambda keys: {"mode": "additive", "keys": keys}
    item("clips", {"id": "nod", "kind": "clip", "tier": "free", "description": "Quick yes-nod",
                   "duration": 0.9, "interruptible_after": 0.3,
                   "tracks": {"head_tilt": add([[0, 0], [0.25, -8], [0.5, 4], [0.9, 0]])}})
    item("clips", {"id": "wave", "kind": "clip", "tier": "free", "description": "A friendly hello wave with the hero arm",
                   "duration": 1.2, "tracks": {"hero_wrist": add([[0, 0], [0.3, 20], [0.6, -20], [0.9, 20], [1.2, 0]])}})
    item("clips", {"id": "arm_throw", "kind": "clip", "tier": "cheap", "description": "Big arm throw",
                   "duration": 1.0, "tracks": {"hero_shoulder": add([[0, 0], [0.3, 40], [1.0, 0]])}})
    item("clips", {"id": "visor_flip", "kind": "clip", "tier": "free", "description": "Flip the visor up and back",
                   "duration": 0.8, "tracks": {"visor": {"mode": "override", "keys": [[0, 0], [0.3, 60], [0.8, 0]]}}})
    item("cues", {"id": "hype_drop", "kind": "cue", "tier": "cheap",
                  "description": "Arms up, amber eyes, red lights, airhorn - for big hype moments",
                  "actions": [
                      {"at": 0.0, "do": "clip", "id": "arm_throw", "intensity": 1.0},
                      {"at": 0.0, "do": "eyes", "pattern": "happy", "color": "#ffb000", "duration": 1.5},
                      {"at": 0.0, "do": "chest", "command": "X2", "hold": 1.5},
                      {"at": 0.0, "do": "lights", "cue": "red_flash", "fade": 0.1, "hold": 2.0},
                      {"at": 0.05, "do": "sfx", "id": "airhorn"}]})
    item("cues", {"id": "greet", "kind": "cue", "tier": "free", "description": "Wave hello with a visor flip",
                  "actions": [{"at": 0.0, "do": "clip", "id": "wave"},
                              {"at": 0.2, "do": "clip", "id": "visor_flip"}]})
    item("sequences", {"id": "dj_intro", "kind": "sequence", "tier": "show", "clock": "beat", "bpm": 120,
                       "description": "The DJ intro: hype drop, visor reveal, 'Time to boogie!'",
                       "layer": "show", "owns": ["head_pan", "visor"],
                       "track": [{"at": 0, "cue": "hype_drop"}, {"at": 2, "clip": "visor_flip"},
                                 {"at": 4, "do": "speak", "text": "Time to boogie!"},
                                 {"at": 4, "do": "wait", "for": "speech_end"},
                                 {"at": 5, "do": "lights", "mode": "dj"}]})


def _install_speech_stub(bus) -> None:
    """Stand-in for ElevenLabs: the same STARTED/COMPLETE events, no audio, no API."""
    loop = asyncio.get_running_loop()

    def speak(cid, text, clip_id=None):
        bus.emit(EventTopics.SPEECH_GENERATION_STARTED.value,
                 {"conversation_id": cid, "text": text, "clip_id": clip_id})
        done = {"conversation_id": cid, "text": text, "audio_length_seconds": len(text) / 15,
                "success": True, "clip_id": clip_id, "step_id": clip_id}
        loop.call_later(len(text) / 15, lambda: bus.emit(EventTopics.SPEECH_GENERATION_COMPLETE.value, done))

    def on_llm(p):
        if p.get("is_complete") and (p.get("text") or "").strip():
            speak(p.get("conversation_id"), p["text"].strip(), p.get("conversation_id"))

    def on_tts(p):
        speak(p.get("conversation_id") or p.get("clip_id"), p.get("text", ""), p.get("clip_id"))

    bus.on(EventTopics.LLM_RESPONSE.value, on_llm)
    bus.on(EventTopics.TTS_GENERATE_REQUEST.value, on_tts)


def _summ(topic: str, p: Any) -> str:
    if not isinstance(p, dict):
        return ""
    keys = {
        "SHOW_PERFORM": ("id", "source", "conversation_id"), "SHOW_STARTED": ("id", "kind", "source"),
        "SHOW_ENDED": ("id", "reason"), "SHOW_MOTION": ("clip", "intensity", "speed", "layer"),
        "SHOW_SFX": ("id",), "STAGE_LIGHTS": ("cue", "mode", "fade", "hold"),
        "CHEST_OVERRIDE": ("command", "hold"), "EYE_COMMAND": ("pattern", "color", "command"),
        "TTS_GENERATE_REQUEST": ("text",), "CLI_COMMAND": ("raw_input",), "SHOW_COMMAND": ("raw_input",),
        "DJ_MODE_CHANGED": ("is_active",), "SPEECH_GENERATION_STARTED": ("conversation_id", "text"),
        "SPEECH_GENERATION_COMPLETE": ("clip_id",), "INTENT_DETECTED": ("intent_name",),
        "MUSIC_COMMAND": ("action", "song_query"), "LLM_RESPONSE": ("is_complete", "text"),
    }.get(topic, ())
    parts = []
    for k in keys:
        v = p.get(k)
        if v is not None:
            v = v if not isinstance(v, str) else (v if len(v) <= 90 else v[:87] + "...")
            parts.append(f"{k}={v!r}")
    return " ".join(parts)


async def run_show(args) -> int:
    print("=" * 78)
    print("CantinaOS show-system smoke run")
    print("=" * 78)
    from cantina_os.show import load_library, resolve_show_dir

    repo_show = resolve_show_dir()
    if len(load_library(repo_show)) == 0:
        tmp = tempfile.mkdtemp(prefix="r3x-show-smoke-")
        _write_temp_show_set(tmp)
        os.environ["SHOW_DIR"] = tmp
        print(f"  {repo_show} has no content yet -> temporary show set at {tmp}")
    else:
        print(f"  show folder: {repo_show} ({len(load_library(repo_show))} items)")
    provider = resolve_provider()
    print(f"  LLM provider: {provider.provider if provider else 'NONE'}   TTS: "
          f"{'real ElevenLabs' if args.with_tts else 'speech stub (same events, no audio)'}")

    os_ = CantinaOS()
    bus = os_.event_bus
    rec = Recorder()
    for name in SHOW_WATCH:
        rec.hook(bus, name)
    original_create = os_._create_service

    def guarded_create(service_name: str):
        if service_name in SKIP_SERVICES or (service_name == "elevenlabs" and not args.with_tts):
            return None
        return original_create(service_name)

    os_._create_service = guarded_create  # type: ignore[method-assign]
    await os_._initialize_services()
    # As CantinaOS.run() does after init: without this the dispatcher knows no 'show'.
    await os_._register_commands(os_._services["command_dispatcher"])
    if not args.with_tts:
        _install_speech_stub(bus)
    bus.emit(EventTopics.SYSTEM_MODE_CHANGE.value, {"new_mode": "INTERACTIVE", "old_mode": "IDLE"})
    await asyncio.sleep(1.5)

    def dump(t0: float) -> List[Dict[str, Any]]:
        evs = rec.since(t0)
        for e in evs:
            print(f"    {(e['t'] - t0) * 1000:8.1f} ms  {e['topic']:<27} {_summ(e['topic'], e['payload'])}")
        return evs

    def cli(raw: str) -> None:
        bus.emit(EventTopics.CLI_COMMAND.value, {"command": raw.split()[0], "args": raw.split()[1:],
                                                 "raw_input": raw, "timestamp": time.time()})

    ok = {}
    # 1. CLI
    print("-" * 78)
    print("SCENARIO 1: CLI 'show hype_drop'")
    t0 = time.monotonic()
    cli("show hype_drop")
    await asyncio.sleep(2.5)
    evs = dump(t0)
    topics = {e["topic"] for e in evs}
    ok["cli show"] = {"SHOW_MOTION", "EYE_COMMAND", "CHEST_OVERRIDE", "STAGE_LIGHTS", "SHOW_SFX"} <= topics

    # 2. DJ start -> dj_intro
    print("-" * 78)
    print("SCENARIO 2: CLI 'dj start' -> dj_intro")
    t0 = time.monotonic()
    cli("dj start")
    await asyncio.sleep(args.dj_hold)
    cli("dj stop")
    await asyncio.sleep(1.0)
    evs = dump(t0)
    ok["dj_intro"] = any(e["topic"] == "SHOW_STARTED" and e["payload"].get("id") == "dj_intro" for e in evs)

    # 3. Real Claude turn with tags
    print("-" * 78)
    transcript = args.show_transcript
    print(f'SCENARIO 3: voice turn "{transcript}" (real Claude)')
    turn_id = f"smoke-{uuid.uuid4()}"
    t0 = time.monotonic()
    bus.emit(EventTopics.VOICE_LISTENING_STARTED.value, {"conversation_id": turn_id, "timestamp": time.time()})
    await asyncio.sleep(0.05)
    bus.emit(EventTopics.VOICE_LISTENING_STOPPED.value, {"transcript": transcript, "conversation_id": turn_id})
    await asyncio.sleep(args.turn_timeout)
    evs = dump(t0)
    finals = [e for e in evs if e["topic"] == "LLM_RESPONSE" and e["payload"].get("is_complete")]
    chunks = [e for e in evs if e["topic"] == "LLM_RESPONSE" and not e["payload"].get("is_complete")]
    started = next((e for e in evs if e["topic"] == "SPEECH_GENERATION_STARTED"
                    and e["payload"].get("conversation_id") == turn_id), None)
    claude_performs = [e for e in evs if e["topic"] == "SHOW_PERFORM" and e["payload"].get("source") == "claude"]
    claude_svc = os_._services.get("claude")
    raw = claude_svc._memory.messages[-1].content if claude_svc and claude_svc._memory.messages else ""
    print(f"  Claude raw reply (SessionMemory): {raw!r}")
    if finals:
        print(f"  spoken/final text (LLM_RESPONSE): {finals[-1]['payload']['text']!r}")
    leaked = [e for e in chunks + finals if "{" in (e["payload"].get("text") or "")]
    print(f"  chunks={len(chunks)}  brace leaks={len(leaked)}  claude show.perform={len(claude_performs)}")
    from cantina_os.show.tags import extract_tags

    clean, tags, _ = extract_tags(raw, valid=claude_svc._show_catalog.taggable if claude_svc else None)
    lead = len(clean) - len(clean.lstrip())
    cps = float(claude_svc._show_tags.chars_per_sec) if claude_svc else 15.0
    tool_ids = [e["payload"]["intent_name"] for e in evs if e["topic"] == "INTENT_DETECTED"]
    print(f"  tags in reply: {[(t.kind, t.id, t.offset - lead) for t in tags]}   tool calls: {tool_ids}")
    tag_performs = []
    if started:
        remaining = list(claude_performs)
        for t in tags:
            match = next((e for e in remaining if e["payload"]["id"] == t.id and e["t"] >= started["t"]), None)
            if match:
                remaining.remove(match)
                tag_performs.append(match)
                print(f"    tag {t.kind}:{t.id} -> show.perform at +{(match['t'] - started['t']) * 1000:.0f} ms "
                      f"after speech start (expected {(t.offset - lead) / cps * 1000:.0f} ms)")
        for e in remaining:
            print(f"    tool -> show.perform {e['payload']['id']!r} at {(e['t'] - t0) * 1000:.0f} ms")
    ok["claude tags"] = bool(finals) and not leaked and bool(tags) and len(tag_performs) == len(tags)

    print()
    print("=" * 78)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    with contextlib.suppress(Exception):
        await os_._cleanup_services()
    return 0 if all(ok.values()) else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-tts", action="store_true", help="also start ElevenLabs (paid, audible)")
    ap.add_argument("--hold", type=float, default=6.0, help="seconds to let music actually play")
    ap.add_argument("--turn-timeout", type=float, default=15.0)
    ap.add_argument("--settle", type=float, default=1.5)
    ap.add_argument("--show", action="store_true", help="run the show-system scenarios instead")
    ap.add_argument("--show-transcript", default="say hi to everyone and get hyped")
    ap.add_argument("--dj-hold", type=float, default=6.0, help="seconds to let dj_intro run")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run_show(args) if args.show else run(args)))


if __name__ == "__main__":
    main()
