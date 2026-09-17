"""
Rendezvous between the fast intent router and ClaudeService for a single voice turn.

## The problem this solves

``JevIntentService`` and ``ClaudeService`` both subscribe to ``VOICE_LISTENING_STOPPED``, so
they wake on the same event, in an order pyee does not guarantee. The router needs ~190 ms to
reach a verdict. Without coordination, Claude would have its request in flight long before the
verdict lands, and two bad things would follow:

* Claude's own ``tool_use`` would dispatch the *same* action a second time — music restarting
  after it already started;
* Claude would not know an action had been taken, so it would narrate a future it was not
  responsible for.

So ClaudeService waits for the verdict before it builds its prompt. That costs the Claude path
~190 ms, which the brief explicitly permits ("the spoken confirmation may stay slow") and which
is noise next to its own ~1,400 ms round trip. Crucially it costs the *action* path nothing: the
music command is emitted by the router the moment the verdict lands, not after Claude replies.

## Why a module-level object rather than an event

The rendezvous needs a *value returned to a specific awaiting coroutine* within a bounded time.
Doing that over a fire-and-forget event bus means reimplementing futures on top of it —
correlation ids, a pending-waiters map, timeout sweeps. This is ~60 lines instead. The bus still
carries ``INTENT_CONSUMED`` for everything that wants to *observe* the decision (dashboard,
nervous system, debug logs); this gate exists only for the one consumer that must *block* on it.

## Fail-open by construction

* If no router ever registers, :attr:`enabled` is False and ClaudeService never waits at all.
* If the router registers but crashes, dies, or never resolves, :meth:`wait_for_verdict` returns
  ``None`` at its timeout and the Claude path proceeds exactly as it does today.
* The turn key is the transcript text, and the voice loop is strictly serial (one click, one
  utterance, one turn), so a single-slot-per-transcript store is sufficient.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: How long an ``ActionTaken`` record stays valid. Long enough to cover Claude's own round trip
#: plus its follow-up verbal call, short enough that a stale record can never leak into a later
#: turn.
RECORD_TTL_S = 30.0


@dataclass
class ActionTaken:
    """A record that the fast router already dispatched something for this turn."""

    intent_name: str
    parameters: Dict[str, Any]
    confidence: float
    transcript: str
    at: float


class FastRouterGate:
    """One-slot-per-turn rendezvous. Single event loop; not thread-safe."""

    def __init__(self) -> None:
        self._futures: Dict[str, "asyncio.Future[Optional[ActionTaken]]"] = {}
        self._records: Dict[str, ActionTaken] = {}
        self._router_registered = False

    # -- router side ---------------------------------------------------------------------

    def register_router(self) -> None:
        """Called by the fast router at startup. Until this runs, nobody waits on the gate."""
        self._router_registered = True
        logger.info("Fast router registered with gate; Claude path will await verdicts")

    def unregister_router(self) -> None:
        """Called on router shutdown. Releases anyone currently waiting."""
        self._router_registered = False
        for key in list(self._futures):
            self._resolve(key, None)
        logger.info("Fast router unregistered from gate")

    def resolve(self, transcript: str, action: Optional[ActionTaken]) -> None:
        """Publish the verdict for ``transcript``.

        ``action`` is the dispatched action, or ``None`` for "nothing fired, Claude owns this
        turn". Safe to call even if nobody is waiting yet — the value is stored for the waiter.
        """
        if action is not None:
            self._records[transcript] = action
            self._sweep()
        self._resolve(transcript, action)

    # -- Claude side ---------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._router_registered

    async def wait_for_verdict(
        self, transcript: str, timeout_s: float
    ) -> Optional[ActionTaken]:
        """Block up to ``timeout_s`` for the router's verdict on ``transcript``.

        Returns the dispatched action, or ``None`` if nothing fired, the router is absent, or
        the wait timed out. Never raises.
        """
        if not self._router_registered:
            return None

        # Already resolved before we got here — common, since the router may win the race.
        existing = self._records.get(transcript)
        if existing is not None:
            return existing

        future = self._futures.get(transcript)
        if future is None or future.done():
            future = asyncio.get_event_loop().create_future()
            self._futures[transcript] = future

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                f"Fast router verdict timed out after {timeout_s:.2f}s; "
                "proceeding on the Claude path"
            )
            return None
        except Exception as exc:  # noqa: BLE001 — the gate must never break the voice loop
            logger.warning(f"Fast router gate error: {exc}; proceeding on the Claude path")
            return None
        finally:
            self._futures.pop(transcript, None)

    def consume(self, transcript: str) -> Optional[ActionTaken]:
        """Take and remove the record for ``transcript``, if one is live.

        Used by ClaudeService once it has folded the action into its prompt, so a retry of the
        same turn cannot double-suppress.
        """
        self._sweep()
        return self._records.pop(transcript, None)

    def peek(self, transcript: str) -> Optional[ActionTaken]:
        self._sweep()
        return self._records.get(transcript)

    # -- internals -----------------------------------------------------------------------

    def _resolve(self, key: str, value: Optional[ActionTaken]) -> None:
        future = self._futures.get(key)
        if future is not None and not future.done():
            future.set_result(value)

    def _sweep(self) -> None:
        cutoff = time.time() - RECORD_TTL_S
        for key, record in list(self._records.items()):
            if record.at < cutoff:
                del self._records[key]

    def reset(self) -> None:
        """Drop all state. For tests."""
        self._futures.clear()
        self._records.clear()
        self._router_registered = False


#: Process-wide gate. CantinaOS runs all services in one process on one event loop, so a module
#: singleton is the same lifetime as the services that use it.
GATE = FastRouterGate()
