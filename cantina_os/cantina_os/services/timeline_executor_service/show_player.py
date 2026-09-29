"""The show player: TimelineExecutorService's clock-driven runner for clips, cues and sequences.

``show/SPEC.md`` is the contract. The service owns one :class:`ShowPlayer` and feeds it bus
events; the player emits the department events (``show.motion``, ``EYE_COMMAND``,
``chest.override``, ``stage.lights``, ``show.sfx``, TTS requests, ducking) and the run
lifecycle (``show.started`` / ``show.ended``).

Clock rules
-----------
Every item is scheduled against a :class:`Clock` anchored on the monotonic clock when its
sequence starts: ``time(pos) = anchor_t + (pos - anchor_pos) / rate``. Nothing ever sleeps
"the gap since the previous item", so slow handlers or a busy loop cannot accumulate drift.

* ``clock: time`` has rate 1 (units are seconds). ``clock: beat`` has rate ``bpm / 60`` and
  **chases the live tempo**: when the music's bpm changes the clock re-anchors at its current
  beat, so beats already played stay where they were and later ones move.
* ``wait for speech_end`` **pauses every clock in the run**; on resume each re-anchors at the
  position it froze at, so every later item - in the parent and in nested sequences - slides
  by the wait's real duration.
* Cue action offsets are seconds (a cue has no clock of its own).

Runs and layers
---------------
A sequence runs on its ``layer`` (``show`` or ``gesture``); a clip or cue performed on its
own runs on ``gesture``. A new run on a layer ends the one there (``show.ended
reason=interrupted``; the body blends that run's clips out). While a clip is inside its
``interruptible_after`` window the new request queues until the window ends. ``show.stop``
ends runs by id / layer / all, immediately. ``motion.freeze {on:true}`` ends everything and
refuses new requests until ``{on:false}`` - safety beats everything.

Tiers are enforced by ``source`` before anything is emitted: a violation emits only
``show.ended reason=rejected``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from ...core.event_payloads import (
    ChestOverridePayload,
    ShowMotionPayload,
    ShowParams,
    ShowRunPayload,
    ShowSfxPayload,
    StageLightsPayload,
)
from ...core.event_topics import EventTopics
from ...event_payloads import EyeCommandPayload, SpeechGenerationRequestPayload
from ...show.loader import ShowLibrary, ShowLibraryHandle
from ...show.models import (
    INTENSITY_RANGE,
    MAX_NESTING,
    SPEED_RANGE,
    TIER_RANK,
    Action,
    Clip,
    Cue,
    Sequence,
    clamp,
)

#: The highest tier each source may trigger (show/SPEC.md tier table). Permission is
#: monotone: a source allowed ``show`` may also trigger ``cheap`` and ``free``.
SOURCE_MAX_TIER: Dict[str, str] = {
    "jev": "cheap",  # never show-tier; the free/cheap gate split is Jev's own business
    "idle": "free",
    "claude": "show",  # tags are limited to free/cheap by ClaudeService; the tool may run show
    "timeline": "show",
    "ui": "show",
    "cli": "show",
}

#: Sources whose shows never speak: the reply that triggered them already is the voice.
VOICELESS_SOURCES = frozenset({"claude"})

#: How long a ``wait`` for someone else's speech may hold a run.
FOREIGN_SPEECH_WAIT_S = 30.0
#: How long a ``wait`` holds for the run's own line to start when nothing at all is speaking
#: (TTS down, request dropped). Mirrors the sim's ``waitGraceS`` (player.ts). While other
#: speech is playing the line is simply queued behind it (ElevenLabs is one FIFO), so that
#: time does not count; FOREIGN_SPEECH_WAIT_S bounds it instead.
NO_SPEECH_GRACE_S = 1.5


def tier_allowed(source: str, tier: str) -> bool:
    top = SOURCE_MAX_TIER.get(source)
    return top is not None and TIER_RANK[tier] <= TIER_RANK[top]


class Clock:
    """A pausable, re-anchorable position clock on top of a monotonic time source."""

    def __init__(self, now: Callable[[], float], rate: float, paused: bool = False,
                 beat_bpm: Optional[float] = None):
        self._now = now
        self.rate = rate
        #: The sequence's own bpm when this is a beat clock (fallback tempo), else None.
        self.beat_bpm = beat_bpm
        self.anchor_t = now()
        self.anchor_pos = 0.0
        self.paused_pos: Optional[float] = 0.0 if paused else None
        self._changed = asyncio.Event()

    @property
    def paused(self) -> bool:
        return self.paused_pos is not None

    def pos(self, t: Optional[float] = None) -> float:
        if self.paused_pos is not None:
            return self.paused_pos
        t = self._now() if t is None else t
        return self.anchor_pos + (t - self.anchor_t) * self.rate

    def time_for(self, pos: float) -> Optional[float]:
        """Monotonic time at which ``pos`` is reached, or None while paused."""
        if self.paused_pos is not None:
            return None
        return self.anchor_t + (pos - self.anchor_pos) / self.rate

    def set_rate(self, rate: float) -> None:
        if rate == self.rate:
            return
        if self.paused_pos is None:
            now = self._now()
            self.anchor_pos, self.anchor_t = self.pos(now), now
        self.rate = rate
        self._poke()

    def pause(self) -> None:
        if self.paused_pos is None:
            self.paused_pos = self.pos()
            self._poke()

    def resume(self) -> None:
        if self.paused_pos is not None:
            self.anchor_pos, self.anchor_t = self.paused_pos, self._now()
            self.paused_pos = None
            self._poke()

    def _poke(self) -> None:
        ev, self._changed = self._changed, asyncio.Event()
        ev.set()

    async def wait_until(self, pos: float) -> None:
        """Sleep until the clock reaches ``pos``, re-planning on any tempo change or pause."""
        while True:
            changed = self._changed
            target = self.time_for(pos)
            if target is None:
                await changed.wait()
                continue
            delay = target - self._now()
            if delay <= 0:
                return
            try:
                await asyncio.wait_for(changed.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


@dataclass
class ShowRun:
    run_id: str
    item: Any
    source: str
    layer: str
    params: ShowParams
    library: ShowLibrary
    conversation_id: Optional[str] = None
    task: Optional[asyncio.Task] = None
    children: Set[asyncio.Task] = field(default_factory=set)
    clocks: List[Clock] = field(default_factory=list)
    pause_depth: int = 0
    #: speech_id -> (completion event, text)
    speeches: Dict[str, tuple] = field(default_factory=dict)
    motion_until: float = 0.0
    locked_until: float = 0.0
    started: bool = False
    ended: bool = False
    reason: Optional[str] = None
    finished: Optional[asyncio.Future] = None

    @property
    def id(self) -> str:
        return self.item.id if self.item is not None else "?"

    @property
    def kind(self) -> str:
        return getattr(self.item, "kind", "unknown")


class ShowPlayer:
    def __init__(
        self,
        library: ShowLibraryHandle,
        emit: Callable[[str, dict], Any],
        *,
        logger: Optional[logging.Logger] = None,
        speech_budget: Callable[[str], float] = lambda text: 8.0 + 0.06 * len(text or ""),
        ducking_level: float = 0.5,
        ducking_fade_ms: int = 500,
        on_duck: Optional[Callable[[bool], None]] = None,
        now: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ):
        self.library = library
        self._emit_raw = emit
        self.log = logger or logging.getLogger("cantina_os.show.player")
        self._speech_budget = speech_budget
        self._ducking_level = ducking_level
        self._ducking_fade_ms = ducking_fade_ms
        self._on_duck = on_duck
        self._now = now
        self._wall = wall

        self.frozen = False
        #: Why the most recent perform() was refused (for CLI / plan-step feedback).
        self.last_refusal: Optional[str] = None
        self.live_bpm: Optional[float] = None
        self._runs: Dict[str, ShowRun] = {}  # every run not yet ended (active or queued)
        self._active: Dict[str, ShowRun] = {}  # layer -> the run playing on it
        self._queued: Dict[str, ShowRun] = {}  # layer -> the run waiting for a clip window
        self._speech_owner: Dict[str, ShowRun] = {}
        #: speech id -> monotonic start time, for every speech in progress (anyone's)
        self._active_speech: Dict[str, float] = {}
        self._speech_idle = asyncio.Event()
        self._speech_idle.set()
        #: Set (and replaced) on every speech start/complete, so a waiter can sleep until
        #: "something about speech changed" without polling.
        self._speech_changed = asyncio.Event()

    # ================================================================== public API
    @property
    def runs(self) -> List[ShowRun]:
        return list(self._runs.values())

    def perform(self, item_id: str, source: str, params: Optional[Any] = None,
                conversation_id: Optional[str] = None, expect_kind: Optional[str] = None,
                optional: bool = False) -> Optional[ShowRun]:
        """Start (or queue) a run. Returns None when the request is refused."""
        lib = self.library.library
        item = lib.get(item_id)
        run_params = self._params(params)

        refusal = None
        if self.frozen:
            refusal = "motion is frozen"
        elif item is None:
            refusal = "unknown show item"
        elif expect_kind and item.kind != expect_kind:
            refusal = f"is a {item.kind}, not a {expect_kind}"
        elif not lib.is_valid(item_id):
            refusal = "invalid: " + "; ".join(i.message for i in lib.errors_for(item_id))
        elif source not in SOURCE_MAX_TIER:
            refusal = f"unknown source {source!r}"
        elif not tier_allowed(source, item.tier):
            refusal = f"tier {item.tier!r} may not be triggered by {source!r}"
        self.last_refusal = refusal
        if refusal:
            quiet = optional and item is None
            (self.log.info if quiet else self.log.warning)(f"show.perform {item_id!r} from {source}: refused ({refusal})")
            self._emit(EventTopics.SHOW_ENDED, ShowRunPayload(
                id=item_id, kind=getattr(item, "kind", "unknown"), source=source,
                run_id=str(uuid.uuid4()), reason="rejected", conversation_id=conversation_id))
            return None

        layer = item.layer if isinstance(item, Sequence) else "gesture"
        run = ShowRun(run_id=str(uuid.uuid4()), item=item, source=source, layer=layer,
                      params=run_params, library=lib, conversation_id=conversation_id)
        run.finished = asyncio.get_running_loop().create_future()
        self._runs[run.run_id] = run

        superseded = self._queued.pop(layer, None)
        if superseded is not None:
            self._finish(superseded, "interrupted")
        current = self._active.get(layer)
        delay = 0.0
        if current is not None:
            delay = max(0.0, current.locked_until - self._now())
        if delay > 0:
            self._queued[layer] = run
            self.log.info(f"show {item_id!r} queued {delay:.2f}s behind {current.id!r} on {layer}")
        run.task = asyncio.create_task(self._main(run, delay), name=f"show:{item_id}")
        return run

    async def wait(self, run: Optional[ShowRun], timeout: Optional[float] = None) -> str:
        """Wait for a run to end; returns its reason ('rejected' for a refused request)."""
        if run is None:
            return "rejected"
        try:
            return await asyncio.wait_for(asyncio.shield(run.finished), timeout)
        except asyncio.TimeoutError:
            return "timeout"

    def stop(self, item_id: Optional[str] = None, layer: Optional[str] = None,
             all_: Optional[bool] = None, source: Optional[str] = None) -> int:
        """End matching runs (and queued requests). No criteria at all means everything."""
        everything = all_ or not (item_id or layer or source)
        victims = [r for r in self._runs.values() if everything
                   or (item_id and r.id == item_id)
                   or (layer and r.layer == layer)
                   or (source and r.source == source)]
        for r in victims:
            self._finish(r, "interrupted")
        return len(victims)

    def set_frozen(self, on: bool) -> None:
        if on == self.frozen:
            return
        self.frozen = on
        if on:
            n = self.stop(all_=True)
            self.log.warning(f"MOTION FREEZE: stopped {n} show run(s); refusing new ones until unfrozen")
        else:
            self.log.info("Motion freeze released")

    def set_live_bpm(self, bpm: Optional[float]) -> None:
        bpm = float(bpm) if bpm else None
        if bpm == self.live_bpm:
            return
        self.live_bpm = bpm
        for run in self._runs.values():
            for clock in run.clocks:
                if clock.beat_bpm is not None:
                    clock.set_rate((bpm or clock.beat_bpm) / 60.0)

    # -- speech bookkeeping, fed by the service's SPEECH_GENERATION_* handlers
    def _notify_speech_changed(self) -> None:
        changed, self._speech_changed = self._speech_changed, asyncio.Event()
        changed.set()

    def on_speech_started(self, payload: dict) -> None:
        # clip_id first: a show line has no conversation_id (see _speak), and a reply's clip_id
        # equals its conversation_id. on_speech_complete pops every key, so either matches.
        sid = (payload or {}).get("clip_id") or (payload or {}).get("conversation_id")
        if sid:
            self._notify_speech_changed()
            now = self._now()
            # A completion that never arrived must not hold every later wait forever.
            for stale in [k for k, t in self._active_speech.items() if now - t > 120.0]:
                del self._active_speech[stale]
            self._active_speech[sid] = now
            self._speech_idle.clear()

    def on_speech_complete(self, payload: dict) -> bool:
        """Returns True when the completion belonged to a show ``speak``."""
        p = payload or {}
        for key in ("clip_id", "step_id", "conversation_id"):
            self._active_speech.pop(p.get(key), None)
        if not self._active_speech:
            self._speech_idle.set()
        self._notify_speech_changed()
        sid = p.get("clip_id") or p.get("step_id")
        run = self._speech_owner.pop(sid, None) if sid else None
        if run is None:
            return False
        entry = run.speeches.get(sid)
        if entry:
            entry[0].set()
        return True

    def is_show_speech(self, speech_id: Optional[str]) -> bool:
        return bool(speech_id) and speech_id in self._speech_owner

    async def shutdown(self) -> None:
        tasks = [r.task for r in self._runs.values() if r.task]
        self.stop(all_=True)
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)

    # ================================================================== run lifecycle
    def _params(self, params: Any) -> ShowParams:
        if isinstance(params, ShowParams):
            return params
        p = dict(params or {})
        return ShowParams(
            intensity=clamp(p.get("intensity"), INTENSITY_RANGE),
            speed=clamp(p.get("speed"), SPEED_RANGE),
        )

    async def _main(self, run: ShowRun, delay: float) -> None:
        reason = "done"
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if self._queued.get(run.layer) is run:
                del self._queued[run.layer]
            current = self._active.get(run.layer)
            if current is not None and current is not run:
                self._finish(current, "interrupted")
            self._active[run.layer] = run
            run.started = True
            self._emit(EventTopics.SHOW_STARTED, self._run_payload(run))
            self.log.info(f"show {run.id!r} ({run.kind}, {run.source}) started on {run.layer} [{run.run_id[:8]}]")

            item = run.item
            if isinstance(item, Clip):
                self._fire_clip(run, item.id, None, None, None)
            elif isinstance(item, Cue):
                await self._run_cue(run, item, None)
            elif isinstance(item, Sequence):
                await self._run_sequence(run, item, 0, item.owns)
            await self._join_children(run)
            remaining = run.motion_until - self._now()
            if remaining > 0:
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            reason = run.reason or "interrupted"
        except Exception as e:
            self.log.error(f"show {run.id!r} failed: {e}", exc_info=True)
            reason = "interrupted"
        finally:
            self._finish(run, reason, cancel_self=False)

    def _finish(self, run: ShowRun, reason: str, cancel_self: bool = True) -> None:
        """End a run exactly once: emit show.ended, cancel its tasks, release its layer."""
        if run.ended:
            return
        run.ended = True
        run.reason = reason
        for t in list(run.children):
            t.cancel()
        if cancel_self and run.task and not run.task.done() and run.task is not asyncio.current_task():
            run.task.cancel()
        for sid in list(run.speeches):
            self._speech_owner.pop(sid, None)
        if self._active.get(run.layer) is run:
            del self._active[run.layer]
        if self._queued.get(run.layer) is run:
            del self._queued[run.layer]
        self._runs.pop(run.run_id, None)
        # A request that never started still gets its ended, so waiters are released.
        self._emit(EventTopics.SHOW_ENDED, self._run_payload(run, reason))
        self.log.info(f"show {run.id!r} ended: {reason} [{run.run_id[:8]}]")
        if run.finished and not run.finished.done():
            run.finished.set_result(reason)

    def _run_payload(self, run: ShowRun, reason: Optional[str] = None) -> ShowRunPayload:
        return ShowRunPayload(id=run.id, kind=run.kind, source=run.source, run_id=run.run_id,
                              reason=reason, conversation_id=run.conversation_id)

    def _spawn(self, run: ShowRun, coro) -> None:
        async def guarded():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.error(f"show {run.id!r}: nested element failed: {e}", exc_info=True)

        task = asyncio.create_task(guarded())
        run.children.add(task)
        task.add_done_callback(run.children.discard)

    async def _join_children(self, run: ShowRun) -> None:
        while True:
            pending = [t for t in run.children if not t.done()]
            if not pending:
                return
            await asyncio.wait(pending)

    # ================================================================== clocks
    def _new_clock(self, run: ShowRun, seq: Optional[Sequence]) -> Clock:
        if seq is not None and seq.clock == "beat":
            clock = Clock(self._now, (self.live_bpm or seq.bpm) / 60.0, paused=run.pause_depth > 0,
                          beat_bpm=seq.bpm)
        else:
            clock = Clock(self._now, 1.0, paused=run.pause_depth > 0)
        run.clocks.append(clock)
        return clock

    def _drop_clock(self, run: ShowRun, clock: Clock) -> None:
        if clock in run.clocks:
            run.clocks.remove(clock)

    def _pause(self, run: ShowRun) -> None:
        run.pause_depth += 1
        if run.pause_depth == 1:
            for c in run.clocks:
                c.pause()

    def _resume(self, run: ShowRun) -> None:
        run.pause_depth = max(0, run.pause_depth - 1)
        if run.pause_depth == 0:
            for c in run.clocks:
                c.resume()

    # ================================================================== elements
    async def _run_cue(self, run: ShowRun, cue: Cue, owns: Optional[List[str]]) -> None:
        clock = self._new_clock(run, None)
        try:
            for _, action in sorted(enumerate(cue.actions), key=lambda e: (e[1].at, e[0])):
                await clock.wait_until(action.at)
                await self._fire_action(run, action, owns)
        finally:
            self._drop_clock(run, clock)

    async def _run_sequence(self, run: ShowRun, seq: Sequence, depth: int,
                            owns: Optional[List[str]]) -> None:
        clock = self._new_clock(run, seq)
        items = sorted(enumerate(seq.track), key=lambda e: (e[1].at, e[0]))
        lib = run.library
        iteration = 0
        try:
            while True:
                base = iteration * (seq.length or 0.0) if seq.loop else 0.0
                for _, item in items:
                    await clock.wait_until(base + item.at)
                    kind = item.ref_kind
                    if kind == "cue":
                        cue = lib.get(item.cue)
                        if isinstance(cue, Cue):
                            self._spawn(run, self._run_cue(run, cue, owns))
                    elif kind == "clip":
                        self._fire_clip(run, item.clip, item.intensity, item.speed, owns)
                    elif kind == "sequence":
                        nested = lib.get(item.sequence)
                        if depth + 1 > MAX_NESTING:
                            self.log.warning(f"show {seq.id!r}: {item.sequence!r} nests deeper than {MAX_NESTING}; skipped")
                        elif isinstance(nested, Sequence):
                            self._spawn(run, self._run_sequence(run, nested, depth + 1, nested.owns or owns))
                    else:
                        await self._fire_action(run, item, owns)
                if not seq.loop:
                    return
                iteration += 1
        finally:
            self._drop_clock(run, clock)

    def _fire_clip(self, run: ShowRun, clip_id: str, intensity: Optional[float],
                   speed: Optional[float], owns: Optional[List[str]]) -> None:
        clip = run.library.get(clip_id)
        if not isinstance(clip, Clip):
            self.log.warning(f"show {run.id!r}: clip {clip_id!r} not found; skipped")
            return
        i = clamp((1.0 if intensity is None else intensity) * run.params.intensity, INTENSITY_RANGE)
        s = clamp((1.0 if speed is None else speed) * run.params.speed, SPEED_RANGE)
        now = self._now()
        self._emit(EventTopics.SHOW_MOTION, ShowMotionPayload(
            run_id=run.run_id, clip=clip_id, intensity=i, speed=s, start_at=self._wall(),
            layer=run.layer, owns=owns))
        run.motion_until = max(run.motion_until, now + clip.duration / s)
        run.locked_until = max(run.locked_until, now + clip.interruptible_after / s)

    async def _fire_action(self, run: ShowRun, a: Action, owns: Optional[List[str]]) -> None:
        do = a.do
        if do == "clip":
            self._fire_clip(run, a.id, a.intensity, a.speed, owns)
        elif do == "eyes":
            self._emit(EventTopics.EYE_COMMAND, EyeCommandPayload(
                pattern=a.pattern, color=a.color, intensity=a.intensity, duration=a.duration,
                conversation_id=run.conversation_id))
        elif do == "chest":
            self._emit(EventTopics.CHEST_OVERRIDE, ChestOverridePayload(command=a.command, hold=a.hold or 0.0))
        elif do == "lights":
            self._emit(EventTopics.STAGE_LIGHTS, StageLightsPayload(
                cue=a.cue, mode=a.mode, fade=a.fade or 0.0, hold=a.hold or 0.0, rig=a.rig))
        elif do == "sfx":
            self._emit(EventTopics.SHOW_SFX, ShowSfxPayload(id=a.id))
        elif do == "speak":
            if run.source in VOICELESS_SOURCES:
                # Claude's own reply is this turn's voice. A show line queued beside it made
                # R3X say two unrelated things back to back ("Make some noise!" then the
                # reply, 2026-09-29), so a Claude-started show performs silently.
                self.log.info(f"show {run.id!r} ({run.source}): skipped speak {a.text!r}; the reply is the voice")
            else:
                self._speak(run, a.text)
        elif do == "duck":
            self._emit_dict(EventTopics.AUDIO_DUCKING_START,
                            {"level": self._ducking_level, "fade_ms": self._ducking_fade_ms})
            if self._on_duck:
                self._on_duck(True)
        elif do == "unduck":
            self._emit_dict(EventTopics.AUDIO_DUCKING_STOP, {"fade_ms": self._ducking_fade_ms})
            if self._on_duck:
                self._on_duck(False)
        elif do == "wait":
            await self._wait_for_speech_end(run)

    def _speak(self, run: ShowRun, text: str) -> None:
        # The existing path that makes ElevenLabs speak a line: TTS_GENERATE_REQUEST, keyed by
        # clip_id; SPEECH_GENERATION_COMPLETE echoes clip_id back. conversation_id stays None
        # (as for plan speak steps) so a show line can never be mistaken for the voice turn's
        # reply by the latency tracker, the CLI or the tag scheduler.
        speech_id = f"show-{run.run_id[:8]}-{uuid.uuid4().hex[:6]}"
        run.speeches[speech_id] = (asyncio.Event(), text)
        self._speech_owner[speech_id] = run
        self._emit(EventTopics.TTS_GENERATE_REQUEST, SpeechGenerationRequestPayload(
            text=text, clip_id=speech_id, step_id=speech_id, plan_id=run.run_id))

    async def _wait_for_speech_end(self, run: ShowRun) -> None:
        mine = [(ev, text) for ev, text in run.speeches.values() if not ev.is_set()]
        if not mine and self._speech_idle.is_set():
            return
        self._pause(run)
        started = self._now()
        try:
            if mine:
                # Hard ceiling: queue time behind other speech, plus every own line's budget.
                ceiling = FOREIGN_SPEECH_WAIT_S + sum(self._speech_budget(text) for _, text in mine)
                await asyncio.wait_for(self._hold_for_own_lines(run), ceiling)
            else:
                await asyncio.wait_for(self._speech_idle.wait(), FOREIGN_SPEECH_WAIT_S)
        except asyncio.TimeoutError:
            self.log.warning(f"show {run.id!r}: speech_end wait timed out; continuing")
        finally:
            self._resume(run)
            self.log.debug(f"show {run.id!r}: waited {self._now() - started:.2f}s for speech_end")

    async def _hold_for_own_lines(self, run: ShowRun) -> None:
        """Until every ``speak`` line of this run has finished, in three states per step:

        * a line is **playing** (its clip_id is in ``_active_speech``): wait for it, bounded by
          its own text budget measured from now;
        * other speech is playing and the line is **queued** behind it (a show line after
          Claude's reply in ElevenLabs' FIFO): wait for the next speech change - the caller's
          ceiling bounds this;
        * **nothing** is speaking and the line has not started: give it NO_SPEECH_GRACE_S to
          start, then stop waiting. This is what makes a dropped request or a TTS outage cost
          1.5 s instead of the whole budget - and what rules out a deadlock.
        """
        while True:
            pending = [(sid, ev, text) for sid, (ev, text) in run.speeches.items() if not ev.is_set()]
            if not pending:
                return
            playing = [(sid, ev, text) for sid, ev, text in pending if sid in self._active_speech]
            if playing:
                sid, ev, text = playing[0]
                try:
                    await asyncio.wait_for(ev.wait(), self._speech_budget(text))
                except asyncio.TimeoutError:
                    self.log.warning(f"show {run.id!r}: line {sid} overran its budget; continuing")
                    ev.set()
                continue
            changed = self._speech_changed
            if self._active_speech:
                await changed.wait()  # queued behind someone else's speech
                continue
            try:
                await asyncio.wait_for(changed.wait(), NO_SPEECH_GRACE_S)
            except asyncio.TimeoutError:
                self.log.warning(
                    f"show {run.id!r}: {len(pending)} line(s) never started within "
                    f"{NO_SPEECH_GRACE_S}s (TTS unavailable?); continuing"
                )
                for _, ev, _ in pending:
                    ev.set()
                return

    # ================================================================== emit
    def _emit(self, topic: EventTopics, payload) -> None:
        self._emit_raw(topic.value, payload.model_dump())

    def _emit_dict(self, topic: EventTopics, payload: dict) -> None:
        self._emit_raw(topic.value, payload)
