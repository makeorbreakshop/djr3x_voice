"""Part finishes (manifest `finish`, SCHEMA.md "Finish"): the paint a part wears on the finished droid,
the filament and colour a printed part is printed in, and the real colour of a purchased one.

Finishes are assigned where the parts are declared (the assembly modules and mech/parts), never in a
viewer: Build's Exterior look reads `paint` through sim/web/src/palette.json, the Mechanism look shows
`print.color` (printed) or `color` (purchased), and the print list groups `print`. The kit's paint lives
in assemblies/kit/finish.json, which sim/model/build_r3x.py also reads, so the Original rig and Build
are painted from one table.

    from workbench.finish import PETG_GREY, finish
    p.finish = finish(paint="paint_charcoal", print=PLA_BLACK)
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PALETTE = REPO / "sim/web/src/palette.json"
# `paint: "none"`: seen from outside and deliberately left bare (shown in its filament or material colour)
BARE = "none"


def palette_classes() -> set[str]:
    try:
        return set(json.loads(PALETTE.read_text())["classes"])
    except Exception:  # a checkout without the sim: names are not validated
        return set()


def filament(kind: str, color: str, name: str) -> dict:
    return {"filament": kind, "color": color, "color_name": name}


# Filaments. Structural internals: PETG (or PA-CF where the source says so) in a neutral colour; Hunter's
# photos show his head mech in black. The kit's painted shells: any PLA, grey shown (it is primed over).
PLA_GREY = filament("PLA", "#8e9195", "Grey")
PLA_BLACK = filament("PLA", "#1f2023", "Black")
PLA_NATURAL = filament("PLA", "#ece6d6", "Natural (translucent)")
PETG_BLACK = filament("PETG", "#1d1e21", "Black")
PETG_GREY = filament("PETG", "#6b6f75", "Grey")
PACF_BLACK = filament("PA-CF", "#2a2b2d", "Carbon black")

# Purchased parts with a colour of their own (the rest show their material: aluminium, steel, brass).
GOBILDA_SERVO = {"color": "#3d4045", "color_name": "goBILDA servo grey"}
SERVO_BLACK = {"color": "#202124", "color_name": "Black case"}
BLACK_ANODISED = {"color": "#24262a", "color_name": "Black anodised"}
CLEAR_PC = {"color": "#b9c2c6", "color_name": "Clear polycarbonate"}


def finish(paint: str | None = None, print: dict | None = None, color: dict | None = None,  # noqa: A002
           kit: str | None = None, note: str = "") -> dict:
    """A part's finish: `paint` (palette class, or BARE), `print` (filament()), `color` (a purchased part's
    {"color", "color_name"}), `kit` (the kit code whose paint it carries)."""
    out: dict = {}
    if paint:
        out["paint"] = paint
    if print:
        out["print"] = dict(print)
    if color:
        out.update(color)
    if kit:
        out["kit"] = kit
    if note:
        out["note"] = note
    return out


def apply(parts, table: dict, printed_default: dict | None = None):
    """Set `finish` on `parts` from `table` (part id -> finish dict), merged over what a part already has.
    A printed part with no `print` takes `printed_default`."""
    for p in parts:
        f = dict(getattr(p, "finish", None) or {})
        if p.id in table:
            f.update(table[p.id])
        if getattr(p, "printed", False) and "print" not in f and printed_default:
            f["print"] = dict(printed_default)
        p.finish = f or None


def exposed(p) -> bool:
    """Seen from outside the droid: manifest `exposed`, else shells only (as the viewer reads it)."""
    e = getattr(p, "exposed", None)
    return e if e is not None else p.cls == "shell"


def needs_paint(p) -> bool:
    """A part someone sees on the finished droid that is printed (or a shell) must say how it is painted:
    a palette class, or BARE. Purchased metal seen from outside may stay unpainted (it shows its material)."""
    if getattr(p, "replaced_by", None):
        return False
    return exposed(p) and (bool(getattr(p, "printed", False)) or p.cls == "shell")


def problems(p, classes: set[str] | None = None) -> list[str]:
    """What is wrong with one part's finish (empty = fine)."""
    classes = palette_classes() if classes is None else classes
    f = getattr(p, "finish", None) or {}
    out = []
    paint = f.get("paint")
    if needs_paint(p) and not paint:
        out.append("seen from outside but has no paint")
    if paint and paint != BARE and classes and paint not in classes:
        out.append(f"paint {paint!r} is not a palette class")
    if getattr(p, "printed", False):
        pr = f.get("print") or {}
        if not (pr.get("filament") and pr.get("color") and pr.get("color_name")):
            out.append("printed but has no print filament / colour")
    return out


def check(parts):
    """The manifest Check for a tree's finishes (fail when any part is missing one)."""
    from .model import Check

    classes = palette_classes()
    bad = {p.id: problems(p, classes) for p in parts}
    bad = {k: v for k, v in bad.items() if v}
    n = sum(1 for _ in parts)
    if not bad:
        return Check("finish", "finish", "pass", "Finishes: paint and print colour",
                     f"All {n} parts have a finish (paint where seen, filament where printed)")
    items = [f"{pid}: {', '.join(v)}" for pid, v in sorted(bad.items())]
    return Check("finish", "finish", "fail", "Finishes: paint and print colour",
                 f"{len(bad)} parts without a full finish; " + "; ".join(items), parts=sorted(bad),
                 assumptions=["Exterior shows a missing paint in magenta; assign it where the part is declared "
                              "(SCHEMA.md 'Finish')"])
