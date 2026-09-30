"""Head-lift base ring (Anderson, `headlift-base-mount (1)`) - the flanged ring the pan ring-gear
and the base stack bolt to.

A tube (bore to outer) under a wider flange at its top: eight M5 clearance holes round the flange,
four M5 through the tube's wall with round nut/head pockets from underneath.

Frame = the STEP's: axis +Z at the origin, z = 0 .. height, the flange on top.
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES

REFERENCE = "r3x-internal - headlift-base-mount (1).step"
LABEL = "Head-lift base ring (parametric)"

DEFAULTS = dict(
    fit=0.0,
    bore_r=111.5, tube_r=127.5, flange_r=155.0, height=38.0, flange_t=8.0,
    bolt="M5", hole_sizes=HOLE_SIZES,
    flange_holes=(144.0, 8, 0.0, 5.0),   # radius, count, first angle, diameter (Anderson: 5.0 here)
    tube_holes=(118.5, 4, 0.0),          # radius, count, first angle: M5 clearance through the wall
    pocket=(7.0, 5.5),                   # under each tube hole: diameter, depth
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Pos

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown base_ring parameters: {sorted(unknown)}")
    fit = P["fit"]
    H, ft = P["height"], P["flange_t"]
    rb = P["bore_r"] + fit / 2
    body = Pos(0, 0, (H - ft) / 2) * Cylinder(P["tube_r"], H - ft) + Pos(0, 0, H - ft / 2) * Cylinder(P["flange_r"], ft)
    body -= Pos(0, 0, H / 2) * Cylinder(rb, H + 1)
    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, H), (0, 0, 1)),
                   "flange_under": plane((P["tube_r"] + 5, 0, H - ft), (0, 0, -1))}
    hole_features(feats, "bore", (0, 0, 0), (0, 0, 1), rb, depth=H)
    r, n, a0, fd = P["flange_holes"]
    for k in range(n):
        a = math.radians(a0 + 360.0 * k / n)
        x, y = r * math.cos(a), r * math.sin(a)
        body -= Pos(x, y, H - ft / 2) * Cylinder((fd + fit) / 2, ft + 0.02)
        hole_features(feats, f"flange{k + 1}", (x, y, H), (0, 0, -1), (fd + fit) / 2, depth=ft)
    d = hole_d(P["bolt"], "clearance", fit, P["hole_sizes"])
    r, n, a0 = P["tube_holes"]
    pd, pdep = P["pocket"]
    for k in range(n):
        a = math.radians(a0 + 360.0 * k / n)
        x, y = r * math.cos(a), r * math.sin(a)
        body -= Pos(x, y, H / 2) * Cylinder(d / 2, H + 0.02)
        body -= Pos(x, y, pdep / 2) * Cylinder((pd + fit) / 2, pdep + 0.02)
        hole_features(feats, f"tube{k + 1}", (x, y, H), (0, 0, -1), d / 2, depth=H - pdep, bolt=P["bolt"], kind="clearance")
        hole_features(feats, f"pocket{k + 1}", (x, y, 0), (0, 0, 1), (pd + fit) / 2, depth=pdep)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flange down (z = height on the bed)", "top", False,
                                     "flange down the tube rises from it and the pockets open upward; 310 mm "
                                     "across: needs a large-format bed"))
