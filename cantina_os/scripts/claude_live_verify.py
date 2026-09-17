#!/usr/bin/env python
"""Measure the three Claude turn shapes against the live LLM, on a real CantinaOS.

Why this exists
---------------
``dev_logs/2026-09-17_dev_log.md`` closed with one assertion it could not make: that
``tool_choice: {"type": "none"}`` is honoured by the live API when the Jev fast router has
already executed the turn's action. There was no Anthropic key with credit, so the
suppression was *documented, not measured*. Routing the SDK at OpenRouter's
Anthropic-compatible endpoint removed that blocker, and this script makes the measurement.

Three turns, one for each path a transcript can take:

1. **fast-routed action** - "hey Rex play some music". Jev dispatches ``MUSIC_COMMAND``
   before Claude is called; Claude receives the ``action_already_taken`` block with
   ``tool_choice: none``. Asserts: no ``tool_use`` block came back, a spoken reply was
   produced, and the music tool was *not* dispatched a second time.
2. **pure chat** - "what's your favourite planet". No action, normal reply.
3. **Claude's own tool path** - a transcript Jev declines. Claude's unsuppressed tool path
   runs and stops the music with its own tool call. This is the control for turn 1: it
   proves the suppression in turn 1 was the cause, not just an absence.

Each turn reports transcript -> action, transcript -> first Claude token, and
transcript -> full reply, read off real bus traffic.

Real: the event bus, Jev against the live typesafe.ai API, IntentRouterService,
CommandDispatcherService, BrainService, ClaudeService against the live LLM,
MusicControllerService + real VLC + the real music library, LatencyTrackerService.
Mocked: the Arduino / eye LEDs. Skipped: mic, mouse trigger, vision (hardware).
Off: ElevenLabs (paid and audible) unless --with-tts.

Usage
-----
    ../venv/bin/python scripts/claude_live_verify.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault("FORCE_MOCK_LED_CONTROLLER", "true")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(REPO), ".env"))

from cantina_os.core.event_topics import EventTopics  # noqa: E402
from cantina_os.llm.anthropic_provider import resolve_provider  # noqa: E402
from cantina_os.main import CantinaOS  # noqa: E402

SKIP_SERVICES = {"deepgram_direct_mic", "mouse_input", "vision"}

WATCH = [
    "VOICE_LISTENING_STOPPED",
    "INTENT_DETECTED",
    "INTENT_CONSUMED",
    "INTENT_EXECUTION_RESULT",
    "CLI_COMMAND",
    "MUSIC_COMMAND",
    "DJ_COMMAND",
    "EYE_COMMAND",
    "MUSIC_PLAYBACK_STARTED",
    "MUSIC_PLAYBACK_STOPPED",
    "LLM_RESPONSE",
]

# (key, label, transcript, seconds to wait after the reply lands)
TURNS = [
    ("fast_action", "fast-routed action (tool_choice none)", "hey Rex play some music", 4.0),
    ("pure_chat", "pure chat", "what's your favorite planet", 1.5),
    ("claude_tool", "Jev declines -> Claude's own tool call", "quiet please", 3.0),
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


def _payload_get(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


async def run(args) -> int:
    provider = resolve_provider()
    print("=" * 78)
    print("Claude live verification - the assertion the Sep 17 log could not make")
    print("=" * 78)
    print(f"  LLM provider  : {provider.provider if provider else 'NONE - cannot run'}")
    print(f"  base_url      : {provider.base_url if provider else '-'}")
    print(f"  typesafe key  : {bool(os.getenv('TYPESAFE_API_KEY', '').strip())}  (Jev)")
    print(f"  VLC present   : {os.path.isdir('/Applications/VLC.app')}")
    print(f"  TTS enabled   : {args.with_tts}")
    print()
    if provider is None:
        return 2

    os_ = CantinaOS()
    bus = os_.event_bus
    rec = Recorder()
    for name in WATCH:
        rec.hook(bus, name)

    original_create = os_._create_service

    def guarded_create(service_name: str):
        if service_name in SKIP_SERVICES or (
            service_name == "elevenlabs" and not args.with_tts
        ):
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

    claude = os_._services.get("claude")
    if claude is not None:
        print(f"  claude model  : {claude._config['MODEL']}")
        print(f"  streaming     : {claude._config['STREAMING']}")
    print()

    bus.emit(
        EventTopics.SYSTEM_MODE_CHANGE.value,
        {"new_mode": "INTERACTIVE", "old_mode": "IDLE"},
    )
    await asyncio.sleep(1.5)

    results: List[Dict[str, Any]] = []

    for key, label, transcript, settle in TURNS:
        turn_id = f"verify-{uuid.uuid4()}"
        print("-" * 78)
        print(f"TURN {key}: {label}")
        print(f'  transcript: "{transcript}"')
        t0 = time.monotonic()

        bus.emit(
            EventTopics.VOICE_LISTENING_STARTED.value,
            {"conversation_id": turn_id, "timestamp": time.time()},
        )
        await asyncio.sleep(0.05)
        bus.emit(
            EventTopics.VOICE_LISTENING_STOPPED.value,
            {"transcript": transcript, "conversation_id": turn_id},
        )

        # Wait for a complete LLM_RESPONSE (is_complete=True), then settle so any
        # tool call Claude made has time to reach the music controller.
        deadline = t0 + args.turn_timeout
        while time.monotonic() < deadline:
            if any(
                e["topic"] == "LLM_RESPONSE" and _payload_get(e["payload"], "is_complete")
                for e in rec.since(t0)
            ):
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(settle)

        evs = rec.since(t0)
        for e in evs:
            ms = (e["t"] - t0) * 1000
            extra = ""
            if e["topic"] == "LLM_RESPONSE":
                complete = _payload_get(e["payload"], "is_complete")
                tc = _payload_get(e["payload"], "tool_calls")
                extra = f"  (is_complete={complete}, tool_calls={len(tc) if tc else 0})"
            elif e["topic"] in ("CLI_COMMAND", "MUSIC_COMMAND", "DJ_COMMAND", "INTENT_DETECTED"):
                for field in ("command", "action", "raw_input", "intent_name", "subcommand", "args"):
                    v = _payload_get(e["payload"], field)
                    if v not in (None, "", []):
                        extra += f" {field}={v!r}"
            print(f"    {ms:8.1f} ms  {e['topic']}{extra}")

        def first(*topics: str) -> Optional[float]:
            return next(
                ((e["t"] - t0) * 1000 for e in evs if e["topic"] in topics), None
            )

        action_ms = first("MUSIC_COMMAND", "DJ_COMMAND", "EYE_COMMAND")
        chunks = [
            e
            for e in evs
            if e["topic"] == "LLM_RESPONSE" and not _payload_get(e["payload"], "is_complete")
        ]
        finals = [
            e
            for e in evs
            if e["topic"] == "LLM_RESPONSE" and _payload_get(e["payload"], "is_complete")
        ]
        first_token_ms = (chunks[0]["t"] - t0) * 1000 if chunks else None
        full_reply_ms = (finals[-1]["t"] - t0) * 1000 if finals else None
        reply_text = "".join(
            str(_payload_get(e["payload"], "text") or "") for e in chunks
        ) or (str(_payload_get(finals[-1]["payload"], "text") or "") if finals else "")
        tool_calls = [
            tc
            for e in evs
            if e["topic"] == "LLM_RESPONSE"
            for tc in (_payload_get(e["payload"], "tool_calls") or [])
        ]
        fast_routed = any(e["topic"] == "INTENT_CONSUMED" for e in evs)
        # "Did Claude re-dispatch the music tool?" is a question about what happened *after*
        # Claude started answering. The fast router's single play_music dispatch legitimately
        # produces two MUSIC_COMMANDs ("list music" then "play music <track>"), so counting
        # them all would fail a turn that behaved perfectly.
        music_cmds_after_claude = [
            e
            for e in evs
            if e["topic"] == "MUSIC_COMMAND"
            and first_token_ms is not None
            and (e["t"] - t0) * 1000 >= first_token_ms
        ]

        r = {
            "key": key,
            "label": label,
            "transcript": transcript,
            "action_ms": action_ms,
            "first_token_ms": first_token_ms,
            "full_reply_ms": full_reply_ms,
            "reply": reply_text,
            "reply_chars": len(reply_text),
            "chunk_count": len(chunks),
            "tool_call_names": [
                (tc.get("function", {}) or {}).get("name") if isinstance(tc, dict) else None
                for tc in tool_calls
            ],
            "fast_routed": fast_routed,
            "music_commands_after_claude_started": len(music_cmds_after_claude),
            "playback_started": any(e["topic"] == "MUSIC_PLAYBACK_STARTED" for e in evs),
            "playback_stopped": any(e["topic"] == "MUSIC_PLAYBACK_STOPPED" for e in evs),
        }

        # ---- assertions, per turn shape ----
        checks: List[tuple] = []
        if key == "fast_action":
            checks = [
                ("Jev dispatched the action before Claude", r["fast_routed"]),
                ("action reached MUSIC_COMMAND", action_ms is not None),
                ("music actually played", r["playback_started"]),
                ("NO tool_use block came back", len(tool_calls) == 0),
                ("a spoken reply was produced", r["reply_chars"] > 0),
                (
                    "music tool NOT re-dispatched once Claude was answering",
                    r["music_commands_after_claude_started"] == 0,
                ),
            ]
        elif key == "pure_chat":
            checks = [
                ("no action dispatched", action_ms is None),
                ("no tool_use block", len(tool_calls) == 0),
                ("a spoken reply was produced", r["reply_chars"] > 0),
            ]
        else:
            checks = [
                ("Jev did NOT fast-route this turn", not r["fast_routed"]),
                ("Claude called a tool itself", len(tool_calls) > 0),
                ("the tool call reached MUSIC_COMMAND", action_ms is not None),
                ("music stopped", r["playback_stopped"]),
            ]
        r["checks"] = checks
        r["passed"] = all(ok for _, ok in checks)
        results.append(r)

        print(f'  reply ({r["reply_chars"]} chars, {r["chunk_count"]} chunks): {reply_text[:160]}')
        print(f"  tool calls from Claude: {r['tool_call_names'] or 'none'}")
        print("  timings:")
        for name, v in (
            ("transcript -> action", action_ms),
            ("transcript -> first Claude token", first_token_ms),
            ("transcript -> full reply", full_reply_ms),
        ):
            print(f"    {name:<34} {'-' if v is None else f'{v:8.0f} ms'}")
        for desc, ok in checks:
            print(f"    [{'PASS' if ok else 'FAIL'}] {desc}")
        music = os_._services.get("music_controller")
        if music is not None:
            # The VLC player lives on the active *backend*, not on the service - the service's
            # own self.player is only used by the crossfade path.
            track = getattr(music, "current_track", None)
            backend = getattr(music, "backends", {}).get(
                getattr(music, "active_source", None)
            )
            player = getattr(backend, "player", None)
            print(
                f"  music controller: track={getattr(track, 'title', None)!r} "
                f"backend={getattr(music, 'active_source', None)!r} "
                f"vlc_is_playing={bool(player.is_playing()) if player else None}"
            )

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    hdr = f"  {'turn':<12} {'action':>9} {'1st tok':>9} {'full':>9}  {'tools':<18} result"
    print(hdr)
    for r in results:
        f = lambda v: "-" if v is None else f"{v:.0f} ms"  # noqa: E731
        print(
            f"  {r['key']:<12} {f(r['action_ms']):>9} {f(r['first_token_ms']):>9} "
            f"{f(r['full_reply_ms']):>9}  {str(r['tool_call_names'] or 'none'):<18} "
            f"{'PASS' if r['passed'] else 'FAIL'}"
        )

    print()
    print("Shutting down...")
    with contextlib.suppress(Exception):
        await os_._cleanup_services()
    return 0 if all(r["passed"] for r in results) else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-tts", action="store_true", help="also start ElevenLabs (paid, audible)")
    ap.add_argument("--turn-timeout", type=float, default=25.0)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
