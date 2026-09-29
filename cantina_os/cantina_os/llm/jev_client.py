"""
Jev (typesafe.ai systemone) client — the fast intent classifier for CantinaOS.

Jev answers a fixed set of small, independent questions about one piece of text in a single
round trip, and returns calibrated probabilities rather than prose. That makes it usable on the
hot path in a way a chat model is not: measured p50 is ~190 ms against Claude's ~1,400 ms.

Design constraints, all deliberate:

* **One connection, kept warm.** A cold call pays TLS setup and measures ~440 ms; a warm one
  measures ~190 ms. The client therefore owns a persistent ``httpx.AsyncClient`` and exposes
  :meth:`prewarm` so the voice loop can pay that cost on ``engage`` instead of mid-utterance.
* **No retries on the hot path.** A retry would blow the latency budget it exists to protect.
  ``attempts`` defaults to 1. The reference TypeScript client retries 429/529 because it runs
  offline batch work; this one does not.
* **Fail open, never raise into the voice loop.** :meth:`classify` returns ``None`` on any
  error — timeout, HTTP status, malformed body — and the caller falls back to the Claude path,
  which still works, just slowly. A dead classifier must degrade to "slow", never to "broken".

Reference implementation: ``~/video-scripter-v2/video-scripter/scripts/angles/jev.ts``.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from ..tap import fixtures as tap_fixtures

JEV_URL = "https://api.typesafe.ai/v1/systemone"
#: Pinned deliberately. The benchmark measured jev-1.13.0 and the report is explicit: "Do not
#: use jev-latest in production." A silent model bump would move every threshold below.
JEV_MODEL = "jev-1.13.0"

#: Hot-path ceiling. Measured p50 is 183 ms and p95 ~300 ms, so 800 ms is ~2.7x the tail: late
#: enough to ride out a slow call, early enough that the Claude path is not held open for long.
#: The reference TypeScript client's 120 s default is for batch work and is wrong here.
DEFAULT_TIMEOUT_S = 0.8

logger = logging.getLogger(__name__)


@dataclass
class JevAnswer:
    """One question's answer. Exactly one of ``noul`` / ``score`` / ``choice`` is meaningful."""

    type: str
    noul: Optional[float] = None
    score: Optional[int] = None
    choice: Optional[str] = None
    confidence: Optional[float] = None
    probabilities: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "JevAnswer":
        return cls(
            type=raw.get("type", ""),
            noul=raw.get("noul"),
            score=raw.get("score"),
            choice=raw.get("choice"),
            confidence=raw.get("confidence"),
            probabilities=raw.get("probabilities") or {},
        )


@dataclass
class JevResult:
    """A whole response, plus the client-side latency the caller wants to log."""

    model: str
    answers: Dict[str, JevAnswer]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    def noul(self, key: str, default: float = 0.0) -> float:
        """Probability that question ``key`` is true, or ``default`` if it was not answered."""
        answer = self.answers.get(key)
        if answer is None or answer.noul is None:
            return default
        return float(answer.noul)


class JevClient:
    """Async Jev client holding one warm connection.

    Not thread-safe; construct and use it from a single event loop, which is how every
    CantinaOS service works.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        url: str = JEV_URL,
        model: str = JEV_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        attempts: int = 1,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self._api_key = (api_key or os.environ.get("TYPESAFE_API_KEY", "")).strip()
        self._url = url
        self._model = model
        self._timeout_s = timeout_s
        self._attempts = max(1, attempts)
        self.logger = logger_ or logger
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def configured(self) -> bool:
        """True when an API key is present. Without one the router must stay disabled."""
        return bool(self._api_key)

    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_s),
            # One host, one utterance at a time — a single kept-alive connection is the whole point.
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
            headers={
                "authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def prewarm(self) -> bool:
        """Open the TCP+TLS connection ahead of time with one throwaway question.

        Saves roughly 250 ms on the first real utterance. Returns whether it succeeded; a
        failure is logged and otherwise ignored, since the real call will retry the handshake.
        """
        result = await self.classify(
            {"utterance": "warmup"},
            {
                "warmup": {
                    "type": "noul",
                    "instructions": "Is this text the single word 'warmup'?",
                    "criteria": {"true": "It is the word warmup.", "false": "It is anything else."},
                }
            },
        )
        if result is None:
            self.logger.warning("Jev prewarm failed; first real call will pay connection setup")
            return False
        self.logger.info(f"Jev connection pre-warmed in {result.latency_ms:.0f} ms")
        return True

    async def classify(
        self,
        state: Any,
        questions: Dict[str, Dict[str, Any]],
    ) -> Optional[JevResult]:
        """Ask Jev every question in ``questions`` about ``state`` in one round trip.

        ``state`` is JSON-encoded if it is not already a string. Returns ``None`` on any
        failure — this method does not raise, because it is called from the voice loop.
        """
        if not self.configured:
            return None
        if self._client is None:
            await self.start()
        assert self._client is not None

        payload = {
            "model": self._model,
            "state": state if isinstance(state, str) else json.dumps(state),
            "questions": questions,
        }

        fx = tap_fixtures.get()  # Phase 0 fixture record/replay; None in normal runs
        last_error: Optional[str] = None
        for attempt in range(self._attempts):
            started = time.perf_counter()
            try:
                if fx is not None and fx.replaying:
                    rec = fx.jev_replay(payload)
                    if rec is None:
                        raise RuntimeError("no Jev fixture for this request")
                    await asyncio.sleep(rec["latency_ms"] / 1000.0)
                    status, body = rec["status"], rec["body"]
                else:
                    response = await self._client.post(self._url, json=payload)
                    status = response.status_code
                    body = response.json() if status == 200 else response.text[:200]
                latency_ms = (time.perf_counter() - started) * 1000.0
                if fx is not None and not fx.replaying:
                    fx.jev_record(payload, status, body, latency_ms)
                if status != 200:
                    last_error = f"HTTP {status}: {body}"
                    # 429/529 are the only genuinely retryable answers this API gives, but on the
                    # hot path there is no time to retry — record and fail open.
                    self.logger.warning(f"Jev request failed ({last_error})")
                    break
                return JevResult(
                    model=body.get("model", ""),
                    answers={
                        key: JevAnswer.from_dict(value)
                        for key, value in (body.get("answers") or {}).items()
                    },
                    input_tokens=(body.get("usage") or {}).get("input_tokens", 0),
                    output_tokens=(body.get("usage") or {}).get("output_tokens", 0),
                    latency_ms=latency_ms,
                )
            except (httpx.TimeoutException, asyncio.TimeoutError):
                last_error = f"timeout after {self._timeout_s:.1f}s"
                self.logger.warning(f"Jev request timed out after {self._timeout_s:.1f}s")
            except Exception as exc:  # noqa: BLE001 — fail open on anything at all
                last_error = f"{type(exc).__name__}: {exc}"
                self.logger.warning(f"Jev request error: {last_error}")
            if attempt + 1 < self._attempts:
                await asyncio.sleep(0.05)

        self.logger.debug(f"Jev classify failed, falling back to Claude path ({last_error})")
        return None
