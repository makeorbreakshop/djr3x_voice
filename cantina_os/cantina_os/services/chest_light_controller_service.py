"""
ChestLightControllerService - drives the middle-ring logic panel lights (second Arduino).

The chest board (cantina_os/arduino/rex_chest_v1) has 3 panels x (8 LED dots + 3 windows).
This service keeps it in lockstep with the eyes and shows the machine's overall status:

* Interaction: the same pattern mapping as EyeLightControllerService - IDLE -> SI,
  AMBIENT/INTERACTIVE -> SE, and in INTERACTIVE listening -> SL, thinking -> ST,
  speaking -> SS with speech amplitude (Mnnn), then a green "done" sparkle (SF) when
  speech ends, exactly when the eyes flash green.
* Music: Bnnn tempo while music plays (beat chase), B000 when it stops.
* Machine status:
  - X1 boot sweep from start until the system leaves STARTUP,
  - Hxxx health mask - each of the 9 windows stands for a subsystem and blinks red while
    that service reports error/degraded (WINDOW_SUBSYSTEMS below),
  - X3 fault alarm while a service R3X cannot converse without is in error.
  Services report ERROR per failed request and never report recovery, so a runtime error
  counts for CHEST_FAULT_HOLD_S (60 s) unless repeated; a failure to start or initialise
  stays until the service reports running again.

Show control (show/SPEC.md):
* ``chest.override {command, hold}`` - the timeline sends a chest word (``X2``, ``SF``,
  ``M200``...). It goes out at once; for ``hold`` seconds the status logic above is kept off
  that channel (keyed by the command's letter), then the current status is re-sent. A hold
  of 0 keeps the override until the status on that channel next changes.
* ``motion.freeze {on}`` - while frozen the board holds whatever it shows (nothing is sent);
  on release every channel is re-asserted.

Every command is also emitted on CHEST_COMMAND, so the 3D sim mirrors the real board's
input byte for byte. Fail-open: with no board (or CHEST_ENABLED=false) it runs in mock
mode and still emits CHEST_COMMAND.

Port selection (two Arduinos on one Mac): set CHEST_SERIAL_PORT explicitly, ideally with
ARDUINO_SERIAL_PORT for the face. Otherwise it probes Arduino-like ports, skipping the
face board's port, for a board that identifies as the chest ("CHEST READY" at boot, or a
"Chest:" reply to "?").
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from ..base_service import BaseService
from ..core.event_payloads import ChestCommandPayload, ChestOverridePayload, MotionFreezePayload
from ..core.event_topics import EventTopics

# Panel-major window order, matching the sketch's WINDOW_PIXEL order (and the sim).
WINDOW_SUBSYSTEMS: List[Tuple[str, Tuple[str, ...]]] = [
    ("mic / speech-to-text", ("DeepgramDirectMicService",)),
    ("LLM", ("ClaudeService",)),
    ("text-to-speech", ("ElevenLabsService",)),
    ("intent routing", ("IntentRouterService", "JevIntentService")),
    ("music", ("MusicControllerService",)),
    ("memory", ("MemoryService",)),
    ("vision", ("VisionService",)),
    ("face LEDs", ("EyeLightControllerService",)),
    ("show control", ("BrainService", "TimelineExecutorService")),
]
# In error, R3X cannot hold a conversation -> fault alarm.
CRITICAL_SERVICES = {"DeepgramDirectMicService", "ClaudeService"}
UNHEALTHY = {"error", "degraded"}
#: Channels the status logic drives, keyed by the command letter (see tick()).
STATUS_CHANNELS = ("X", "H", "S", "B", "M")

ARDUINO_NAME_HINTS = ("usbmodem", "usbserial", "ttyacm", "ttyusb", "wchusbserial")


def health_mask(statuses: Dict[str, str]) -> int:
    """9-bit mask, bit i set while window i's subsystem is healthy (or not reporting)."""
    mask = 0
    for i, (_label, services) in enumerate(WINDOW_SUBSYSTEMS):
        if not any(statuses.get(s) in UNHEALTHY for s in services):
            mask |= 1 << i
    return mask


