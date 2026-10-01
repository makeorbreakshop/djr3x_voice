"""Ring-drive internal gear sectors (Anderson, `lower-ring-inner-gear` / `top-ring-inner-gear`) -
the arcs of internal teeth fixed inside the droid's rings that the ring servos' pinions walk.

Rounded internal teeth (round tips, straight flanks, round root fillets mid-gap; _gear.py) on a
sector of the ring, out to the ring's inner radius.

- `lower`: a 95-tooth ring, 45 mm tall, on a wider 6 mm flange with an M5 hole at each end;
- `top`:   an 85-tooth ring, 14 mm tall, two M4 holes with hex nut traps from the top.

His sources are STLs only: the teeth are fitted to them (mean ~0.01 mm), the rest measured. Each
sector end is a radial cut with a round at its outer corner (`corner_r`) and one where it meets
the last tooth (`end_r`), both fitted to the STLs. The lower sector's flange is solid at its ends:
from the flange's own end to the second tooth in (`tabs`), the gaps are filled to the tips' circle.

Frame = the STLs': ring axis +Z at the origin, underside z = 0.

    make()              the lower sector
    make(**TOP)         the top sector
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, plane, resolve_hole_types

from ._gear import rounded_internal_band

REFERENCE = "r3x-lower-ring-animation - lower-ring-inner-gear.stl"
REF_SUBDIR = "r3x - lower ring animation"
LABEL = "Ring internal gear sector (parametric)"

TOP = dict(kind="top", teeth=85, tip=(1.4569, 108.7028), fillet=(1.7944, 111.7818), phase=1.0588,
           outer_r=124.0, height=14.0, sector=(15.803, 75.173), corner_r=3.0, end_r=3.0, flange=None,
           holes=(118.5, (30.0, 60.0), 4.5), nut=(4.25, 4.0, 90.0))
TOP_REFERENCE = ("r3x - top ring animation", "r3x-top-ring-animation - top-ring-inner-gear.stl")

DEFAULTS = dict(
    kind="lower",
    fit=0.0,
    teeth=95, tip=(1.4924, 114.9916), fillet=(2.0333, 116.4716), phase=2.8421,
    outer_r=124.5, height=45.0,
    sector=(50.221, 129.779), corner_r=2.0, end_r=2.0,   # the ends' outer / last-tooth rounds
    flange=(6.0, (42.632, 137.368), 4.0, (48.316, 131.684)),   # height, sector, end rounds, solid tabs to (tooth tips)
    holes=(118.5, (45.0, 135.0), 5.0),          # radius, angles, diameter (through the flange / the base)
    nut=None,                                   # (hex circumradius, floor z, corner angle) - nut traps from the top
    bolt="M4",
    inserts=False, ring_hole=None,              # hole type for the fixing screws (as designed: clearance / nut trap)
)
STL_MAX_MM = 0.3                                # held to the droid's worst-case standard (tests)
DESIGNED = {"ring": "clearance"}                # the lower sector's; the top's are nut traps (TOP)
INSERT_CANDIDATES = ("ring",)


def _band(P, a0, a1, r_out, r_end):
    """The toothed band between a0 and a1 (deg): r_out rounds its outer corners, r_end the corners
    where the radial ends meet the teeth."""
    R = P["outer_r"]
    face = rounded_internal_band(P["teeth"], P["tip"], P["fillet"], P["phase"], a0, a1, R).faces()[0]
    if r_out:
        face = face.fillet_2d(r_out, [v for v in face.vertices() if abs(math.hypot(v.X, v.Y) - R) < 1e-3])
    if r_end:
        ends = [v for v in face.vertices() if math.hypot(v.X, v.Y) < R - r_out - 0.5
                and min(abs(math.degrees(math.atan2(v.Y, v.X)) - a) for a in (a0, a1)) < 1e-4]
        face = face.fillet_2d(r_end, ends)
    return face


def _tab(P, a0, a1, r_round, end):
    """A solid annular sector from the tips' circle out to the ring, a0 .. a1 (deg), its corners at
    the `end` angle rounded."""
    from ._sketch import profile

    r_in = P["tip"][1] - P["tip"][0]
    R = P["outer_r"]

    def pt(r, a):
        return (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))

    face = profile([pt(r_in, a0), ("arc", (0.0, 0.0)), pt(r_in, a1), pt(R, a1), ("arc", (0.0, 0.0)), pt(R, a0)])
    corners = [v for v in face.vertices() if abs(math.degrees(math.atan2(v.Y, v.X)) - end) < 1e-4]
    return face.fillet_2d(r_round, corners)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Plane, Pos, RegularPolygon, extrude, BuildSketch

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown ring_gear parameters: {sorted(unknown)}")
    fit, H = P["fit"], P["height"]
    a0, a1 = P["sector"]
    z_body = 0.0
    parts = []
    if P["flange"]:
        fh, (f0, f1), fr, (t0, t1) = P["flange"]
        flange = extrude(_band(P, t0, t1, 0.0, 0.0), amount=fh)
        flange += (extrude(_tab(P, f0, t0, fr, f0), amount=fh, dir=(0, 0, 1))
                   + extrude(_tab(P, t1, f1, fr, f1), amount=fh, dir=(0, 0, 1)))
        parts.append(flange)
        z_body = fh
    parts.append(extrude(Plane.XY.offset(z_body) * _band(P, a0, a1, P["corner_r"], P["end_r"]), amount=H - z_body))
    body = parts[0]
    for q in parts[1:]:
        body = body + q
    feats: dict = {"bottom": plane((0, P["outer_r"] - 3, 0), (0, 0, -1)), "top": plane((0, P["outer_r"] - 3, H), (0, 0, 1)),
                   "ring_axis": {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": P["outer_r"]}}
    r, angs, d = P["holes"]
    depth = P["flange"][0] if P["flange"] else (P["nut"][1] if P["nut"] else H)
    designed = {"ring": "nut_trap" if P["nut"] else "clearance"}
    t = resolve_hole_types(P, designed, INSERT_CANDIDATES)["ring"]
    for i, a in enumerate(angs):
        x, y = r * math.cos(math.radians(a)), r * math.sin(math.radians(a))
        if t != designed["ring"]:
            body = cut_hole(body, feats, f"h{i + 1}", (x, y, 0), (0, 0, 1), P["bolt"], t,
                            P["flange"][0] if P["flange"] else H, fit, grow_boss=True)
            continue
        body -= Pos(x, y, depth / 2) * Cylinder((d + fit) / 2, depth + 0.02)
        hole_features(feats, f"h{i + 1}", (x, y, 0), (0, 0, 1), (d + fit) / 2, depth=depth)
        if P["nut"]:
            hr, hz, hang = P["nut"]
            with BuildSketch(Plane.XY.offset(hz)) as hx:
                RegularPolygon(hr + fit / 2, 6, rotation=hang)
            body -= Pos(x, y, 0) * extrude(hx.sketch, amount=H - hz + 0.01)
            feats[f"nut{i + 1}"] = plane((x, y, hz), (0, 0, 1))
            # the trap as an axis: radius midway between the hexagon's corner and flat
            hole_features(feats, f"trap{i + 1}", (x, y, H), (0, 0, -1), (hr + fit / 2) * (1 + math.cos(math.pi / 6)) / 2,
                          depth=H - hz)
            feats[f"hole_trap{i + 1}"]["shape"] = "hex"
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z = 0 on the bed)", "bottom", False, "an extruded sector: no supports"))
