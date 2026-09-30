"""Tap websocket: every bus event out, ``{topic, payload}`` emits back in.

This is the socket the Phase 1 Rust bridge connects to. Loopback only by default, token +
Origin checked on the handshake (``tap/auth.py``).

  URL   ws://127.0.0.1:8766/   (R3X_TAP_HOST / R3X_TAP_PORT; R3X_TAP_ENABLED=0 turns it off)
  auth  Authorization: Bearer <token>   or   ?token=<token>

  out   {"v":1,"kind":"hello","session":"...","seq":<last seq>,"t_mono":..,"t_wall":..}
        {"v":1,"kind":"event","seq":..,"t_mono":..,"t_wall":..,"topic":"..","source":"..","payload":{..}}
        {"v":1,"kind":"ack","re":<id>,"ok":true,"seq":<seq of the emitted event>}
        {"v":1,"kind":"ack","re":<id>,"ok":false,"error":"..."}
  in    {"topic":"music.command","payload":{...},"id":"optional","source":"optional"}

Inbound events are emitted with source ``tap`` (or ``tap:<source>``). Sending never blocks
the bus: websockets' ``broadcast`` skips a client whose buffer is full.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Optional, Set

from . import auth
from .bus_tap import Record, TappedEmitter

logger = logging.getLogger("cantina_os.tap")


class TapServer:
    def __init__(self, bus: TappedEmitter, session: str, host: Optional[str] = None,
                 port: Optional[int] = None, token: Optional[str] = None):
        self._bus = bus
        self._session = session
        self.host = host or os.environ.get("R3X_TAP_HOST", "127.0.0.1")
        self.port = int(port if port is not None else os.environ.get("R3X_TAP_PORT", "8766"))
        self._token = token
        self._clients: Set[Any] = set()
        self._server = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[int] = None

    async def start(self) -> bool:
        try:
            from websockets.asyncio.server import serve
        except ImportError as e:  # pragma: no cover
            logger.warning(f"Bus tap websocket unavailable: {e}")
            return False
        token = self._token or auth.load_token()
        try:
            self._server = await serve(
                self._handle, self.host, self.port,
                process_request=auth.process_request_hook(token, auth.allowed_origins(), logger),
                max_size=2**20,
            )
        except OSError as e:
            logger.warning(f"Bus tap could not listen on {self.host}:{self.port} ({e}); continuing without it")
            return False
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._bus.add_sink(self._on_record)
        logger.info(f"Bus tap listening on ws://{self.host}:{self.port}/ (token auth)")
        return True

    async def stop(self) -> None:
        self._bus.remove_sink(self._on_record)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._clients.clear()

    # ------------------------------------------------------------------ out

    def _on_record(self, rec: Record) -> None:
        if not self._clients:
            return
        msg = json.dumps({"v": 1, "kind": "event", **rec}, separators=(",", ":"))
        if threading.get_ident() == self._loop_thread:
            self._broadcast(msg)
        elif self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._broadcast, msg)
            except RuntimeError:
                pass  # loop closed

    def _broadcast(self, msg: str) -> None:
        from websockets.asyncio.server import broadcast

        broadcast(self._clients, msg)

    # ------------------------------------------------------------------ in

    async def _handle(self, ws) -> None:
        await ws.send(json.dumps({"v": 1, "kind": "hello", "session": self._session,
                                  "seq": self._bus.last_seq, "t_mono": time.monotonic(),
                                  "t_wall": time.time()}))
        self._clients.add(ws)
        try:
            async for raw in ws:
                reply = self._inbound(raw)
                if reply is not None:
                    await ws.send(json.dumps(reply))
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    def _inbound(self, raw: Any) -> Optional[dict]:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return {"v": 1, "kind": "ack", "re": None, "ok": False, "error": "not JSON"}
        if not isinstance(msg, dict) or not isinstance(msg.get("topic"), str) or not msg["topic"]:
            return {"v": 1, "kind": "ack", "re": None, "ok": False, "error": "expected {topic, payload}"}
        src = msg.get("source")
        source = f"tap:{src}" if isinstance(src, str) and src else "tap"
        payload = msg.get("payload")
        try:
            seq = self._bus.emit_as(source, msg["topic"], {} if payload is None else payload)
        except Exception as e:
            return {"v": 1, "kind": "ack", "re": msg.get("id"), "ok": False, "error": str(e)[:200]}
        return {"v": 1, "kind": "ack", "re": msg.get("id"), "ok": True, "seq": seq}
