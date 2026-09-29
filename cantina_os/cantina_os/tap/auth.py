"""Token + Origin checks shared by every CantinaOS websocket (the tap and SimBridge).

Loopback is not a trust boundary: any web page the browser has open can reach 127.0.0.1.
So every bind requires a token, and a browser (which always sends ``Origin``) must also come
from an allowed origin. Non-browser clients (the Rust bridge, scripts) send no Origin.

Token: ``R3X_TAP_TOKEN`` if set, else ``~/.config/dj-r3x/tap_token`` (created 0600 with a
random value on first use; ``R3X_TOKEN_FILE`` moves it). Clients present it as
``Authorization: Bearer <token>`` or, for browsers (which cannot set headers on a
WebSocket), as the ``token`` query parameter.

Allowed origins: the panel's dev-server origins below, plus ``R3X_ALLOWED_ORIGINS``
(comma-separated). ``python -m cantina_os.tap.auth`` prints the token (for ``./r3x``).
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Iterable, Mapping, Optional, Set
from urllib.parse import parse_qs, urlsplit

DEFAULT_ORIGINS = tuple(
    f"http://{host}:{port}"
    for port in (5391, 5173, 4173)  # ./r3x panel, plain `vite`, `vite preview`
    for host in ("localhost", "127.0.0.1")
)


def token_file(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("R3X_TOKEN_FILE") or Path.home() / ".config" / "dj-r3x" / "tap_token")


def load_token(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    explicit = (env.get("R3X_TAP_TOKEN") or "").strip()
    if explicit:
        return explicit
    path = token_file(env)
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)
    return token


def allowed_origins(env: Optional[Mapping[str, str]] = None) -> Set[str]:
    env = os.environ if env is None else env
    extra = [o.strip().rstrip("/") for o in (env.get("R3X_ALLOWED_ORIGINS") or "").split(",")]
    return set(DEFAULT_ORIGINS) | {o for o in extra if o}


def check(headers: Mapping[str, str], path: str, token: str, origins: Iterable[str]) -> Optional[str]:
    """Return None if the handshake may proceed, else a short refusal reason."""
    origin = headers.get("Origin")
    if origin is not None and origin.rstrip("/") not in set(origins):
        return "origin not allowed"
    presented = ""
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        presented = auth[7:].strip()
    if not presented:
        presented = (parse_qs(urlsplit(path).query).get("token") or [""])[0]
    if not presented or not hmac.compare_digest(presented.encode(), token.encode()):
        return "bad or missing token"
    return None


def process_request_hook(token: str, origins: Iterable[str], logger=None):
    """A ``websockets.asyncio.server.serve(process_request=...)`` hook enforcing ``check``."""
    origins = set(origins)

    def hook(connection, request):
        reason = check(request.headers, request.path, token, origins)
        if reason is None:
            return None
        if logger is not None:
            logger.warning(f"Refused websocket client ({reason}; Origin={request.headers.get('Origin')})")
        status = 403 if reason.startswith("origin") else 401
        return connection.respond(status, reason + "\n")

    return hook


if __name__ == "__main__":
    print(load_token())
