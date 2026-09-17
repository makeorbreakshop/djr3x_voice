"""
Jev Fast Intent Router Service for CantinaOS
"""

"""
SERVICE: JevIntentService
PURPOSE: Classifies the voice transcript with Jev (~190 ms, speculatively pre-warmed off interim
         transcripts) and dispatches deterministic music / DJ / eye actions before Claude has
         replied, cutting transcript-to-action from ~1,400 ms to ~200 ms, or to ~0 ms on a
         speculative cache hit.
EVENTS_IN: VOICE_LISTENING_STOPPED, VOICE_LISTENING_STARTED, TRANSCRIPTION_FINAL,
           TRANSCRIPTION_INTERIM, SYSTEM_MODE_CHANGED
EVENTS_OUT: INTENT_DETECTED, INTENT_CONSUMED, SERVICE_STATUS_UPDATE
KEY_METHODS: _handle_voice_transcript, _classify_and_dispatch, _speculate
DEPENDENCIES: JevClient (typesafe.ai systemone API, TYPESAFE_API_KEY), FastRouterGate
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..core.fast_router_gate import GATE, ActionTaken
from ..event_payloads import IntentPayload, ServiceStatus
from ..llm import jev_intents
from ..llm.jev_client import JevClient, JevResult

#: Minimum words before a partial transcript is worth spending a speculative call on.
_MIN_SPECULATIVE_WORDS = 2

#: Debounce between speculative calls, so a fast talker does not trigger one per interim.
_SPECULATION_DEBOUNCE_S = 0.15

#: Speculative results held per turn. Small because the cache is cleared every turn.
_MAX_SPECULATIVE = 8


def _normalize(text: str) -> str:
    """Key for the speculative cache: casing and punctuation must not cause a miss."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


