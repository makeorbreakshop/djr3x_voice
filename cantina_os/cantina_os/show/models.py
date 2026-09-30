"""Pydantic models for the R3X show format v1 (``show/SPEC.md``).

Three kinds of file, small to large: a **clip** is motion only, a **cue** is one moment
across departments, and a **sequence** is a timeline of cues/clips/actions on one clock.

The models are deliberately tolerant (``extra="allow"``): the sim and its authoring tools
may carry fields CantinaOS does not use, and a file must not become unloadable because of
them. Structural rules live here; cross-file rules (references, tiers, nesting) live in
``validate.py``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

Tier = Literal["free", "cheap", "show"]
TIER_RANK: Dict[str, int] = {"free": 0, "cheap": 1, "show": 2}

Kind = Literal["clip", "cue", "sequence"]

#: Department actions, straight from the spec's `do` table.
DO_KINDS = ("clip", "eyes", "chest", "lights", "sfx", "speak", "duck", "unduck", "wait")
Do = Literal["clip", "eyes", "chest", "lights", "sfx", "speak", "duck", "unduck", "wait"]

#: The nine joints the base build drives: the 8-servo ``r3x_animation`` mechanics plus the
#: head roll of Hunter's head mech.
BASE_JOINTS = frozenset(
    {"head_pan", "head_tilt", "head_roll", "head_lift", "visor", "hero_shoulder", "hero_wrist", "torso_lower",
     "torso_top"}
)
EXTENDED_JOINT_PREFIXES = ("hero_claw_", "throttle_", "poker_")
EXTENDED_JOINTS = frozenset({"torso_middle"})

INTENSITY_RANGE = (0.0, 1.5)
SPEED_RANGE = (0.5, 2.0)
MAX_NESTING = 3


def is_extended_joint(name: str) -> bool:
    return name in EXTENDED_JOINTS or name.startswith(EXTENDED_JOINT_PREFIXES)


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ShowItemBase(_Base):
    id: str
    kind: Kind
    title: Optional[str] = None
    #: ONE line - this is what Claude and Jev see in their catalogue.
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    tier: Tier

    @model_validator(mode="after")
    def _check_id(self):
        if not ID_RE.match(self.id):
            raise ValueError(f"id {self.id!r} is not snake_case")
        if "\n" in self.description:
            raise ValueError("description must be one line")
        return self


# ---------------------------------------------------------------------------- clip


class ClipTrack(_Base):
    mode: Literal["additive", "override"] = "additive"
    keys: List[Tuple[float, float]]
    ease: Literal["minjerk", "linear", "step"] = "minjerk"
    #: Override blend in/out, seconds. None = the joint-class default (a sim concern).
    blend: Optional[float] = None

    @model_validator(mode="after")
    def _check_keys(self):
        if not self.keys:
            raise ValueError("a track needs at least one key")
        if self.keys[0][0] != 0:
            raise ValueError("the first key must be at t=0")
        times = [k[0] for k in self.keys]
        if any(b < a for a, b in zip(times, times[1:])):
            raise ValueError("keys must be in time order")
        if self.mode == "additive" and (self.keys[0][1] != 0 or self.keys[-1][1] != 0):
            raise ValueError("additive tracks must start and end at 0")
        return self


class Clip(ShowItemBase):
    kind: Literal["clip"] = "clip"
    duration: float = Field(..., gt=0)
    interruptible_after: float = Field(0.0, ge=0)
    requires: Optional[Literal["extended"]] = None
    tracks: Dict[str, ClipTrack] = Field(default_factory=dict)


# ---------------------------------------------------------------------------- actions


class Action(_Base):
    """One department action. Used for cue ``actions`` and sequence ``{at, do: ...}`` items."""

    at: float = Field(0.0, ge=0)
    do: Do
    # clip / sfx
    id: Optional[str] = None
    intensity: Optional[float] = None
    speed: Optional[float] = None
    # eyes
    pattern: Optional[str] = None
    color: Optional[str] = None
    duration: Optional[float] = None
    # chest
    command: Optional[str] = None
    hold: Optional[float] = None
    # lights
    cue: Optional[str] = None
    mode: Optional[str] = None
    fade: Optional[float] = None
    rig: Optional[str] = None
    # speak
    text: Optional[str] = None
    # wait
    for_: Optional[Literal["speech_end"]] = Field(None, alias="for")

    @model_validator(mode="after")
    def _check_fields(self):
        need = {
            "clip": ("id",),
            "eyes": ("pattern",),
            "chest": ("command",),
            "sfx": ("id",),
            "speak": ("text",),
            "wait": ("for_",),
        }.get(self.do, ())
        for field in need:
            if getattr(self, field) in (None, ""):
                raise ValueError(f"do={self.do!r} requires {field.rstrip('_')!r}")
        if self.do == "lights" and not (self.cue or self.mode):
            raise ValueError("do='lights' requires 'cue' and/or 'mode'")
        return self

    def fields(self) -> Dict[str, Any]:
        """The action's own fields as authored (no ``at``), JSON names."""
        data = self.model_dump(exclude_unset=True, by_alias=True)
        data.pop("at", None)
        return data


