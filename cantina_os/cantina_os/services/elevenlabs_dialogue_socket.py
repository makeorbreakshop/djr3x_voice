"""A warm ElevenLabs text-to-dialogue WebSocket for Eleven v4 / v4 Turbo.

Why this exists
---------------
v4 Turbo is not accepted on the plain text-to-speech WebSocket (400 ``unsupported_model``:
"Use the text-to-dialogue websocket endpoint instead"). The dialogue socket takes it and,
with ``sync_alignment=true``, returns per-character timing with every audio chunk - which
the show system needs to land cues on words instead of estimating.

Whole replies only. Each reply is sent as one input followed by a flush, so ElevenLabs
generates it in one pass. Feeding text in fragments as Claude streams it would start audio
sooner, but sections synthesised separately sounded disjointed, which is why the service
waits for the complete reply in the first place.

Threading: ``synthesize`` runs on ElevenLabsService's audio thread; a small keep-alive
thread pings the socket while it is idle (the server closes after 20 s of silence). One
lock serialises them, and the keep-alive never waits for it.

Fail-open: any error closes the socket and raises. The caller falls back to the HTTP stream
if no audio had been produced yet; the next request reconnects.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
SAMPLE_RATE = 24000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # pcm_24000 is 16-bit mono
KEEPALIVE_S = 15.0  # server idle timeout is 20 s
RECV_TIMEOUT_S = 10.0

#: (pcm bytes, alignment for exactly those bytes or None)
AudioChunk = Tuple[bytes, Optional[Dict[str, Any]]]


def supports_model(model_id: str) -> bool:
    return model_id.startswith("eleven_v4")


def _default_connect(url: str, api_key: str):
    from websockets.sync.client import connect

    return connect(url, additional_headers={"xi-api-key": api_key}, open_timeout=5)


class DialogueSocket:
    """One persistent dialogue socket, bound to a single voice and model."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str,
        stability: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
        connect: Callable[[str, str], Any] = _default_connect,
        keepalive_s: float = KEEPALIVE_S,
    ):
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._stability = stability
        self._log = logger or logging.getLogger(__name__)
        self._connect = connect
        self._keepalive_s = keepalive_s
        self._ws = None
        self._lock = threading.Lock()
        self._last_io = 0.0
        self._closed = threading.Event()
        self._keepalive_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ lifecycle

    def handles(self, model_id: str, voice_id: str) -> bool:
        """This socket serves a request only for its own model and voice (v4 Turbo allows one
        registered voice per connection)."""
        return model_id == self._model_id and voice_id == self._voice_id and not self._closed.is_set()

    def start(self) -> None:
        """Connect now (so the first reply doesn't pay for it) and start the keep-alive."""
        with self._lock:
            try:
                self._ensure_open()
            except Exception as e:  # warm-up is best effort; synthesize() retries
                self._log.warning(f"ElevenLabs dialogue socket warm-up failed: {e}")
        if self._keepalive_thread is None:
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, name="elevenlabs-keepalive", daemon=True)
            self._keepalive_thread.start()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            self._drop()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=2)
            self._keepalive_thread = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def _ensure_open(self):
        if self._ws is not None:
            return self._ws
        query = f"?model_id={self._model_id}&output_format=pcm_24000&sync_alignment=true"
        ws = self._connect(URL + query, self._api_key)
        init: Dict[str, Any] = {"voices": [self._voice_id]}
        if self._stability is not None:
            # The dialogue socket takes stability only; similarity/style/speed don't exist here.
            init["voice_settings"] = {"stability": self._stability}
        ws.send(json.dumps(init))
        self._ws = ws
        self._last_io = time.monotonic()
        self._log.info(f"ElevenLabs dialogue socket open ({self._model_id})")
        return ws

    def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _keepalive_loop(self) -> None:
        while not self._closed.wait(1.0):
            if self._ws is None or time.monotonic() - self._last_io < self._keepalive_s:
                continue
            if not self._lock.acquire(blocking=False):
                continue  # a reply is streaming; that counts as activity
            try:
                if self._ws is not None:
                    self._ws.send(json.dumps({"keep_alive": True}))
                    self._last_io = time.monotonic()
            except Exception as e:
                self._log.info(f"ElevenLabs dialogue socket keep-alive failed ({e}); will reconnect on next reply")
                self._drop()
            finally:
                self._lock.release()

    # ------------------------------------------------------------------ synthesis

    def synthesize(self, text: str, should_stop: Callable[[], bool] = lambda: False) -> Iterator[AudioChunk]:
        """Yield (pcm, alignment) for ``text``, one turn, until the server ends the turn.

        Alignment times in each item are relative to the start of *that chunk* (that is how
        the server sends them); ``to_absolute`` rebases them.
        """
        with self._lock:
            try:
                ws = self._ensure_open()
                ws.send(json.dumps({"inputs": [{"text": text, "voice_id": self._voice_id, "new_turn": True}]}))
                ws.send(json.dumps({"flush": True}))
                self._last_io = time.monotonic()
                while True:
                    if should_stop():
                        # The rest of this turn would arrive on the next request; don't reuse it.
                        self._drop()
                        return
                    msg = json.loads(ws.recv(timeout=RECV_TIMEOUT_S))
                    self._last_io = time.monotonic()
                    if msg.get("error") or msg.get("code"):
                        raise RuntimeError(f"ElevenLabs dialogue socket error: {msg}")
                    audio = msg.get("audio")
                    if audio:
                        yield base64.b64decode(audio), msg.get("alignment")
                    if msg.get("is_final_audio_for_turn") or msg.get("is_final"):
                        return
            except GeneratorExit:
                # The consumer stopped mid-turn; leftover audio would bleed into the next reply.
                self._drop()
                raise
            except Exception:
                self._drop()
                raise


def to_absolute(alignment: Dict[str, Any], chunk_offset_ms: float) -> Dict[str, List[Any]]:
    """Rebase one chunk's alignment onto the whole reply's timeline."""
    chars = list(alignment.get("chars") or [])
    starts = [chunk_offset_ms + float(t) for t in (alignment.get("char_start_times_ms") or [])]
    durations = [float(d) for d in (alignment.get("char_durations_ms") or [])]
    return {"chars": chars, "char_start_ms": starts, "char_duration_ms": durations}