class ChestSerialLink:
    """Minimal pyserial link: fire-and-forget writes, identity-checked connect."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.ser = None

    def open(self, timeout_s: float = 3.0) -> bool:
        import serial  # pyserial; imported lazily so tests without hardware stay light

        self.ser = serial.Serial(self.port, self.baud, timeout=0.1, write_timeout=0.05)
        # Opening resets a Nano; wait for its boot line, then ask it who it is.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="ignore").strip()
            if line.startswith("CHEST READY") or line.startswith("Chest:"):
                return True
            if line == "READY":  # that is the face board - not ours
                break
            if not line:
                self.ser.write(b"?\n")
        self.close()
        return False

    def write(self, cmd: str) -> bool:
        if not self.ser:
            return False
        try:
            self.ser.write((cmd + "\n").encode())
            if self.ser.in_waiting:
                self.ser.reset_input_buffer()  # acks are not needed
            return True
        except Exception:
            return False

    def close(self):
        try:
            if self.ser:
                self.ser.close()
        finally:
            self.ser = None


class ChestLightControllerService(BaseService):
    LOOP_HZ = 20
    AMP_ALPHA = 0.3

    def __init__(self, event_bus, config: Optional[Dict[str, Any]] = None, eye_service=None):
        super().__init__(service_name="chest_light_controller", event_bus=event_bus)
        cfg = config or {}
        env = os.environ.get
        self._enabled = str(cfg.get("CHEST_ENABLED", env("CHEST_ENABLED", "true"))).lower() in ("1", "true", "yes", "on")
        self._port = cfg.get("CHEST_SERIAL_PORT", env("CHEST_SERIAL_PORT"))
        self._force_mock = str(cfg.get("FORCE_MOCK_CHEST", env("FORCE_MOCK_CHEST", "false"))).lower() in ("1", "true", "yes")
        self._default_bpm = int(cfg.get("CHEST_DEFAULT_BPM", env("CHEST_DEFAULT_BPM", "120")))
        self._fault_hold = float(cfg.get("CHEST_FAULT_HOLD_S", env("CHEST_FAULT_HOLD_S", "60")))
        # Read-only state query (CLAUDE.md Pattern 2): which port the face board holds.
        self._eye_service = eye_service
        self._link: Optional[ChestSerialLink] = None
        self._task: Optional[asyncio.Task] = None

        # desired state
        self.system_mode = "STARTUP"
        self.target_pattern = "SI"
        self.amplitude = 0.0
        self.bpm = 0
        # service -> (status, monotonic time reported, latched)
        self._svc_status: Dict[str, Tuple[str, float, bool]] = {}
        self.booting = True
        self._boot_started = time.monotonic()
        self._sparkle = False
        # last sent
        self._sent: Dict[str, str] = {}
        self._last_amp_level = -1
        # show control: channel letter -> monotonic time the override expires
        self._overrides: Dict[str, float] = {}
        self._reassert_all_at: Optional[float] = None
        self.frozen = False

    # ------------------------------------------------------------------ lifecycle

    @property
    def connected(self) -> bool:
        return self._link is not None

    async def _start(self) -> None:
        if not self._enabled:
            self.logger.info("Chest lights disabled (CHEST_ENABLED=false)")
            return
        if not self._force_mock:
            try:
                self._link = await asyncio.to_thread(self._connect)
            except Exception as e:  # never let the chest stop CantinaOS
                self.logger.warning(f"Chest board connect failed ({e}); mock mode")
                self._link = None
        self.logger.info(f"Chest lights: {'connected on ' + self._link.port if self._link else 'mock mode (no board)'}")

        subs = [
            (EventTopics.SYSTEM_MODE_CHANGE, self._on_mode),
            (EventTopics.VOICE_LISTENING_STARTED, self._on_listening),
            (EventTopics.VOICE_LISTENING_STOPPED, self._on_thinking),
            (EventTopics.VOICE_PROCESSING_STARTED, self._on_thinking),
            (EventTopics.MOUSE_RECORDING_STOPPED, self._on_thinking),
            (EventTopics.LLM_RESPONSE_CHUNK, self._on_llm_chunk),
            (EventTopics.SPEECH_GENERATION_STARTED, self._on_speech_started),
            (EventTopics.SPEECH_SYNTHESIS_STARTED, self._on_speech_started),
            (EventTopics.SPEECH_SYNTHESIS_AMPLITUDE, self._on_amplitude),
            (EventTopics.SPEECH_GENERATION_COMPLETE, self._on_speech_ended),
            (EventTopics.SPEECH_SYNTHESIS_ENDED, self._on_speech_ended),
            (EventTopics.MUSIC_PLAYBACK_STARTED, self._on_music_started),
            (EventTopics.MUSIC_PLAYBACK_STOPPED, self._on_music_stopped),
            (EventTopics.DJ_MODE_CHANGED, self._on_dj_mode),
            ("service_status", self._on_service_status),  # BaseService emits this literal
            (EventTopics.CHEST_OVERRIDE, self._on_override),
            (EventTopics.MOTION_FREEZE, self._on_freeze),
        ]
        for topic, handler in subs:
            await self.subscribe(topic, handler)
        self._send("R")
        self._send("X1")
        self._task = asyncio.create_task(self._loop())

    async def _stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._link:
            self._link.write("SI")
            self._link.write("X2")  # leave the chest asleep, not frozen mid-pattern
            self._link.close()
            self._link = None

    def _connect(self) -> Optional[ChestSerialLink]:
        if self._port:
            link = ChestSerialLink(self._port)
            if link.open():
                return link
            self.logger.warning(f"CHEST_SERIAL_PORT={self._port} did not identify as the chest board")
            return None
        from serial.tools import list_ports

        face_ports = {os.environ.get("ARDUINO_SERIAL_PORT")}
        eye_port = getattr(self._eye_service, "serial_port", None)
        face_ports.add(eye_port)
        for p in list_ports.comports():
            dev = p.device
            if dev in face_ports or not any(h in dev.lower() for h in ARDUINO_NAME_HINTS):
                continue
            if dev.startswith("/dev/tty.") and dev.replace("/dev/tty.", "/dev/cu.") in face_ports:
                continue
            link = ChestSerialLink(dev)
            try:
                if link.open(timeout_s=2.5):
                    return link
            except Exception:
                link.close()
        return None

    # ------------------------------------------------------------------ output

    def _send(self, cmd: str) -> None:
        ok = self._link.write(cmd) if self._link else False
        self._event_bus.emit(EventTopics.CHEST_COMMAND.value,
                             ChestCommandPayload(command=cmd, connected=ok).model_dump())

    def _send_if_changed(self, key: str, cmd: str) -> None:
        if key in self._overrides:  # a show owns this channel for now
            return
        if self._sent.get(key) != cmd:
            self._sent[key] = cmd
            self._send(cmd)

    @property
    def statuses(self) -> Dict[str, str]:
        """Current status per service, with expired runtime errors treated as recovered."""
        now = time.monotonic()
        out = {}
        for svc, (status, t, latched) in self._svc_status.items():
            expired = status in UNHEALTHY and not latched and now - t > self._fault_hold
            out[svc] = "running" if expired else status
        return out

    def system_state(self) -> str:
        if self.booting:
            return "X1"
        statuses = self.statuses
        if any(statuses.get(s) == "error" for s in CRITICAL_SERVICES):
            return "X3"
        return "X0"

    def _expire_overrides(self) -> None:
        now = time.monotonic()
        for key, until in list(self._overrides.items()):
            if now >= until:
                del self._overrides[key]
                self._sent.pop(key, None)  # force the current status back out
                if key == "S":
                    self._last_amp_level = -1
        if self._reassert_all_at is not None and now >= self._reassert_all_at:
            self._reassert_all_at = None
            self._sent.clear()
            self._last_amp_level = -1

    def tick(self) -> None:
        """One control-loop step (20 Hz): send whatever changed."""
        if self.frozen:
            return  # hold the board exactly as it is
        self._expire_overrides()
        if self.booting and time.monotonic() - self._boot_started > 20:
            self.booting = False  # never boot-sweep forever
        self._send_if_changed("X", self.system_state())
        self._send_if_changed("H", f"H{health_mask(self.statuses):03X}")
        if self._sparkle:
            self._sparkle = False
            if "S" not in self._overrides:
                self._send("SF")
        self._send_if_changed("S", self.target_pattern)
        self._send_if_changed("B", f"B{max(0, min(999, self.bpm)):03d}")
        if self.target_pattern == "SS" and "S" not in self._overrides and "M" not in self._overrides:
            level = int(max(0.0, min(1.0, self.amplitude)) * 255)
            if level != self._last_amp_level:
                self._last_amp_level = level
                self._send(f"M{level:03d}")

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                self.logger.error(f"Chest control loop error: {e}")
            await asyncio.sleep(1 / self.LOOP_HZ)

    # ------------------------------------------------------------------ events

    def _interactive(self) -> bool:
        return self.system_mode == "INTERACTIVE"

    async def _on_mode(self, payload: Any = None) -> None:
        mode = str((payload or {}).get("new_mode", "")).upper().split(".")[-1]
        if not mode:
            return
        self.system_mode = mode
        if mode != "STARTUP":
            self.booting = False
        self.target_pattern = "SI" if mode in ("IDLE", "STARTUP", "SLEEPING") else "SE"

    async def _on_listening(self, payload: Any = None) -> None:
        if self._interactive():
            self.target_pattern = "SL"

    async def _on_thinking(self, payload: Any = None) -> None:
        if self._interactive():
            self.target_pattern = "ST"

    async def _on_llm_chunk(self, payload: Any = None) -> None:
        if self._interactive() and self.target_pattern == "ST":
            self.target_pattern = "SS"

    async def _on_speech_started(self, payload: Any = None) -> None:
        if self._interactive():
            self.amplitude = 0.0
            self._last_amp_level = -1
            self.target_pattern = "SS"

    async def _on_amplitude(self, payload: Any = None) -> None:
        if self.target_pattern != "SS" or not isinstance(payload, dict):
            return
        a = float(payload.get("amplitude", 0.0) or 0.0)
        self.amplitude = self.AMP_ALPHA * a + (1 - self.AMP_ALPHA) * self.amplitude

    async def _on_speech_ended(self, payload: Any = None) -> None:
        if not self._interactive() or self.target_pattern != "SS":
            return
        # Unlike the face adapter's throttle, the reset is never dropped: send it now.
        self.amplitude = 0.0
        self._last_amp_level = 0
        if not self.frozen and "M" not in self._overrides:
            self._send("M000")
        self._sparkle = True  # alongside the eyes' green flash
        self.target_pattern = "SE"

    async def _on_music_started(self, payload: Any = None) -> None:
        # The real tempo from offline beat analysis when the track has one (MusicTrack.bpm);
        # CHEST_DEFAULT_BPM only when it does not. A crossfade re-announces the new track.
        track = (payload or {}).get("track") or {}
        bpm = track.get("bpm") if isinstance(track, dict) else None
        try:
            self.bpm = int(round(float(bpm))) if bpm and float(bpm) > 0 else self._default_bpm
        except (TypeError, ValueError):
            self.bpm = self._default_bpm

    async def _on_music_stopped(self, payload: Any = None) -> None:
        self.bpm = 0

    async def _on_dj_mode(self, payload: Any = None) -> None:
        active = bool((payload or {}).get("is_active", (payload or {}).get("active", False)))
        if active and not self.bpm:
            self.bpm = self._default_bpm
        elif not active:
            self.bpm = 0

    async def _on_service_status(self, payload: Any = None) -> None:
        if not isinstance(payload, dict):
            return
        service = payload.get("service")
        status = payload.get("status")
        if service and status is not None:
            status = str(getattr(status, "value", status)).lower()
            message = str(payload.get("message", ""))
            latched = message.startswith(("Failed to start", "Failed to initialize"))
            self._svc_status[service] = (status, time.monotonic(), latched)

    # ------------------------------------------------------------------ show control

    async def _on_override(self, payload: Any = None) -> None:
        try:
            req = ChestOverridePayload(**(payload or {}))
        except Exception as e:
            self.logger.warning(f"Invalid chest.override payload: {e}")
            return
        if self.frozen:
            return
        cmd = req.command.strip()
        if not cmd:
            return
        self._send(cmd)
        key = cmd[0].upper()
        if req.hold and req.hold > 0:
            until = time.monotonic() + req.hold
            if key in STATUS_CHANNELS:
                self._overrides[key] = until
            else:  # e.g. "R": no single channel; re-assert everything afterwards
                self._reassert_all_at = max(self._reassert_all_at or 0.0, until)
        # hold 0: nothing to schedule. _sent still holds the status value from before the
        # override, so tick() stays quiet on this channel until that status changes.

    async def _on_freeze(self, payload: Any = None) -> None:
        try:
            on = MotionFreezePayload(**(payload or {})).on
        except Exception as e:
            self.logger.warning(f"Invalid motion.freeze payload: {e}")
            return
        if on == self.frozen:
            return
        self.frozen = on
        if not on:
            self._overrides.clear()
            self._sent.clear()  # re-assert every channel on release
            self._last_amp_level = -1