class Cue(ShowItemBase):
    kind: Literal["cue"] = "cue"
    actions: List[Action] = Field(default_factory=list)


# ---------------------------------------------------------------------------- sequence


class TrackItem(Action):
    """``{at, cue}`` | ``{at, clip, intensity?, speed?}`` | ``{at, sequence}`` | ``{at, do: ...}``."""

    do: Optional[Do] = None  # type: ignore[assignment]
    clip: Optional[str] = None
    sequence: Optional[str] = None

    @model_validator(mode="after")
    def _check_fields(self):
        # `cue` doubles as the lights field, so a lights action is identified by `do`.
        refs = [k for k in ("clip", "sequence") if getattr(self, k)]
        if self.do is None and self.cue:
            refs.append("cue")
        if self.do is not None:
            if refs:
                raise ValueError(f"track item mixes do={self.do!r} with {refs}")
            return Action._check_fields(self)  # same rules as a cue action
        if len(refs) != 1:
            raise ValueError("track item needs exactly one of cue / clip / sequence / do")
        return self

    @property
    def ref_kind(self) -> Optional[str]:
        if self.do is not None:
            return None
        if self.sequence:
            return "sequence"
        if self.clip:
            return "clip"
        return "cue"

    @property
    def ref_id(self) -> Optional[str]:
        kind = self.ref_kind
        return getattr(self, kind) if kind else None


class Sequence(ShowItemBase):
    kind: Literal["sequence"] = "sequence"
    #: beat: `at` is in beats; seconds = beat * 60 / bpm.
    clock: Literal["time", "beat"] = "time"
    #: Used when clock=beat and there is no live music tempo.
    bpm: float = Field(120.0, gt=0)
    layer: Literal["show", "gesture"] = "show"
    owns: Optional[List[str]] = None
    loop: bool = False
    #: Loop length in the clock's units (needed when loop=true).
    length: Optional[float] = Field(None, gt=0)
    track: List[TrackItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_loop(self):
        if self.loop and not self.length:
            raise ValueError("loop=true needs 'length'")
        return self


ShowItem = Union[Clip, Cue, Sequence]
MODEL_BY_KIND = {"clip": Clip, "cue": Cue, "sequence": Sequence}


def parse_item(data: Dict[str, Any]) -> ShowItem:
    """Parse one show file's JSON object into its model. Raises ValueError/ValidationError."""
    if not isinstance(data, dict):
        raise ValueError("a show file must hold a JSON object")
    kind = data.get("kind")
    model = MODEL_BY_KIND.get(kind)
    if model is None:
        raise ValueError(f"unknown kind {kind!r} (expected clip, cue or sequence)")
    return model.model_validate(data)


def clamp(value: Optional[float], lo_hi: Tuple[float, float], default: float = 1.0) -> float:
    lo, hi = lo_hi
    v = default if value is None else float(value)
    return max(lo, min(hi, v))
