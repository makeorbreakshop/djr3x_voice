"""Hero-arm spacer block (Anderson, `spacerblock`) - the wedge between the body tube and the wrist.

A 55 mm bar with a trapezoid section (20 wide on the bed, 51.66 across the top, 11.5 tall, sides at
36 deg to the bed), a 6 mm centre bore and four M3 clearance holes on the tube's 10 mm square.

Frame = his STEP's: bed z = 0, the bar along X, the holes along Z.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from . import HOLE_SIZES

REFERENCE = "r3x-internal - spacerblock.step"
LABEL = "Hero-arm spacer block (parametric)"
DESIGNED = {"bolt": "clearance"}       # the body tube's four M3 pass through into its inserts
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    bolt="M3",
    length=55.0, height=11.5, base_half=10.0, side_angle=36.0,   # the sides' slope, deg from the bed
    bore_d=6.0, square=10.0, hole_d=3.5,
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Plane, Pos, extrude

    from ._sketch import profile

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown spacerblock parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED)
    L, H, b, fit = P["length"], P["height"], P["base_half"], P["fit"]
    t = b + H / math.tan(math.radians(P["side_angle"]))
    body = extrude(Plane.YZ.offset(-L / 2) * profile([(-b, 0.0), (b, 0.0), (t, H), (-t, H)]), amount=L)
    body -= Pos(0, 0, H / 2) * Cylinder((P["bore_d"] + fit) / 2, H + 0.02)
    feats: dict = {"narrow": plane((0, 0, 0), (0, 0, -1)), "wide": plane((0, 0, H), (0, 0, 1))}
    hole_features(feats, "bore", (0, 0, H), (0, 0, -1), (P["bore_d"] + fit) / 2, depth=H)
    s = P["square"] / 2
    k = 0
    for x in (-s, s):
        for y in (-s, s):
            k += 1
            if types["bolt"] == DESIGNED["bolt"]:
                body -= Pos(x, y, H / 2) * Cylinder((P["hole_d"] + fit) / 2, H + 0.02)
                hole_features(feats, f"bolt{k}", (x, y, H), (0, 0, -1), (P["hole_d"] + fit) / 2, depth=H,
                              bolt=P["bolt"], kind="clearance")
            else:
                body = cut_hole(body, feats, f"bolt{k}", (x, y, H), (0, 0, -1), P["bolt"], types["bolt"], H, fit,
                                HOLE_SIZES)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("wide face down (z = 11.5 on the bed)", "wide", False,
                                     "upside down the sides lean in at 36 deg; the narrow way up they would overhang"))
