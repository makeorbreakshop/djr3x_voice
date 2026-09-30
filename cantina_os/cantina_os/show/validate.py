"""Cross-file validation of a show library.

Per-file structure is enforced by the Pydantic models. This adds the rules that need the
whole library:

* references resolve, and to the right kind;
* **tier rule**: an item may only reference items of the same or a lower tier. Otherwise a
  ``free`` cue (which Jev's loose gate and Claude's tags may trigger) could smuggle in a
  ``show``-tier sequence;
* sequence nesting depth <= 3 and no cycles;
* clip joints: known rig joints, extended joints only with ``"requires": "extended"``,
  ``interruptible_after`` within the clip;
* params (intensity 0-1.5, speed 0.5-2) and loop sanity.

Joint limits and vMax/aMax against ``servo_map.json`` are linted by the sim's TypeScript
suite, which owns the actuation model.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from .models import (
    BASE_JOINTS,
    INTENSITY_RANGE,
    MAX_NESTING,
    SPEED_RANGE,
    TIER_RANK,
    Clip,
    Cue,
    Sequence,
    is_extended_joint,
)


def _in(value: Optional[float], rng) -> bool:
    return value is None or rng[0] <= value <= rng[1]


def validate_library(lib) -> list:
    from .loader import Issue

    issues: List[Issue] = []

    def err(where, msg):
        issues.append(Issue("error", where, msg))

    def warn(where, msg):
        issues.append(Issue("warning", where, msg))

    def check_ref(owner, kind: str, ref: str):
        target = lib.items.get(ref)
        if target is None:
            err(owner.id, f"unknown {kind} {ref!r}")
            return
        if target.kind != kind:
            err(owner.id, f"{ref!r} is a {target.kind}, not a {kind}")
            return
        if TIER_RANK[target.tier] > TIER_RANK[owner.tier]:
            err(owner.id, f"tier {owner.tier!r} may not include {kind} {ref!r} of tier {target.tier!r}")

    def check_params(owner, what, intensity, speed):
        if not _in(intensity, INTENSITY_RANGE):
            err(owner.id, f"{what}: intensity {intensity} outside {INTENSITY_RANGE}")
        if not _in(speed, SPEED_RANGE):
            err(owner.id, f"{what}: speed {speed} outside {SPEED_RANGE}")

    for item in lib.items.values():
        if not item.description:
            warn(item.id, "no description (Claude/Jev catalogue shows nothing for it)")

        if isinstance(item, Clip):
            if item.interruptible_after > item.duration:
                err(item.id, "interruptible_after is longer than the clip")
            for joint, track in item.tracks.items():
                if joint in BASE_JOINTS:
                    pass
                elif is_extended_joint(joint):
                    if item.requires != "extended":
                        err(item.id, f"extended joint {joint!r} needs \"requires\": \"extended\"")
                else:
                    err(item.id, f"unknown joint {joint!r}")
                if track.keys[-1][0] > item.duration + 1e-9:
                    err(item.id, f"{joint}: a key lies past duration {item.duration}")

        elif isinstance(item, Cue):
            for n, a in enumerate(item.actions):
                if a.do == "clip":
                    check_ref(item, "clip", a.id)
                    check_params(item, f"action {n}", a.intensity, a.speed)

        elif isinstance(item, Sequence):
            for n, t in enumerate(item.track):
                kind = t.ref_kind
                if kind:
                    check_ref(item, kind, t.ref_id)
                    if kind == "clip":
                        check_params(item, f"track {n}", t.intensity, t.speed)
                elif t.do == "clip":
                    check_ref(item, "clip", t.id)
                    check_params(item, f"track {n}", t.intensity, t.speed)
                if item.loop and item.length and t.at >= item.length:
                    warn(item.id, f"track {n} at {t.at} is outside the loop length {item.length}")

    # nesting depth and cycles, over sequences only
    seqs: Dict[str, Sequence] = {i.id: i for i in lib.items.values() if isinstance(i, Sequence)}

    def depth(seq_id: str, stack: Set[str]) -> int:
        """Levels of nested sequences below ``seq_id`` (0 = none)."""
        if seq_id in stack:
            raise RecursionError(" -> ".join(list(stack) + [seq_id]))
        seq = seqs.get(seq_id)
        if seq is None:
            return 0
        children = [t.sequence for t in seq.track if t.ref_kind == "sequence" and t.sequence in seqs]
        return max((1 + depth(c, stack | {seq_id}) for c in children), default=0)

    for seq_id in seqs:
        try:
            d = depth(seq_id, set())
        except RecursionError as e:
            err(seq_id, f"sequence cycle: {e}")
            continue
        if d > MAX_NESTING:
            err(seq_id, f"nesting depth {d} exceeds {MAX_NESTING}")
    return issues
