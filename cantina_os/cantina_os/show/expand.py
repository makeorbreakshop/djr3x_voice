"""Expansion: a cue or sequence flattened to a time-sorted list of department actions.

This is the cross-language parity contract (``show/SPEC.md`` "Cross-language parity"):
the Python and TypeScript runtimes must both reproduce ``show/tests/golden/*.json``.

* ``t`` in seconds, rounded to 3 decimals;
* defaults filled in for ``clip`` (intensity 1, speed 1), ``lights`` (fade 0, hold 0) and
  ``chest`` (hold 0), and nowhere else;
* ``wait`` dropped (resolved as 0 s), loops not repeated;
* ``bpm`` stands in for the live tempo and overrides every beat-clock sequence's own bpm;
* nested cues and sequences flattened with their ``at`` added; ties keep file order.

Cue action offsets are seconds (a cue has no clock). A sequence's ``at`` is seconds on a
``time`` clock and beats on a ``beat`` clock.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import MAX_NESTING, Action, Clip, Cue, Sequence

DEFAULTS = {
    "clip": {"intensity": 1.0, "speed": 1.0},
    "lights": {"fade": 0.0, "hold": 0.0},
    "chest": {"hold": 0.0},
}


def normalise_action(action: Action) -> Dict[str, Any]:
    """``{"do": ..., **fields}`` with the spec's defaults filled in."""
    fields = action.fields()
    do = fields.pop("do")
    out: Dict[str, Any] = {"do": do}
    out.update(fields)
    for key, value in DEFAULTS.get(do, {}).items():
        if out.get(key) is None:
            out[key] = value
    return out


def seconds_per_unit(seq: Sequence, bpm: Optional[float]) -> float:
    if seq.clock == "beat":
        return 60.0 / (bpm or seq.bpm)
    return 1.0


def expand(library, root_id: str, bpm: Optional[float] = None) -> List[Dict[str, Any]]:
    """Flatten ``root_id`` (clip, cue or sequence) from ``library`` into timed actions."""
    root = library.get(root_id)
    if root is None:
        raise KeyError(f"unknown show item {root_id!r}")
    out: List[tuple] = []  # (t, emit_order, action_dict)

    def push(t: float, action: Dict[str, Any]) -> None:
        out.append((round(t + 0.0, 6), len(out), action))

    def clip_action(clip_id: str, intensity=None, speed=None) -> Dict[str, Any]:
        return {"do": "clip", "id": clip_id,
                "intensity": 1.0 if intensity is None else intensity,
                "speed": 1.0 if speed is None else speed}

    def walk_cue(cue: Cue, t0: float) -> None:
        for a in cue.actions:
            if a.do != "wait":
                push(t0 + a.at, normalise_action(a))

    def walk_sequence(seq: Sequence, t0: float, depth: int) -> None:
        unit = seconds_per_unit(seq, bpm)
        for item in seq.track:
            t = t0 + item.at * unit
            kind = item.ref_kind
            if kind == "cue":
                walk_cue(library.get(item.cue), t)
            elif kind == "clip":
                push(t, clip_action(item.clip, item.intensity, item.speed))
            elif kind == "sequence":
                if depth + 1 > MAX_NESTING:
                    raise ValueError(f"{seq.id}: nesting deeper than {MAX_NESTING}")
                walk_sequence(library.get(item.sequence), t, depth + 1)
            elif item.do != "wait":
                push(t, normalise_action(item))

    if isinstance(root, Clip):
        push(0.0, clip_action(root.id))
    elif isinstance(root, Cue):
        walk_cue(root, 0.0)
    else:
        walk_sequence(root, 0.0, 0)

    # Stable sort on the rounded time: ties keep file (depth-first) order.
    ordered = sorted(out, key=lambda e: (round(e[0], 3), e[1]))
    return [{"t": round(t, 3), **action} for t, _, action in ordered]
