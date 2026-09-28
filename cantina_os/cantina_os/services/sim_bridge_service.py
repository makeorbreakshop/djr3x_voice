"""
SimBridgeService - streams the events that drive R3X's face and body to the 3D sim.

The sim (``sim/web``, a Three.js page) listens on ``ws://127.0.0.1:8765`` and replays
these events through its own port of the eye-controller host logic and the
rex_face_v3_clean firmware, so the virtual droid shows what the real LEDs would -
including when no Arduino is plugged in.

Read-only and fail-open: it only subscribes and forwards. If the port is taken or the
websockets import fails, it logs a warning and CantinaOS carries on unchanged.

Wire format, one JSON object per message:
    {"type": "hello", "mode": "INTERACTIVE"}                 on connect
    {"type": "event", "topic": "voice.listening.started", "data": {...}, "t": 1727.4}

Config (env): SIM_BRIDGE_ENABLED (default true), SIM_BRIDGE_HOST, SIM_BRIDGE_PORT.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Set

from ..base_service import BaseService
from ..core.event_topics import EventTopics

# What the sim needs: interaction state for the eyes/mouth and motion, music for dancing.
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
]

MAX_STRING = 400  # keep chatty payloads (LLM text) small on the wire


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


class SimBridgeService(BaseService):
    """Forward face/body-relevant bus events to the 3D sim over a local websocket."""

    def __init__(self, event_bus, config: Optional[Dict[str, Any]] = None, mode_manager=None):
        super().__init__(service_name="sim_bridge", event_bus=event_bus)
        config = config or {}
        self._enabled = str(config.get("SIM_BRIDGE_ENABLED", os.getenv("SIM_BRIDGE_ENABLED", "true"))).lower() in (
            "1", "true", "yes", "on")
        self._host = config.get("SIM_BRIDGE_HOST", os.getenv("SIM_BRIDGE_HOST", "127.0.0.1"))
        self._port = int(config.get("SIM_BRIDGE_PORT", os.getenv("SIM_BRIDGE_PORT", "8765")))
        # Read-only state query (CLAUDE.md Pattern 2) so a sim that connects late still
        # learns the current mode.
        self._mode_manager = mode_manager
        self._last_mode: Optional[str] = None
        self._clients: Set[Any] = set()
        self._server = None
        self._t0 = time.monotonic()

    @property
    def client_count(self) -> int:
        return len(self._clients)

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
        for topic in FORWARDED_TOPICS:
            await self.subscribe(topic, self._make_forwarder(topic.value))
        self.logger.info(f"Sim bridge listening on ws://{self._host}:{self._port}")

    async def _stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()

    def _current_mode(self) -> Optional[str]:
        if self._mode_manager is not None:
            try:
                mode = self._mode_manager.current_mode
                return getattr(mode, "name", None) or str(mode)
            except Exception:  # read-only query must never break the bridge
                pass
        return self._last_mode

    async def _handle_client(self, ws) -> None:
        self._clients.add(ws)
        self.logger.info(f"Sim connected ({len(self._clients)} client(s))")
        try:
            await ws.send(json.dumps({"type": "hello", "mode": self._current_mode()}))
            async for _ in ws:  # the sim does not send anything yet; just hold the socket
                pass
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            self.logger.info(f"Sim disconnected ({len(self._clients)} client(s))")

    def _make_forwarder(self, topic: str):
        async def forward(payload: Any = None) -> None:
            if topic == EventTopics.SYSTEM_MODE_CHANGE.value and isinstance(payload, dict):
                self._last_mode = payload.get("new_mode") or self._last_mode
            if not self._clients:
                return
            self.broadcast(topic, payload)

        return forward

    def broadcast(self, topic: str, payload: Any) -> None:
        try:
            from websockets.asyncio.server import broadcast
        except ImportError:  # pragma: no cover
            return
        msg = json.dumps({
            "type": "event",
            "topic": topic,
            "data": _jsonable(payload),
            "t": round(time.monotonic() - self._t0, 3),
        })
        # broadcast() never blocks: slow clients are skipped rather than stalling the bus.
        broadcast(self._clients, msg)
