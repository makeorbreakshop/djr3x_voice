"""Fixture recorder / replayer for external exchanges (plan section 8.3-8.4).

  R3X_FIXTURES=record   R3X_FIXTURE_DIR=fixtures/<name>   (default fixtures/<session>)
  R3X_FIXTURES=replay   R3X_FIXTURE_DIR=fixtures/<name>   (required)

Hooks sit at the client boundaries, each one or two lines at the call site:

  Jev          ``JevClient.classify``          key: sha1(request body)
  Claude       the ``Anthropic`` client        key: method + tool_choice + last user message
  ElevenLabs   ``_open_audio_stream``          key: model + text
  Deepgram     ``_on_transcript``              record only (the harness injects transcripts)
  random picks ``fixtures.choice``             track picks (BrainService, MusicController)

Every streamed item records how long the consumer waited for it (``wait``), so replay
reproduces chunk boundaries *and* pacing without double-counting the consumer's own time.
Identical requests are replayed in recorded order. A miss is logged at WARNING and fails
the call the way the real client would fail - the trace then diverges visibly.

Files in the folder: ``jev.jsonl``, ``claude.jsonl``, ``tts.jsonl`` + ``tts/<n>.pcm``,
``choice.jsonl``, ``deepgram.jsonl``, ``meta.json``. ``random`` is seeded (``R3X_SEED``, default 1234) in
both modes so track picks repeat.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Iterator, List, Optional, Tuple

logger = logging.getLogger("cantina_os.tap.fixtures")


def _key(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def claude_key(method: str, kwargs: Dict[str, Any]) -> str:
    last_user = next((m.get("content") for m in reversed(kwargs.get("messages") or [])
                      if isinstance(m, dict) and m.get("role") == "user"), None)
    return _key("claude", method, kwargs.get("tool_choice"), last_user)


class FixtureStore:
    def __init__(self, mode: str, folder: Path):
        self.mode = mode
        self.dir = Path(folder)
        self._lock = threading.Lock()
        self._replay: Dict[str, Dict[str, Deque[dict]]] = defaultdict(lambda: defaultdict(deque))
        self._last: Dict[Tuple[str, str], dict] = {}
        self._tts_n = 0
        random.seed(int(os.environ.get("R3X_SEED", "1234")))
        if mode == "record":
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "meta.json").write_text(json.dumps({"recorded": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                            "seed": os.environ.get("R3X_SEED", "1234")}))
        else:
            for kind in ("jev", "claude", "tts", "choice", "deepgram"):
                path = self.dir / f"{kind}.jsonl"
                if path.exists():
                    for line in path.read_text().splitlines():
                        if line.strip():
                            rec = json.loads(line)
                            self._replay[kind][rec["key"]].append(rec)
        logger.info(f"Fixtures: {mode} {self.dir}")

    @property
    def replaying(self) -> bool:
        return self.mode == "replay"

    def write(self, kind: str, rec: dict) -> None:
        with self._lock:
            with open(self.dir / f"{kind}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")

    def take(self, kind: str, key: str) -> Optional[dict]:
        with self._lock:
            q = self._replay[kind].get(key)
            if q:
                rec = q.popleft()
                self._last[(kind, key)] = rec
                return rec
            rec = self._last.get((kind, key))
        if rec is None:
            logger.warning(f"Fixture miss: {kind} {key}")
        return rec

    # ------------------------------------------------------------------ Jev

    def jev_record(self, payload: dict, status: int, body: Any, latency_ms: float) -> None:
        self.write("jev", {"key": _key("jev", payload), "state": payload.get("state"),
                           "status": status, "body": body, "latency_ms": latency_ms})

    def jev_replay(self, payload: dict) -> Optional[dict]:
        return self.take("jev", _key("jev", payload))

    # ------------------------------------------------------------------ Claude

    def wrap_anthropic(self, client: Any, name: str) -> Any:
        return _AnthropicProxy(client, self, name)

    # ------------------------------------------------------------------ ElevenLabs

    def tts(self, text: str, model_id: str, open_live) -> Iterator[Tuple[bytes, Optional[dict]]]:
        key = _key("tts", model_id, text)
        if self.replaying:
            return self._tts_replay(key)
        return self._tts_record(key, text, model_id, open_live)

    def _tts_record(self, key, text, model_id, open_live):
        t0 = time.monotonic()
        stream = open_live()
        chunks: List[dict] = []
        audio = bytearray()
        opened = time.monotonic() - t0
        it = iter(stream)
        try:
            while True:
                t = time.monotonic()
                try:
                    chunk, alignment = next(it)
                except StopIteration:
                    break
                wait = time.monotonic() - t + (opened if not chunks else 0.0)
                chunks.append({"wait": round(wait, 4), "len": len(chunk), "alignment": alignment})
                audio.extend(chunk)
                yield chunk, alignment
        finally:
            with self._lock:
                self._tts_n += 1
                n = self._tts_n
            (self.dir / "tts").mkdir(exist_ok=True)
            (self.dir / "tts" / f"{n}.pcm").write_bytes(bytes(audio))
            self.write("tts", {"key": key, "text": text, "model": model_id, "audio": f"tts/{n}.pcm",
                               "chunks": chunks})

    def _tts_replay(self, key):
        rec = self.take("tts", key)
        if rec is None:
            raise RuntimeError("no TTS fixture for this line")
        audio = (self.dir / rec["audio"]).read_bytes()
        pos = 0
        for c in rec["chunks"]:
            time.sleep(c["wait"])
            yield audio[pos:pos + c["len"]], c["alignment"]
            pos += c["len"]

    def tts_bytes(self, text: str, model_id: str, produce) -> bytes:
        """Whole-file TTS (cached DJ commentary): ``produce()`` returns the bytes."""
        key = _key("tts_bytes", model_id, text)
        if self.replaying:
            rec = self.take("tts", key)
            if rec is None:
                raise RuntimeError("no TTS fixture for this line")
            time.sleep(rec.get("wait", 0.0))
            return (self.dir / rec["audio"]).read_bytes()
        t0 = time.monotonic()
        data = produce()
        with self._lock:
            self._tts_n += 1
            n = self._tts_n
        (self.dir / "tts").mkdir(exist_ok=True)
        (self.dir / "tts" / f"{n}.mp3").write_bytes(data)
        self.write("tts", {"key": key, "text": text, "model": model_id, "audio": f"tts/{n}.mp3",
                           "wait": round(time.monotonic() - t0, 4)})
        return data

    # ------------------------------------------------------------------ random picks

    def choice(self, site: str, options: List[Any], name=lambda o: o) -> Any:
        """``random.choice`` whose result is part of the fixture: random state is consumed by
        timer-driven code, so a seed alone does not make a track pick repeat."""
        if self.replaying:
            rec = self.take("choice", site)
            if rec is not None:
                for o in options:
                    if name(o) == rec["picked"]:
                        return o
                logger.warning(f"Fixture choice {site}: recorded {rec['picked']!r} is not an option")
            return random.choice(options)
        picked = random.choice(options)
        self.write("choice", {"key": site, "picked": name(picked)})
        return picked

    # ------------------------------------------------------------------ Deepgram

    def transcript(self, payload: dict) -> None:
        if self.mode == "record":
            self.write("deepgram", {"key": _key("dg", payload.get("conversation_id")), "t_wall": time.time(),
                                    **{k: payload.get(k) for k in ("text", "is_final", "confidence", "words",
                                                                   "conversation_id")}})


# ---------------------------------------------------------------------- Anthropic proxy

def _message_from(d: dict) -> Any:
    from anthropic.types import Message

    return Message.model_validate(d)


class _RecordingStream:
    def __init__(self, mgr, store: FixtureStore, key: str, name: str):
        self._mgr, self._store, self._key, self._name = mgr, store, key, name
        self._chunks: List[dict] = []
        self._final: Optional[dict] = None

    def __enter__(self):
        t0 = time.monotonic()
        self._s = self._mgr.__enter__()
        self._opened = time.monotonic() - t0
        return self

    def __exit__(self, *exc):
        self._store.write("claude", {"key": self._key, "client": self._name, "method": "stream",
                                     "chunks": self._chunks, "final": self._final})
        return self._mgr.__exit__(*exc)

    @property
    def text_stream(self):
        it = iter(self._s.text_stream)
        while True:
            t = time.monotonic()
            try:
                text = next(it)
            except StopIteration:
                return
            wait = time.monotonic() - t + (self._opened if not self._chunks else 0.0)
            self._chunks.append({"wait": round(wait, 4), "text": text})
            yield text

    def get_final_message(self):
        msg = self._s.get_final_message()
        self._final = msg.model_dump(mode="json")
        return msg

    def __getattr__(self, name):
        return getattr(self._s, name)


class _ReplayStream:
    def __init__(self, rec: dict):
        self._rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for c in self._rec["chunks"]:
            time.sleep(c["wait"])
            yield c["text"]

    def get_final_message(self):
        return _message_from(self._rec["final"])


class _Messages:
    def __init__(self, real, store: FixtureStore, name: str):
        self._real, self._store, self._name = real, store, name

    def stream(self, **kwargs):
        key = claude_key("stream", kwargs)
        if self._store.replaying:
            rec = self._store.take("claude", key)
            if rec is None:
                raise RuntimeError("no Claude fixture for this request")
            return _ReplayStream(rec)
        return _RecordingStream(self._real.stream(**kwargs), self._store, key, self._name)

    def create(self, **kwargs):
        key = claude_key("create", kwargs)
        if self._store.replaying:
            rec = self._store.take("claude", key)
            if rec is None:
                raise RuntimeError("no Claude fixture for this request")
            time.sleep(rec.get("wait", 0.0))
            return _message_from(rec["final"])
        t0 = time.monotonic()
        msg = self._real.create(**kwargs)
        self._store.write("claude", {"key": key, "client": self._name, "method": "create",
                                     "wait": round(time.monotonic() - t0, 4),
                                     "final": msg.model_dump(mode="json")})
        return msg

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AnthropicProxy:
    def __init__(self, client, store: FixtureStore, name: str):
        self._client = client
        self.messages = _Messages(client.messages, store, name)

    def __getattr__(self, name):
        return getattr(self._client, name)


# ---------------------------------------------------------------------- module singleton

_store: Optional[FixtureStore] = None
_configured = False


def configure(mode: Optional[str] = None, folder: Optional[Path] = None, session: Optional[str] = None
              ) -> Optional[FixtureStore]:
    """(Re)configure from arguments or env. Called once by CantinaOS; tests call it directly."""
    global _store, _configured
    mode = (mode if mode is not None else os.environ.get("R3X_FIXTURES", "")).strip().lower()
    _configured = True
    if mode not in ("record", "replay"):
        _store = None
        return None
    raw = folder or os.environ.get("R3X_FIXTURE_DIR")
    if not raw:
        if mode == "replay":
            raise RuntimeError("R3X_FIXTURES=replay needs R3X_FIXTURE_DIR")
        from .bus_tap import repo_root

        raw = repo_root() / "fixtures" / (session or time.strftime("%Y%m%d-%H%M%S"))
    _store = FixtureStore(mode, Path(raw))
    return _store


def get() -> Optional[FixtureStore]:
    if not _configured:
        configure()
    return _store


def replaying() -> bool:
    s = get()
    return s is not None and s.replaying


def choice(site: str, options: List[Any], name=lambda o: o) -> Any:
    s = get()
    return random.choice(options) if s is None else s.choice(site, options, name)


def wrap_anthropic(client: Any, name: str) -> Any:
    s = get()
    return client if s is None else s.wrap_anthropic(client, name)