class JevIntentService(BaseService):
    """Fast, deterministic intent dispatch ahead of the Claude turn.

    ## What it does

    On ``VOICE_LISTENING_STOPPED`` it asks Jev one question set about the transcript. If a
    deterministic tool clears its risk tier's confidence gate, it emits ``INTENT_DETECTED`` —
    the same event ``ClaudeService`` emits after its tool call — so ``IntentRouterService``
    executes it with no changes to the execution side at all. It then records the action on
    :data:`~cantina_os.core.fast_router_gate.GATE` and emits ``INTENT_CONSUMED``, which is how
    ``ClaudeService`` learns to skip its own tool call and just speak.

    ## Speculative fan-out

    Jev costs ~$0.00008 a call, so waiting for the final transcript is leaving latency on the
    table. Partial transcripts (``TRANSCRIPTION_FINAL`` segments during recording, and debounced
    ``TRANSCRIPTION_INTERIM``) are classified as they arrive and the answers cached by
    normalized text. When the authoritative transcript lands and matches one, dispatch happens
    in well under a millisecond instead of ~190 ms. Misses fall through to a live call, and the
    cache is cleared at the start of every turn so a stale answer can never be reused.

    ## Failure behaviour

    Every failure mode resolves the gate with ``None``, which hands the turn to Claude
    unchanged: no API key, HTTP error, timeout, malformed response, low confidence, missing
    parameters, or an unhandled exception in this service. The router can only ever make the
    system faster or leave it as it was.
    """

    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__("jev_intent_service", event_bus, logger)
        raw_config = config or {}

        self._threshold = float(
            raw_config.get("JEV_CONFIDENCE_THRESHOLD", jev_intents.DEFAULT_THRESHOLD)
        )
        self._command_threshold = float(
            raw_config.get("JEV_COMMAND_THRESHOLD", jev_intents.DEFAULT_COMMAND_THRESHOLD)
        )
        self._timeout_s = float(raw_config.get("JEV_TIMEOUT_S", 0.8))
        self._enabled = bool(raw_config.get("JEV_ROUTER_ENABLED", True))
        self._speculate_enabled = bool(raw_config.get("JEV_SPECULATE", True))

        self._client = JevClient(
            api_key=raw_config.get("TYPESAFE_API_KEY"),
            timeout_s=self._timeout_s,
            attempts=1,  # no retries on the hot path — a retry blows the whole budget
            logger_=self.logger,
        )
        self._questions = jev_intents.build_questions()

        # Speculative state, all scoped to one turn.
        self._turn_id = 0
        self._speculative: Dict[str, Tuple[int, JevResult]] = {}
        self._last_speculated_at = 0.0
        self._last_speculated_text = ""

        self._stats: Dict[str, Any] = {
            "classified": 0,
            "executed": 0,
            "declined": 0,
            "failed": 0,
            "speculative_hits": 0,
            "speculative_calls": 0,
            "latency_ms": [],
            "dispatch_ms": [],
        }

    # ---------------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------------

    async def _start(self) -> None:
        try:
            if not self._enabled:
                self.logger.warning(
                    "JevIntentService disabled by config (JEV_ROUTER_ENABLED=false); "
                    "all turns will take the Claude path"
                )
                await self._emit_status(ServiceStatus.RUNNING, "JevIntentService disabled")
                return

            if not self._client.configured:
                self.logger.warning(
                    "TYPESAFE_API_KEY is not set; JevIntentService will stay inactive and all "
                    "turns will take the Claude path"
                )
                await self._emit_status(
                    ServiceStatus.RUNNING, "JevIntentService inactive (no API key)"
                )
                return

            await self._client.start()
            await self._setup_subscriptions()

            # Claiming the gate is what makes ClaudeService wait for our verdicts. Do it only
            # once we are genuinely able to produce them.
            GATE.register_router()

            self.logger.info(
                f"JevIntentService started (cheap-tier gate={self._threshold:.2f}, "
                f"command gate={self._command_threshold:.2f}, timeout={self._timeout_s:.2f}s, "
                f"{len(self._questions)} questions, "
                f"speculation={'on' if self._speculate_enabled else 'off'})"
            )
            await self._emit_status(ServiceStatus.RUNNING, "JevIntentService started")
        except Exception as exc:
            error = f"Failed to start JevIntentService: {exc}"
            self.logger.error(error, exc_info=True)
            await self._emit_status(ServiceStatus.ERROR, error)
            # Do not re-raise: a broken fast path must not stop the voice assistant booting.

    async def _stop(self) -> None:
        GATE.unregister_router()
        await self._client.close()
        self.logger.info(f"JevIntentService stopping. {self._stats_line()}")
        await self._emit_status(ServiceStatus.STOPPED, "JevIntentService stopped")

    def _stats_line(self) -> str:
        latencies: List[float] = self._stats["latency_ms"]
        dispatches: List[float] = self._stats["dispatch_ms"]

        def p50(values: List[float]) -> str:
            if not values:
                return "n/a"
            return f"{sorted(values)[len(values) // 2]:.0f}ms"

        return (
            f"classified={self._stats['classified']} executed={self._stats['executed']} "
            f"declined={self._stats['declined']} failed={self._stats['failed']} "
            f"speculative_calls={self._stats['speculative_calls']} "
            f"speculative_hits={self._stats['speculative_hits']} "
            f"api_p50={p50(latencies)} dispatch_p50={p50(dispatches)}"
        )

    async def _setup_subscriptions(self) -> None:
        subscriptions = [
            self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_transcript),
            self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_turn_started),
            self.subscribe(EventTopics.SYSTEM_MODE_CHANGED, self._handle_mode_changed),
        ]
        if self._speculate_enabled:
            subscriptions += [
                self.subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_partial_transcript),
                self.subscribe(EventTopics.TRANSCRIPTION_INTERIM, self._handle_interim_transcript),
            ]
        await asyncio.gather(*subscriptions)
        self.logger.info("JevIntentService subscribed to VOICE_LISTENING_STOPPED")

    # ---------------------------------------------------------------------------------
    # Turn bookkeeping and pre-warm
    # ---------------------------------------------------------------------------------

    async def _handle_turn_started(self, payload: Dict[str, Any]) -> None:
        """New utterance: bump the turn id and drop last turn's speculative answers."""
        self._turn_id += 1
        self._speculative.clear()
        self._last_speculated_text = ""

    async def _handle_mode_changed(self, payload: Dict[str, Any]) -> None:
        """Pre-warm the Jev connection on engage, the way Deepgram opens its socket.

        A cold TLS handshake costs ~100 ms of a ~190 ms budget, and that cost would otherwise
        land on the user's first command of the session.
        """
        mode = str((payload or {}).get("new_mode") or (payload or {}).get("mode") or "").upper()
        if mode.endswith("INTERACTIVE") or mode == "ENGAGED":
            asyncio.create_task(self._client.prewarm())

    # ---------------------------------------------------------------------------------
    # Speculation
    # ---------------------------------------------------------------------------------

    async def _handle_partial_transcript(self, payload: Dict[str, Any]) -> None:
        """A finalized STT segment, which arrives well before the click stops recording.

        For a one-sentence command this text is usually byte-identical to the transcript that
        ``VOICE_LISTENING_STOPPED`` will carry ~500 ms later, so this is the highest-value
        speculative trigger available.
        """
        await self._speculate((payload or {}).get("text", ""), debounce=False)

    async def _handle_interim_transcript(self, payload: Dict[str, Any]) -> None:
        await self._speculate((payload or {}).get("text", ""), debounce=True)

    async def _speculate(self, text: str, debounce: bool) -> None:
        """Classify a partial transcript in the background and cache the answer.

        Speculative results are never acted on directly — only a matching authoritative
        transcript can dispatch. That keeps the false-trigger properties of the measured
        design intact while moving the API latency off the critical path.
        """
        text = (text or "").strip()
        key = _normalize(text)
        if not key or len(key.split()) < _MIN_SPECULATIVE_WORDS:
            return
        if key in self._speculative or key == self._last_speculated_text:
            return

        now = time.monotonic()
        if debounce and (now - self._last_speculated_at) < _SPECULATION_DEBOUNCE_S:
            return

        self._last_speculated_at = now
        self._last_speculated_text = key
        asyncio.create_task(self._run_speculation(self._turn_id, text, key))

    async def _run_speculation(self, turn_id: int, text: str, key: str) -> None:
        try:
            self._stats["speculative_calls"] += 1
            result = await self._client.classify(
                jev_intents.build_state(text), self._questions
            )
            # Discard if the turn moved on while we were waiting.
            if result is None or turn_id != self._turn_id:
                return
            if len(self._speculative) >= _MAX_SPECULATIVE:
                self._speculative.pop(next(iter(self._speculative)))
            self._speculative[key] = (turn_id, result)
            self.logger.debug(
                f"Speculative Jev answer cached for '{text[:40]}' in {result.latency_ms:.0f} ms"
            )
        except Exception as exc:  # noqa: BLE001 — speculation is best-effort by definition
            self.logger.debug(f"Speculative Jev call failed (ignored): {exc}")

    # ---------------------------------------------------------------------------------
    # The hot path
    # ---------------------------------------------------------------------------------

    async def _handle_voice_transcript(self, payload: Dict[str, Any]) -> None:
        """Entry point: the authoritative transcript has landed and the clock is running."""
        transcript = (payload or {}).get("transcript", "") or ""
        if not transcript.strip():
            return

        conversation_id = (payload or {}).get("conversation_id")
        # Run detached so a slow classification cannot delay other subscribers of this event
        # (the eye controller and timeline executor are on it too).
        asyncio.create_task(self._classify_and_dispatch(transcript, conversation_id))

    async def _classify_and_dispatch(
        self, transcript: str, conversation_id: Optional[str]
    ) -> None:
        started = time.perf_counter()
        try:
            # Speculative hit? Then the API latency was already paid while the user was still
            # talking, and this dispatch costs microseconds.
            cached = self._speculative.get(_normalize(transcript))
            if cached is not None and cached[0] == self._turn_id:
                result: Optional[JevResult] = cached[1]
                self._stats["speculative_hits"] += 1
                self.logger.info(
                    f"Jev speculative cache hit for '{transcript[:48]}' "
                    "— classification already done"
                )
            else:
                result = await self._client.classify(
                    jev_intents.build_state(transcript), self._questions
                )

            if result is None:
                self._stats["failed"] += 1
                self.logger.info(
                    "Jev classification unavailable; handing turn to Claude "
                    f"({(time.perf_counter() - started) * 1000:.0f} ms elapsed)"
                )
                GATE.resolve(transcript, None)
                return

            self._stats["classified"] += 1
            self._stats["latency_ms"].append(result.latency_ms)

            decision = jev_intents.decide(
                result,
                transcript,
                threshold=self._threshold,
                command_threshold=self._command_threshold,
            )

            if not decision.should_execute or decision.intent is None:
                self._stats["declined"] += 1
                # The full probability map on every decline IS the eval set for sharpening
                # criteria wording later. Log it, at info, deliberately.
                self.logger.info(
                    f"Jev declined: {decision.reason} | utterance='{transcript}' | "
                    f"top2={decision.top_two} | is_a_command={decision.is_command:.2f} | "
                    f"probabilities={decision.probabilities}"
                )
                GATE.resolve(transcript, None)
                return

            # ---- Dispatch. This emit is the thing the whole service exists for. ----
            intent_name: str = decision.intent
            intent_payload = IntentPayload(
                intent_name=intent_name,
                parameters=decision.parameters,
                confidence=decision.confidence,
                original_text=transcript,
            )
            payload_dict = intent_payload.model_dump()
            if conversation_id:
                payload_dict["conversation_id"] = conversation_id
            # Marks provenance so IntentRouterService logs and the dashboard can tell a
            # fast-router dispatch from a Claude tool call.
            payload_dict["source"] = "jev_fast_router"

            await self.emit(EventTopics.INTENT_DETECTED, payload_dict)

            elapsed_ms = (time.perf_counter() - started) * 1000
            self._stats["executed"] += 1
            self._stats["dispatch_ms"].append(elapsed_ms)
            self.logger.info(
                f"⚡ Jev dispatched '{intent_name}' in {elapsed_ms:.1f} ms "
                f"({decision.tier} tier, choice={decision.choice_confidence:.2f}, "
                f"noul={decision.noul:.2f}) params={decision.parameters}"
            )

            # Record it *before* announcing it, so a ClaudeService already waiting on the gate
            # cannot wake up to a missing record.
            action = ActionTaken(
                intent_name=intent_name,
                parameters=decision.parameters,
                confidence=decision.confidence,
                transcript=transcript,
                at=time.time(),
            )
            GATE.resolve(transcript, action)

            await self.emit(
                EventTopics.INTENT_CONSUMED,
                {
                    "intent_name": intent_name,
                    "parameters": decision.parameters,
                    "confidence": decision.confidence,
                    "original_text": transcript,
                    "conversation_id": conversation_id,
                    "source": "jev_fast_router",
                    "tier": decision.tier,
                    "latency_ms": elapsed_ms,
                },
            )

        except Exception as exc:  # noqa: BLE001 — never let the fast path break the slow one
            self._stats["failed"] += 1
            self.logger.error(f"Jev fast router error: {exc}", exc_info=True)
            GATE.resolve(transcript, None)
