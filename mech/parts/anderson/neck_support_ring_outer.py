"""Neck support ring, outer (Anderson) - the big ring the neck's upper support hangs from.

A 10 mm disc between a bore and the rim, lightened by six windows between 16 deg spokes (r8
corners), with a raised rim round the outside and a groove inside it, and three circles of M5
clearance holes: 12 through the rim, 12 and 8 through the disc. The rim's inner wall registers
the part that sits in it (feature `hole_rim`).

Frame = the STEP's: axis +Z at the origin, underside z = 0.
"""

from __future__ import annotations

import math

from parts.head._common import (Print, cut_hole, finish, hole_d, hole_features, hole_group_params, plane,
                                resolve_hole_types)

from . import HOLE_SIZES

REFERENCE = "r3x-internal - neck-support-ring-outer.step"
LABEL = "Neck support ring, outer (parametric)"

DESIGNED = {"ring": "clearance"}
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    outer_r=124.5, bore_r=57.5, thickness=10.0,
    rim=(113.5, 2.0),          # raised rim: inner radius, height above the disc
    groove=(110.5, 1.0),       # groove inside the rim: inner radius, depth
    windows=(6, 72.5, 95.0, 16.0, 8.0),   # count, inner r, outer r, spoke width (deg), corner r
    bolt="M5", hole_sizes=HOLE_SIZES,
    hole_circles=((118.0, 12, 0.0), (104.0, 12, 0.0), (64.0, 8, 0.0)),   # radius, count, first angle (deg)
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import (BuildPart, BuildSketch, Circle, Cylinder, Locations, Mode, Plane, Pos, Rot,
                           Polygon, extrude, fillet)

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown neck_support_ring_outer parameters: {sorted(unknown)}")
    R, rb, t = P["outer_r"], P["bore_r"] + P["fit"] / 2, P["thickness"]
    rr, rh = P["rim"]
    gr, gd = P["groove"]
    n, wi, wo, spoke, cr = P["windows"]
    with BuildPart() as bp:
        with BuildSketch():
            Circle(R)
            Circle(rb, mode=Mode.SUBTRACT)
        extrude(amount=t)
        with BuildSketch(Plane.XY.offset(t)):
            Circle(R)
            Circle(rr, mode=Mode.SUBTRACT)
        extrude(amount=rh)
        with BuildSketch(Plane.XY.offset(t)):
            Circle(rr)
            Circle(gr, mode=Mode.SUBTRACT)
        extrude(amount=-gd, mode=Mode.SUBTRACT)
        # windows: annular sectors between the spokes (radial sides), corners rounded
        half = (360.0 / n - spoke) / 2
        for k in range(n):
            c = math.radians(360.0 * k / n + 180.0 / n)
            a0, a1 = c - math.radians(half), c + math.radians(half)
            with BuildSketch() as ws:
                Circle(wo)
                Circle(wi, mode=Mode.SUBTRACT)
                big = 2 * wo
                Polygon((0, 0), (big * math.cos(a0), big * math.sin(a0)), (big * math.cos(c), big * math.sin(c)),
                        (big * math.cos(a1), big * math.sin(a1)), align=None, mode=Mode.INTERSECT)
                fillet(ws.vertices(), cr)
            extrude(ws.sketch, amount=t, mode=Mode.SUBTRACT)
    body = bp.part
    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, t), (0, 0, 1))}
    hole_features(feats, "bore", (0, 0, 0), (0, 0, 1), rb, depth=t)
    hole_features(feats, "rim", (0, 0, t + rh), (0, 0, -1), rr, depth=rh + gd)
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    d = hole_d(P["bolt"], "clearance", P["fit"], P["hole_sizes"])
    i = 0
    for r, cnt, a0 in P["hole_circles"]:
        depth = t + rh if r > rr else t
        for k in range(cnt):
            a = math.radians(a0 + 360.0 * k / cnt)
            x, y = r * math.cos(a), r * math.sin(a)
            if types["ring"] != DESIGNED["ring"]:
                i += 1
                body = cut_hole(body, feats, f"h{i}", (x, y, 0), (0, 0, 1), P["bolt"], types["ring"], depth, P["fit"],
                                P.get("hole_sizes"), grow_boss=True)
                continue
            body -= Pos(x, y, depth / 2) * Cylinder(d / 2, depth + 0.02)
            i += 1
            hole_features(feats, f"h{i}", (x, y, 0), (0, 0, 1), d / 2, depth=depth, bolt=P["bolt"], kind="clearance")
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z = 0 on the bed)", "bottom", False, "no overhangs; 249 mm across: "
                                     "needs a 250 mm bed"))
