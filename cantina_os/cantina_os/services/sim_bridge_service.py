"""
SimBridgeService - the websocket between CantinaOS and the R3X control panel (``sim/web``).

Out (bus -> panel): the events that drive R3X's face and body, the conversation, music,
DJ mode, command replies and service status, plus the process's log records. The panel
replays the face/body events through its own port of the eye-controller host logic and the
rex_face_v3_clean firmware, so the virtual droid shows what the real LEDs would - including
when no Arduino is plugged in.

In (panel -> bus): push-to-talk, typed utterances, and CLI command lines. Every one of
these is turned into the *same* event the existing input path emits - MIC_RECORDING_START/
STOP as the mouse does, VOICE_LISTENING_STARTED/STOPPED as the capture services do,
CLI_COMMAND as CLIService does - so the panel adds no new behaviour downstream.

While at least one panel is connected the bridge reports ``control_panel`` RUNNING on
SERVICE_STATUS_UPDATE, and MouseInputService stops treating global clicks as
push-to-talk; clicking the panel's own buttons would otherwise toggle the mic too.

Fail-open: if the port is taken or websockets will not import, it logs a warning and
CantinaOS carries on unchanged.

Wire format, one JSON object per message.
  out  {"type": "hello", "mode": ..., "services": {...}, "music": {...}, "dj_active": bool,
        "logs": [...], "events": [...]}                                   on connect
       {"type": "event", "topic": "voice.listening.started", "data": {...}, "t": 1727.4}
       {"type": "log", "t": 1727.4, "level": "INFO", "name": "...", "msg": "..."}
       {"type": "ack", "id": "...", "ok": true, "message": "..."}         reply to a cmd
  in   {"type": "hello", "role": "panel"}
       {"type": "cmd", "id": "...", "action": "ptt", "state": "start" | "stop"}
       {"type": "cmd", "id": "...", "action": "say", "text": "what's playing?"}
       {"type": "cmd", "id": "...", "action": "cli", "text": "play music cantina"}
       {"type": "cmd", "id": "...", "action": "log_level", "level": "DEBUG"}

Config (env): SIM_BRIDGE_ENABLED (default true), SIM_BRIDGE_HOST, SIM_BRIDGE_PORT,
SIM_BRIDGE_LOG_LEVEL (default INFO - the lowest level streamed to the panel).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import CliCommandPayload, ServiceStatus, ServiceStatusPayload

# What the 3D droid needs: interaction state for the eyes/mouth and motion, music for dancing.
FORWARDED_TOPICS = [
    EventTopics.SYSTEM_MODE_CHANGE,
    EventTopics.VOICE_LISTENING_STARTED,
    EventTopics.VOICE_LISTENING_STOPPED,
    EventTopics.VOICE_PROCESSING_STARTED,
    EventTopics.MOUSE_RECORDING_STOPPED,
    EventTopics.LLM_RESPONSE_CHUNK,
    EventTopics.LLM_RESPONSE,
    EventTopics.SPEECH_SYNTHESIS_STARTED,
    EventTopics.SPEECH_GENERATION_STARTED,
    EventTopics.SPEECH_SYNTHESIS_AMPLITUDE,
    EventTopics.SPEECH_SYNTHESIS_ENDED,
    EventTopics.SPEECH_GENERATION_COMPLETE,
    EventTopics.TRANSCRIPTION_INTERIM,
    EventTopics.MUSIC_PLAYBACK_STARTED,
    EventTopics.MUSIC_PLAYBACK_STOPPED,
    EventTopics.DJ_MODE_CHANGED,
    EventTopics.INTENT_DETECTED,
    EventTopics.CHEST_COMMAND,  # the exact bytes sent to the chest board
    # Show system (show/SPEC.md "Live bus contract"): SimBridge forwards all of them.
    EventTopics.SHOW_PERFORM,
    EventTopics.SHOW_STOP,
    EventTopics.SHOW_STARTED,
    EventTopics.SHOW_ENDED,
    EventTopics.SHOW_MOTION,
    EventTopics.SHOW_SFX,
    EventTopics.STAGE_LIGHTS,
    EventTopics.CHEST_OVERRIDE,
    EventTopics.MOTION_FREEZE,
    EventTopics.EYE_COMMAND,  # a cue's `eyes` action (EyeCommandPayload)
]

# What the control panel adds on top: the conversation, command traffic and health.
PANEL_TOPICS = [
    EventTopics.TRANSCRIPTION_FINAL,
    EventTopics.SPEECH_ALIGNMENT,
    EventTopics.INTENT_CONSUMED,
    EventTopics.CLI_COMMAND,
    EventTopics.CLI_RESPONSE,
    EventTopics.MODE_COMMAND,
    EventTopics.MUSIC_COMMAND,
    EventTopics.DJ_COMMAND,
    EventTopics.MIC_RECORDING_START,
    EventTopics.MIC_RECORDING_STOP,
    EventTopics.MUSIC_LIBRARY_UPDATED,
    EventTopics.DJ_NEXT_TRACK_SELECTED,
    EventTopics.DEBUG_PERFORMANCE,
    EventTopics.SERVICE_STATUS_UPDATE,
    EventTopics.SYSTEM_ERROR,
]

# BaseService._emit_status emits this literal, not SERVICE_STATUS_UPDATE (CLAUDE.md s10).
LITERAL_STATUS_TOPIC = "service_status"

# Not kept in the replay buffer: ~100 per spoken reply, and the panel only wants them live.
UNBUFFERED_TOPICS = {EventTopics.SPEECH_SYNTHESIS_AMPLITUDE.value}

MAX_STRING = 4000  # long enough for a full reply or `list music`; bounds anything pathological
LOG_BUFFER = 1000
EVENT_BUFFER = 300
PTT_START_TIMEOUT_S = 5.0  # how long a held button keeps retrying MIC_RECORDING_START

# CLIService.SHORTCUTS, so a line typed in the panel means what it means in the terminal.
CLI_SHORTCUTS = {
    "e": "engage", "a": "ambient", "d": "disengage", "h": "help", "st": "status",
    "r": "reset", "l": "list music", "p": "play music", "s": "stop music",
}


def _jsonable(payload: Any) -> Any:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return {k: _jsonable(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_jsonable(v) for v in payload[:50]]
    if isinstance(payload, str):
        return payload[:MAX_STRING]
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    return str(payload)[:MAX_STRING]


class _PanelLogHandler(logging.Handler):
    """Root-logger handler that hands records to the bridge on the event loop.

    Records arrive on whatever thread logged them (audio callbacks, VLC, the logging
    QueueListener), so the handler does nothing but format and hop threads.
    """

    # The bridge's own transport; forwarding these would feed back into itself.
    _IGNORED = ("websockets", "asyncio")

    def __init__(self, bridge: "SimBridgeService", loop: asyncio.AbstractEventLoop, level: int):
        super().__init__(level)
        self._bridge = bridge
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(self._IGNORED):
            return
        try:
            msg = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                msg = f"{msg} ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            entry = {
                "t": round(record.created, 3),
                "level": record.levelname,
                "name": record.name,
                "msg": msg[:4000],
            }
            self._loop.call_soon_threadsafe(self._bridge._on_log, entry)
        except RuntimeError:
            pass  # loop already closed during shutdown
        except Exception:
            self.handleError(record)


class SimBridgeService(BaseService):
    """Two-way websocket between the bus and the R3X control panel / 3D sim."""

    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        mode_manager=None,
        music_controller=None,
        brain_service=None,
    ):
        super().__init__(service_name="sim_bridge", event_bus=event_bus)
        config = config or {}
        self._enabled = str(config.get("SIM_BRIDGE_ENABLED", os.getenv("SIM_BRIDGE_ENABLED", "true"))).lower() in (
            "1", "true", "yes", "on")
        self._host = config.get("SIM_BRIDGE_HOST", os.getenv("SIM_BRIDGE_HOST", "127.0.0.1"))
        self._port = int(config.get("SIM_BRIDGE_PORT", os.getenv("SIM_BRIDGE_PORT", "8765")))
        self._log_level = str(config.get("SIM_BRIDGE_LOG_LEVEL", os.getenv("SIM_BRIDGE_LOG_LEVEL", "INFO"))).upper()
        # Read-only state queries (CLAUDE.md Pattern 2), so a panel that connects late still
        # learns the mode, the music library and what is playing. Any of them may be None.
        self._mode_manager = mode_manager
        self._music = music_controller
        self._brain = brain_service
        self._last_mode: Optional[str] = None
        self._clients: Set[Any] = set()
        self._panels: Set[Any] = set()
        self._server = None
        self._t0 = time.monotonic()

        self._logs: Deque[Dict[str, Any]] = deque(maxlen=LOG_BUFFER)
        self._events: Deque[Dict[str, Any]] = deque(maxlen=EVENT_BUFFER)
        self._services: Dict[str, Dict[str, Any]] = {}
        self._log_handler: Optional[_PanelLogHandler] = None

        self._music_playing = False
        self._current_track: Optional[str] = None
        self._dj_active = False
        self._listening = False
        self._listening_started = asyncio.Event()
        self._ptt_held = False
        # A release that lands while a start is still in flight: if the mic comes up before
        # this monotonic deadline, it is stopped again at once.
        self._ptt_cancel_until = 0.0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------------ lifecycle

    async def _start(self) -> None:
        if not self._enabled:
            self.logger.info("Sim bridge disabled (SIM_BRIDGE_ENABLED=false)")
            return
        try:
            from websockets.asyncio.server import serve
        except ImportError as e:  # pragma: no cover - dependency is in requirements
            self.logger.warning(f"Sim bridge unavailable, websockets not importable: {e}")
            return
        try:
            self._server = await serve(self._handle_client, self._host, self._port)
        except OSError as e:
            self.logger.warning(f"Sim bridge could not listen on {self._host}:{self._port} ({e}); continuing without it")
            return
        for topic in dict.fromkeys(FORWARDED_TOPICS + PANEL_TOPICS):  # de-duplicated, ordered
            await self.subscribe(topic, self._make_forwarder(topic.value))
        await self.subscribe(LITERAL_STATUS_TOPIC, self._make_forwarder(LITERAL_STATUS_TOPIC))

        level = logging.getLevelNamesMapping().get(self._log_level, logging.INFO)
        self._log_handler = _PanelLogHandler(self, asyncio.get_running_loop(), level)
        logging.getLogger().addHandler(self._log_handler)
        self.logger.info(f"Sim bridge listening on ws://{self._host}:{self._port}")

    async def _stop(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()
        self._panels.clear()

    # ------------------------------------------------------------------ state

    def _current_mode(self) -> Optional[str]:
        if self._mode_manager is not None:
            try:
                mode = self._mode_manager.current_mode
                return getattr(mode, "name", None) or str(mode)
            except Exception:  # read-only query must never break the bridge
                pass
        return self._last_mode

    def _music_state(self) -> Dict[str, Any]:
        tracks: List[str] = []
        current = self._current_track
        if self._music is not None:
            try:
                tracks = sorted(getattr(self._music, "tracks", {}) or {})
                track = getattr(self._music, "current_track", None)
                current = getattr(track, "name", None) or current
            except Exception:
                pass
        return {"tracks": tracks, "current": current, "playing": self._music_playing}

    def _dj_state(self) -> bool:
        if self._brain is not None:
            try:
                return bool(getattr(self._brain, "_dj_mode_active", self._dj_active))
            except Exception:
                pass
        return self._dj_active

    def _hello(self) -> Dict[str, Any]:
        return {
            "type": "hello",
            "mode": self._current_mode(),
            "services": self._services,
            "music": self._music_state(),
            "dj_active": self._dj_state(),
            "listening": self._listening,
            "log_level": self._log_level,
            "logs": list(self._logs)[-300:],
            "events": list(self._events),
        }

    def _track_state(self, topic: str, payload: Any) -> None:
        d = payload if isinstance(payload, dict) else {}
        if topic == EventTopics.SYSTEM_MODE_CHANGE.value:
            self._last_mode = d.get("new_mode") or self._last_mode
        elif topic == EventTopics.VOICE_LISTENING_STARTED.value:
            self._listening = True
            self._listening_started.set()
            if not self._ptt_held and time.monotonic() < self._ptt_cancel_until:
                self._ptt_cancel_until = 0.0
                asyncio.get_running_loop().create_task(self._stop_listening())
        elif topic == EventTopics.VOICE_LISTENING_STOPPED.value:
            self._listening = False
        elif topic == EventTopics.MUSIC_PLAYBACK_STARTED.value:
            self._music_playing = True
            track = d.get("track")
            self._current_track = (track.get("name") if isinstance(track, dict) else track) or self._current_track
        elif topic == EventTopics.MUSIC_PLAYBACK_STOPPED.value:
            self._music_playing = False
            self._current_track = None
        elif topic == EventTopics.DJ_MODE_CHANGED.value:
            self._dj_active = bool(d.get("is_active"))
        elif topic in (LITERAL_STATUS_TOPIC, EventTopics.SERVICE_STATUS_UPDATE.value):
            name = d.get("service_name") or d.get("service")
            if name:
                self._services[str(name)] = {
                    "status": str(d.get("status", "")).split(".")[-1],
                    "message": str(d.get("message") or "")[:MAX_STRING],
                    "t": round(time.time(), 3),
                }

    # ------------------------------------------------------------------ outbound

    def _make_forwarder(self, topic: str):
        async def forward(payload: Any = None) -> None:
            self._track_state(topic, payload)
            if topic in UNBUFFERED_TOPICS and not self._clients:
                return
            msg = {
                "type": "event",
                "topic": topic,
                "data": _jsonable(payload),
                "t": round(time.monotonic() - self._t0, 3),
                "wall": round(time.time(), 3),
            }
            if topic not in UNBUFFERED_TOPICS:
                self._events.append(msg)
            self._send_all(msg)

        return forward

    def _on_log(self, entry: Dict[str, Any]) -> None:
        entry = {"type": "log", **entry}
        self._logs.append(entry)
        self._send_all(entry)

    def _send_all(self, msg: Dict[str, Any]) -> None:
        if not self._clients:
            return
        try:
            from websockets.asyncio.server import broadcast
        except ImportError:  # pragma: no cover
            return
        # broadcast() never blocks: slow clients are skipped rather than stalling the bus.
        broadcast(self._clients, json.dumps(msg))

    def broadcast(self, topic: str, payload: Any) -> None:
        """Send one event to every client (kept for callers outside the bus)."""
        self._send_all({
            "type": "event",
            "topic": topic,
            "data": _jsonable(payload),
            "t": round(time.monotonic() - self._t0, 3),
        })

    # ------------------------------------------------------------------ clients

    async def _handle_client(self, ws) -> None:
        self._clients.add(ws)
        self.logger.info(f"Sim connected ({len(self._clients)} client(s))")
        try:
            await ws.send(json.dumps(self._hello()))
            async for raw in ws:
                await self._handle_message(ws, raw)
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            if ws in self._panels:
                self._panels.discard(ws)
                if not self._panels:
                    await self._report_panel(False)
            self.logger.info(f"Sim disconnected ({len(self._clients)} client(s))")

    async def _report_panel(self, connected: bool) -> None:
        """Tell MouseInputService whether a panel owns push-to-talk right now."""
        await self.emit(
            EventTopics.SERVICE_STATUS_UPDATE,
            ServiceStatusPayload(
                service_name="control_panel",
                status=ServiceStatus.RUNNING if connected else ServiceStatus.STOPPED,
                message="control panel connected" if connected else "control panel disconnected",
            ),
        )

    async def _handle_message(self, ws, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "hello" and msg.get("role") == "panel":
            first = not self._panels
            self._panels.add(ws)
            if first:
                await self._report_panel(True)
            return
        if kind != "cmd":
            return
        try:
            ok, message = await self._run_command(msg)
        except Exception as e:  # a bad command must never take the bridge down
            self.logger.error(f"Panel command failed: {msg.get('action')}: {e}")
            ok, message = False, str(e)
        try:
            await ws.send(json.dumps({"type": "ack", "id": msg.get("id"), "ok": ok, "message": message}))
        except Exception:
            pass

    async def _run_command(self, msg: Dict[str, Any]) -> Tuple[bool, str]:
        action = msg.get("action")
        text = str(msg.get("text") or "").strip()
        if action == "ptt":
            if msg.get("state") == "start":
                return await self._ptt_start()
            return await self._ptt_stop()
        if action == "say":
            if not text:
                return False, "nothing to say"
            return await self._say(text)
        if action == "cli":
            if not text:
                return False, "empty command"
            return await self._cli(text)
        if action == "log_level":
            level = str(msg.get("level") or "").upper()
            if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
                return False, f"unknown level {level!r}"
            self._log_level = level
            if self._log_handler is not None:
                self._log_handler.setLevel(level)
            return True, f"streaming {level} and above"
        return False, f"unknown action {action!r}"

    # ------------------------------------------------------------------ commands

    async def _ensure_interactive(self, timeout: float = 3.0) -> bool:
        if self._current_mode() == "INTERACTIVE":
            return True
        await self.emit(EventTopics.SYSTEM_SET_MODE_REQUEST, {"mode": "INTERACTIVE"})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._current_mode() == "INTERACTIVE":
                return True
            await asyncio.sleep(0.05)
        return False

    async def _ptt_start(self) -> Tuple[bool, str]:
        self._ptt_held = True
        if self._listening:
            return True, "already listening"
        if not await self._ensure_interactive():
            return False, "could not switch to INTERACTIVE mode"
        # DeepgramDirectMicService refuses a start while R3X is speaking or before its
        # socket (opened on entering INTERACTIVE) is up, and says nothing back. So retry
        # while the button is held until VOICE_LISTENING_STARTED confirms the mic is live.
        deadline = time.monotonic() + PTT_START_TIMEOUT_S
        while self._ptt_held and time.monotonic() < deadline:
            self._listening_started.clear()
            await self.emit(EventTopics.MIC_RECORDING_START, {"source": "panel"})
            try:
                await asyncio.wait_for(self._listening_started.wait(), 0.8)
                return True, "listening"
            except asyncio.TimeoutError:
                continue
        if not self._ptt_held:
            return False, "released before the mic started"
        return False, "mic did not start - R3X may still be speaking, or Deepgram is not connected"

    async def _ptt_stop(self) -> Tuple[bool, str]:
        self._ptt_held = False
        if not self._listening:
            self._ptt_cancel_until = time.monotonic() + 2.0
            return True, "not listening"
        await self._stop_listening()
        return True, "stopped"

    async def _stop_listening(self) -> None:
        # Same pair MouseInputService emits: the first flips the eyes to thinking at once.
        await self.emit(EventTopics.MOUSE_RECORDING_STOPPED, {})
        await self.emit(EventTopics.MIC_RECORDING_STOP, {})

    async def _say(self, text: str) -> Tuple[bool, str]:
        """Run a typed utterance through the voice loop exactly as a spoken one arrives."""
        if self._listening:
            return False, "the mic is live - release push-to-talk first"
        await self._ensure_interactive()
        self._ptt_cancel_until = 0.0
        conversation_id = str(uuid.uuid4())
        await self.emit(EventTopics.VOICE_LISTENING_STARTED, {
            "conversation_id": conversation_id, "timestamp": time.time(), "source": "panel"})
        await asyncio.sleep(0.05)
        await self.emit(EventTopics.VOICE_LISTENING_STOPPED, {
            "transcript": text, "conversation_id": conversation_id,
            "has_transcript": True, "source": "panel"})
        return True, conversation_id

    async def _cli(self, line: str) -> Tuple[bool, str]:
        """Emit CLI_COMMAND the way CLIService does for the same typed line."""
        parts = line.split()
        command, args = parts[0].lower(), parts[1:]
        if command in CLI_SHORTCUTS:
            expanded = CLI_SHORTCUTS[command].split()
            command, args = expanded[0], expanded[1:] + args
            line = " ".join([command, *args])
        if command in ("quit", "exit", "q"):
            return False, "stop CantinaOS from the terminal (Ctrl-C)"
        payload = CliCommandPayload(command=command, args=args, raw_input=line)
        await self.emit(EventTopics.CLI_COMMAND, payload)
        return True, line
