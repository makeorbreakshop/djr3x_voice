"""Ring-drive internal gear sectors (Anderson, `lower-ring-inner-gear` / `top-ring-inner-gear`) -
the arcs of internal teeth fixed inside the droid's rings that the ring servos' pinions walk.

Rounded internal teeth (round tips, straight flanks, round root fillets mid-gap; _gear.py) on a
sector of the ring, out to the ring's inner radius.

- `lower`: a 95-tooth ring, 45 mm tall, on a wider 6 mm flange with an M5 hole at each end;
- `top`:   an 85-tooth ring, 14 mm tall, two M4 holes with hex nut traps from the top.

His sources are STLs only: the teeth are fitted to them (mean ~0.01 mm), the rest measured; the
sector ends' small blends (r1.5-r4 chains into the last tooth) are simplified to one round each.

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
           outer_r=124.0, height=14.0, sector=(15.803, 75.173), corner_r=3.0, flange=None,
           holes=(118.5, (30.0, 60.0), 4.5), nut=(4.25, 4.0, 90.0))
TOP_REFERENCE = ("r3x - top ring animation", "r3x-top-ring-animation - top-ring-inner-gear.stl")

DEFAULTS = dict(
    kind="lower",
    fit=0.0,
    teeth=95, tip=(1.4924, 114.9916), fillet=(2.0333, 116.4716), phase=2.8421,
    outer_r=124.5, height=45.0,
    sector=(50.221, 129.779), corner_r=2.0,
    flange=(6.0, (42.632, 137.368), 4.0),      # height, sector, end corner round
    holes=(118.5, (45.0, 135.0), 5.0),          # radius, angles, diameter (through the flange / the base)
    nut=None,                                   # (hex circumradius, floor z, corner angle) - nut traps from the top
    bolt="M4",
    inserts=False, ring_hole=None,              # hole type for the fixing screws (as designed: clearance / nut trap)
)
DESIGNED = {"ring": "clearance"}                # the lower sector's; the top's are nut traps (TOP)
INSERT_CANDIDATES = ("ring",)


def _band(P, a0, a1, r_round):
    face = rounded_internal_band(P["teeth"], P["tip"], P["fillet"], P["phase"], a0, a1, P["outer_r"])
    if r_round:
        corners = [v for v in face.vertices() if abs(math.hypot(v.X, v.Y) - P["outer_r"]) < 1e-3]
        face = face.fillet_2d(r_round, corners) if hasattr(face, "fillet_2d") else face
    return face


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
        fh, (f0, f1), fr = P["flange"]
        parts.append(extrude(_band(P, f0, f1, fr).faces()[0], amount=fh))
        z_body = fh
    parts.append(extrude(Plane.XY.offset(z_body) * _band(P, a0, a1, P["corner_r"]).faces()[0], amount=H - z_body))
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
