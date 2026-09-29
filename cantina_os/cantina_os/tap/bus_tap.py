"""Full-bus tap: every emit on the CantinaOS bus, whoever made it.

``BaseService.emit`` is not a choke point (~40 call sites use ``_event_bus.emit`` directly,
and there are two base classes), so the tap is the emitter itself: ``TappedEmitter`` is the
pyee ``AsyncIOEventEmitter`` that ``main.py`` builds, with ``emit`` recording first.

Each record: ``seq, t_mono, t_wall, topic, source, payload``. ``source`` is best-effort: the
``service_name`` of the nearest service on the call stack, else ``module:<name>`` of the first
caller outside the tap/pyee, else ``unknown`` (e.g. a thread hop via ``call_soon_threadsafe``).
Payloads are made JSON-safe once (strings/lists truncated, bytes summarised) and shared by
every sink. A sink that raises is ignored - the tap must never break the bus.
"""

from __future__ import annotations

import enum
import itertools
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pyee.asyncio import AsyncIOEventEmitter

MAX_STR = 2000
MAX_ITEMS = 200
MAX_DEPTH = 8
_SKIP_MODULES = ("pyee", "cantina_os.tap.bus_tap", "asyncio")
_PYEE_INTERNAL = {"new_listener", "error"}

Record = Dict[str, Any]


def jsonable(value: Any, depth: int = 0) -> Any:
    """A JSON-safe, bounded copy of a bus payload."""
    if depth > MAX_DEPTH:
        return "<depth>"
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except Exception:
            return repr(value)[:MAX_STR]
    if isinstance(value, enum.Enum):
        value = value.value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR] + "...<truncated>"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": len(value)}
    if isinstance(value, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [jsonable(v, depth + 1) for v in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            out.append(f"<{len(items) - MAX_ITEMS} more>")
        return out
    if hasattr(value, "tolist"):  # numpy arrays / scalars
        try:
            return jsonable(value.tolist(), depth + 1)
        except Exception:
            pass
    return repr(value)[:MAX_STR]


def _derive_source() -> str:
    frame = sys._getframe(2)
    module_name = None
    for _ in range(12):
        if frame is None:
            break
        owner = frame.f_locals.get("self")
        name = getattr(owner, "_service_name", None) if owner is not None else None
        if isinstance(name, str) and name:
            return name
        mod = frame.f_globals.get("__name__", "")
        if module_name is None and not mod.startswith(_SKIP_MODULES):
            module_name = mod
        frame = frame.f_back
    return f"module:{module_name}" if module_name else "unknown"


class TappedEmitter(AsyncIOEventEmitter):
    """``AsyncIOEventEmitter`` whose ``emit`` hands a record to every sink first."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seq = itertools.count(1)
        self._seq_lock = threading.Lock()
        self._sinks: List[Callable[[Record], None]] = []
        self.last_seq = 0

    def add_sink(self, sink: Callable[[Record], None]) -> None:
        self._sinks.append(sink)

    def remove_sink(self, sink: Callable[[Record], None]) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    def _record(self, event: Any, args: tuple, source: Optional[str]) -> Optional[int]:
        if not self._sinks:
            return None
        topic = getattr(event, "value", event)
        if not isinstance(topic, str) or topic in _PYEE_INTERNAL:
            return None
        try:
            with self._seq_lock:
                seq = next(self._seq)
                self.last_seq = seq
            rec = {
                "seq": seq,
                "t_mono": time.monotonic(),
                "t_wall": time.time(),
                "topic": topic,
                "source": source or _derive_source(),
                "payload": jsonable(args[0] if args else None),
            }
        except Exception:
            return None
        for sink in list(self._sinks):
            try:
                sink(rec)
            except Exception:
                pass
        return seq

    def emit(self, event, *args, **kwargs):
        self._record(event, args, None)
        return super().emit(event, *args, **kwargs)

    def emit_as(self, source: str, event: str, payload: Any = None) -> Optional[int]:
        """Emit with an explicit source (inbound tap messages). Returns the record's seq."""
        seq = self._record(event, (payload,), source)
        super().emit(event, payload)
        return seq


def _enabled(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class SessionLog:
    """JSONL sink: one header line, then one line per bus event. Thread-safe, lazy-open."""

    def __init__(self, path: Path, header: Optional[Dict[str, Any]] = None):
        self.path = Path(path)
        self._header = header or {}
        self._lock = threading.Lock()
        self._fh = None
        self._closed = False

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", buffering=1, encoding="utf-8")
        self._fh.write(json.dumps({"kind": "header", **self._header}) + "\n")

    def __call__(self, rec: Record) -> None:
        line = json.dumps(rec, separators=(",", ":"))
        with self._lock:
            if self._closed:
                return
            if self._fh is None:
                self._open()
            self._fh.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def default_session_log(session: str, fixture_dir: Optional[Path] = None) -> Optional[SessionLog]:
    """The session log configured by env: ``R3X_SESSION_LOG`` (default on),
    ``R3X_SESSION_LOG_DIR`` (default ``<repo>/logs``). While recording fixtures the trace is
    written into the fixture folder as ``trace.jsonl`` instead."""
    if not _enabled("R3X_SESSION_LOG"):
        return None
    header = {"session": session, "t_wall": time.time(), "t_mono": time.monotonic(), "pid": os.getpid(),
              "fixtures": os.environ.get("R3X_FIXTURES", "") or None}
    if fixture_dir is not None and os.environ.get("R3X_FIXTURES", "").lower() == "record":
        return SessionLog(Path(fixture_dir) / "trace.jsonl", header)
    log_dir = Path(os.environ.get("R3X_SESSION_LOG_DIR") or repo_root() / "logs")
    return SessionLog(log_dir / f"session-{session}.jsonl", header)
