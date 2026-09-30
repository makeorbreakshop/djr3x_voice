"""Base servo top (Anderson, `base-servo-top`) - the cap plate over the base's servo, split by a
slot so it clamps.

A 9.5 mm plate: a slot in from its top edge, two counterbored M3 clamp screws in from its bottom
edge either side of it, four M3 holes down through it, a chamfered corner (r5 rounds) and a round
on the other top corner.

Frame = the STEP's (in the base's coordinates): plate z = z0 .. z0 + thickness.
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES
from ._sketch import profile

REFERENCE = "r3x-internal - base-servo-top.step"
LABEL = "Base servo top (parametric)"

DEFAULTS = dict(
    fit=0.0,
    x=(-88.0, -33.5), y=(34.183, 75.183), z0=85.0, thickness=9.5,
    chamfer=(-76.0, 65.183),        # the top-left corner cut: from (x, top) to (left, y)
    corner_r=5.0,
    top_right_round=(-37.263, 70.183),   # centre of the top-right round (cut by the right edge)
    slot=(-65.75, -55.75, 50.183),  # from the top edge: x from, x to, down to y
    bolt="M3", hole_sizes=HOLE_SIZES,
    holes=((-82.25, 39.683), (-82.25, 59.683), (-39.25, 39.683), (-39.25, 59.683)),
    clamp=((-69.25, -52.25), 89.75, 4.5, 4.5, 16.5),   # clamp screws from the bottom edge: x, z, head bore
                                                       # diameter and depth, total depth
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Plane, Pos, Rot, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown base_servo_top parameters: {sorted(unknown)}")
    fit = P["fit"]
    (x0, x1), (y0, y1) = P["x"], P["y"]
    cx, cy = P["chamfer"]
    r = P["corner_r"]
    sx0, sx1, sy = P["slot"]
    # rounds on the chamfer's two ends: tangent points from the chamfer line's normal
    n = (-(y1 - cy), cx - x0)
    ln = math.hypot(*n)
    n = (n[0] / ln, n[1] / ln)                     # outward normal of the chamfer
    d = (cx - x0, y1 - cy)
    d = (d[0] / math.hypot(*d), d[1] / math.hypot(*d))
    # fillet centres: r inside both edges at each corner
    def corner_centre(p_line, dirn, edge_pt, edge_n):
        # centre = point at distance r inside the chamfer and inside the other edge
        # chamfer inside offset line: p + t*d - r*n ; other edge: dot(q - edge_pt, edge_n) = -r
        px, py = p_line[0] - r * n[0], p_line[1] - r * n[1]
        t = (-r - ((px - edge_pt[0]) * edge_n[0] + (py - edge_pt[1]) * edge_n[1])) / (d[0] * edge_n[0] + d[1] * edge_n[1])
        return (px + t * d[0], py + t * d[1])
    c_top = corner_centre((cx, y1), d, (cx, y1), (0.0, 1.0))
    c_left = corner_centre((cx, y1), d, (x0, cy), (-1.0, 0.0))
    tr = P["top_right_round"]
    ry = tr[1] + math.sqrt(r ** 2 - (x1 - tr[0]) ** 2)
    face = profile([(x0, y0), (x1, y0), (x1, ry), ("arc", tr), (tr[0], y1), (sx1, y1), (sx1, sy), (sx0, sy), (sx0, y1),
                    (c_top[0], y1), ("arc", c_top), (c_top[0] + r * n[0], c_top[1] + r * n[1]),
                    (c_left[0] + r * n[0], c_left[1] + r * n[1]), ("arc", c_left), (x0, c_left[1])])
    z0, t = P["z0"], P["thickness"]
    body = extrude(Plane.XY.offset(z0) * face, amount=t)
    dh = hole_d(P["bolt"], "clearance", fit, P["hole_sizes"])
    feats: dict = {"bottom": plane(((x0 + x1) / 2, (y0 + y1) / 2, z0), (0, 0, -1)),
                   "top": plane(((x0 + x1) / 2, (y0 + y1) / 2, z0 + t), (0, 0, 1))}
    for i, (x, y) in enumerate(P["holes"]):
        body -= Pos(x, y, z0 + t / 2) * Cylinder(dh / 2, t + 0.02)
        hole_features(feats, f"h{i + 1}", (x, y, z0 + t), (0, 0, -1), dh / 2, depth=t, bolt=P["bolt"], kind="clearance")
    xs, cz, cbd, cbdep, dep = P["clamp"]
    for i, x in enumerate(xs):
        body -= Pos(x, y0 + dep / 2, cz) * Rot(90, 0, 0) * Cylinder(dh / 2, dep + 0.02)
        body -= Pos(x, y0 + cbdep / 2, cz) * Rot(90, 0, 0) * Cylinder((cbd + fit) / 2, cbdep + 0.02)
        hole_features(feats, f"clamp{i + 1}", (x, y0 + cbdep, cz), (0, 1, 0), dh / 2, depth=dep - cbdep,
                      bolt=P["bolt"], kind="clearance")
        hole_features(feats, f"clamp_head{i + 1}", (x, y0, cz), (0, 1, 0), (cbd + fit) / 2, depth=cbdep)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z0 on the bed)", "bottom", False,
                                     "the clamp holes print horizontally; no supports"))
