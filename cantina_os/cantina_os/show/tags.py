"""Inline performance tags in Claude's replies: ``{cue:<id>}`` and ``{clip:<id>}``.

Flow (see CLAUDE.md "Show system"):

1. ClaudeService feeds every streamed chunk through a :class:`TagParser`. The parser holds
   back an unclosed ``{`` across chunks, strips complete tags, and only ever releases clean
   text - so ``LLM_RESPONSE`` chunks, the CLI and TTS never see a tag.
2. The complete reply is parsed once more (same algorithm, so identical offsets) to get
   each tag's character offset into the clean text; the tags are registered with the
   :class:`SpeechTagScheduler` under the turn's ``conversation_id``.
3. On ``SPEECH_GENERATION_STARTED`` (emitted at the first audible sample) the scheduler
   arms each tag at an estimated time; each ``speech.alignment`` chunk then moves the tags
   it covers onto their character's real start. ``show.perform {source: "claude"}`` fires
   when the word is heard.

The conversion is a :class:`TagTimer`. Today it is :class:`LinearCharTimer`
(``char_offset / chars_per_sec``) because ElevenLabs is called without timestamps. The seam
for precise timing is :func:`timer_for_speech`: when the speech-started payload carries an
``alignment`` block (ElevenLabs ``stream_with_timestamps`` format:
``characters`` + ``character_start_times_seconds``), :class:`AlignmentTimer` is used instead
and nothing else has to change.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Collection, Dict, List, Optional, Tuple

logger = logging.getLogger("cantina_os.show.tags")

TAG_RE = re.compile(r"^\{\s*(cue|clip)\s*:\s*([a-z][a-z0-9_]*)\s*\}$")
#: A held-back ``{`` longer than this without a ``}`` is plain text, not a tag.
MAX_TAG_LEN = 64
MAX_TAGS_PER_REPLY = 2


@dataclass
class Tag:
    kind: str  # "cue" | "clip"
    id: str
    offset: int  # character offset into the clean text


@dataclass
class TagParser:
    """Incremental, chunking-invariant tag stripper.

    ``valid`` is the set of ``(kind, id)`` pairs that may be performed; anything else in
    braces is dropped from the text (braces are never meant to be spoken) and reported in
    ``dropped``. At most ``max_tags`` tags are kept per reply; extras are dropped.
    """

    valid: Optional[Collection[Tuple[str, str]]] = None
    max_tags: int = MAX_TAGS_PER_REPLY
    tags: List[Tag] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    _held: str = ""
    _clean_len: int = 0
    _last_char: str = ""
    _skip_space: bool = False

    def feed(self, text: str) -> str:
        out: List[str] = []
        for ch in text:
            if self._held:
                self._held += ch
                if ch == "}":
                    self._close_tag()
                elif ch == "{" or ch == "\n" or len(self._held) > MAX_TAG_LEN:
                    # Not a tag after all: release what was held (a fresh "{" re-opens).
                    held, self._held = self._held, ""
                    if ch == "{":
                        self._emit(held[:-1], out)
                        self._held = "{"
                    else:
                        self._emit(held, out)
                continue
            if ch == "{":
                self._held = "{"
                continue
            self._emit(ch, out)
        return "".join(out)

    def flush(self) -> str:
        """End of reply. An unclosed ``{...`` that could still have been a tag is dropped."""
        held, self._held = self._held, ""
        if not held:
            return ""
        if re.match(r"^\{\s*(c|cu|cue|cl|cli|clip)?\s*(:\s*[a-z0-9_]*)?\s*$", held):
            self.dropped.append(held)
            return ""
        out: List[str] = []
        self._emit(held, out)
        return "".join(out)

    @property
    def clean_length(self) -> int:
        return self._clean_len

    # ------------------------------------------------------------------ internals
    def _emit(self, text: str, out: List[str]) -> None:
        for ch in text:
            if self._skip_space and ch in " \t":
                continue
            self._skip_space = False
            out.append(ch)
            self._clean_len += 1
            self._last_char = ch

    def _close_tag(self) -> None:
        held, self._held = self._held, ""
        m = TAG_RE.match(held)
        if not m:
            self.dropped.append(held)
        else:
            kind, item_id = m.group(1), m.group(2)
            if self.valid is not None and (kind, item_id) not in self.valid:
                self.dropped.append(held)
            elif len(self.tags) >= self.max_tags:
                self.dropped.append(held)
            else:
                self.tags.append(Tag(kind, item_id, self._clean_len))
        # Removing a tag from "word {tag} word" must not leave a double space.
        if self._clean_len == 0 or self._last_char in (" ", "\t", "\n"):
            self._skip_space = True


def extract_tags(text: str, valid=None, max_tags: int = MAX_TAGS_PER_REPLY) -> Tuple[str, List[Tag], List[str]]:
    """Whole-text form of :class:`TagParser`: ``(clean_text, tags, dropped)``."""
    p = TagParser(valid=valid, max_tags=max_tags)
    clean = p.feed(text) + p.flush()
    return clean, p.tags, p.dropped


# ---------------------------------------------------------------------------- timing


class TagTimer:
    """Maps a character offset in the spoken text to seconds after speech start."""

    def seconds_at(self, offset: int) -> float:  # pragma: no cover - interface
        raise NotImplementedError


class LinearCharTimer(TagTimer):
    def __init__(self, chars_per_sec: float = 19.0):
        self.chars_per_sec = max(1e-3, float(chars_per_sec))

    def seconds_at(self, offset: int) -> float:
        return max(0, offset) / self.chars_per_sec


class AlignmentTimer(TagTimer):
    """Per-character start times, as ElevenLabs' with-timestamps endpoints return them."""

    def __init__(self, starts: List[float]):
        self.starts = starts

    def seconds_at(self, offset: int) -> float:
        if not self.starts:
            return 0.0
        return float(self.starts[max(0, min(offset, len(self.starts) - 1))])


def timer_for_speech(payload: dict, chars_per_sec: float) -> TagTimer:
    """THE SEAM for precise timing: use real alignment when the speech event carries it."""
    alignment = (payload or {}).get("alignment")
    if isinstance(alignment, dict):
        starts = alignment.get("character_start_times_seconds")
        if isinstance(starts, list) and starts:
            return AlignmentTimer([float(s) for s in starts])
    return LinearCharTimer(chars_per_sec)


@dataclass
class _Pending:
    clean_text: str
    tags: List[Tag]
    created: float


@dataclass
class _Scheduled:
    """One tag of a reply that is being spoken: its fallback timer until alignment refines it."""
    tag: Tag
    index: int  # character index into the text ElevenLabs is speaking
    handle: Optional[asyncio.TimerHandle] = None
    fired: bool = False
    refined: bool = False


class SpeechTagScheduler:
    """Holds a turn's tags until its speech starts, then fires them on a timer.

    ``perform(item_id, conversation_id)`` is called (on the event loop) at each tag's time;
    ClaudeService passes a function that emits ``show.perform {source: "claude"}``.
    """

    def __init__(self, perform: Callable[[str, Optional[str]], None], chars_per_sec: float = 19.0,
                 ttl_s: float = 120.0, log: Optional[logging.Logger] = None):
        self._perform = perform
        self.chars_per_sec = float(chars_per_sec)
        self._ttl = ttl_s
        self._pending: Dict[str, _Pending] = {}
        #: Replies being spoken: their tags, and how many aligned characters have arrived.
        self._active: Dict[str, Tuple[List[_Scheduled], List[int]]] = {}
        self._log = log or logger

    def register(self, conversation_id: Optional[str], clean_text: str, tags: List[Tag]) -> None:
        if not conversation_id or not tags:
            return
        now = time.monotonic()
        for cid in [c for c, p in self._pending.items() if now - p.created > self._ttl]:
            self._pending.pop(cid, None)
        self._pending[conversation_id] = _Pending(clean_text, list(tags), now)

    def has_pending(self, conversation_id: str) -> bool:
        return conversation_id in self._pending

    def on_speech_started(self, payload: dict) -> List[Tuple[str, float]]:
        """Schedule the tags for this speech. Returns ``[(item_id, delay_s)]``.

        ElevenLabsService emits speech-started at the first audible sample, so the fallback
        estimate is measured from real sound. :meth:`on_alignment` then moves each tag onto
        the actual start of its character as the timing arrives.
        """
        cid = (payload or {}).get("conversation_id")
        pending = self._pending.pop(cid, None) if cid else None
        if pending is None:
            return []
        # ElevenLabs speaks the reply .strip()ped; shift offsets by what was stripped.
        lead = len(pending.clean_text) - len(pending.clean_text.lstrip())
        timer = timer_for_speech(payload, self.chars_per_sec)
        loop = asyncio.get_running_loop()
        scheduled: List[_Scheduled] = []
        report = []
        for tag in pending.tags:
            item = _Scheduled(tag, max(0, tag.offset - lead))
            delay = timer.seconds_at(item.index)
            item.handle = loop.call_later(delay, self._fire_scheduled, item, cid)
            scheduled.append(item)
            report.append((tag.id, delay))
        self._active[cid] = (scheduled, [0])
        self._log.info(f"Scheduled {len(report)} show tag(s) for {cid}: "
                       + ", ".join(f"{i}@{d:.2f}s" for i, d in report))
        return report

    def on_alignment(self, payload: dict) -> List[Tuple[str, float]]:
        """Refine pending tags with one chunk of ElevenLabs character timing.

        ``payload`` is a ``speech.alignment`` event: ``chars`` covered by this chunk, their
        ``char_start_ms`` from the start of the reply's audio, and ``audio_t0`` (wall clock of
        the first sample). Returns ``[(item_id, new_delay_s)]`` for the tags it moved.
        """
        cid = (payload or {}).get("conversation_id")
        active = self._active.get(cid) if cid else None
        if active is None:
            return []
        items, seen = active
        chars = payload.get("chars") or []
        starts = payload.get("char_start_ms") or []
        t0 = payload.get("audio_t0")
        moved = []
        if t0 is not None:
            loop = asyncio.get_running_loop()
            for item in items:
                i = item.index - seen[0]
                if item.fired or item.refined or not (0 <= i < min(len(chars), len(starts))):
                    continue
                delay = max(0.0, float(t0) + float(starts[i]) / 1000.0 - time.time())
                if item.handle is not None:
                    item.handle.cancel()
                item.handle = loop.call_later(delay, self._fire_scheduled, item, cid)
                item.refined = True
                moved.append((item.tag.id, delay))
        seen[0] += len(chars)
        if moved:
            self._log.info(f"Show tag timing from speech alignment for {cid}: "
                           + ", ".join(f"{i}@+{d:.2f}s" for i, d in moved))
        return moved

    def cancel_all(self) -> None:
        for items, _ in self._active.values():
            for item in items:
                if item.handle is not None:
                    item.handle.cancel()
        self._active.clear()
        self._pending.clear()

    def _fire_scheduled(self, item: _Scheduled, conversation_id: Optional[str]) -> None:
        if item.fired:
            return
        item.fired = True
        active = self._active.get(conversation_id)
        if active and all(i.fired for i in active[0]):
            self._active.pop(conversation_id, None)
        self._fire(item.tag.id, conversation_id)

    def _fire(self, item_id: str, conversation_id: Optional[str]) -> None:
        try:
            self._perform(item_id, conversation_id)
        except Exception as e:  # a tag must never break the voice loop
            self._log.warning(f"show tag {item_id!r} failed to fire: {e}")
