"""Neck support platform (Anderson) - the plate that carries the neck's upper support on an MGN12H
carriage.

A flat plate: four M3 clearance holes on the carriage's 20 x 20 mm pattern, and at its far end a
shallow pocket with two M3 holes up through its floor (the part above bolts down into it).

Frame = the STEP's: plate z = 0 .. thickness, x = -width .. 0, y = y0 .. y0 + length.
"""

from __future__ import annotations

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES

REFERENCE = "r3x-internal - neck-support-platform.step"
LABEL = "Neck support platform (parametric)"

DEFAULTS = dict(
    fit=0.0,
    width=39.0, length=56.0, thickness=8.0, y0=7.0,
    bolt="M3", hole_sizes=HOLE_SIZES,
    carriage=((-19.5, 25.0), 20.0),       # MGN12H pattern: centre (x, y), square pitch
    pocket=((-36.0, -3.0), (44.5, 57.5), 2.0),   # x range, y range, depth from the top
    pocket_holes=((-30.0, 51.0), (-9.0, 51.0)),  # up through the pocket floor, from the underside
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown neck_support_platform parameters: {sorted(unknown)}")
    w, L, t, y0 = P["width"], P["length"], P["thickness"], P["y0"]
    body = Pos(-w / 2, y0 + L / 2, t / 2) * Box(w, L, t)
    (px0, px1), (py0, py1), pd = P["pocket"]
    body -= Pos((px0 + px1) / 2, (py0 + py1) / 2, t - pd / 2 + 0.01) * Box(px1 - px0, py1 - py0, pd + 0.02)
    d = hole_d(P["bolt"], "clearance", P["fit"], P["hole_sizes"])
    feats: dict = {"bottom": plane((-w / 2, y0 + L / 2, 0), (0, 0, -1)), "top": plane((-w / 2, y0 + L / 2, t), (0, 0, 1))}
    (cx, cy), pitch = P["carriage"]
    pts = sorted((cx + sx * pitch / 2, cy + sy * pitch / 2) for sx in (-1, 1) for sy in (-1, 1))
    for i, (x, y) in enumerate(pts):
        body -= Pos(x, y, t / 2) * Cylinder(d / 2, t + 0.02)
        hole_features(feats, f"carriage{i + 1}", (x, y, 0), (0, 0, 1), d / 2, depth=t, bolt=P["bolt"], kind="clearance")
    for i, (x, y) in enumerate(P["pocket_holes"]):
        body -= Pos(x, y, (t - pd) / 2) * Cylinder(d / 2, t - pd + 0.02)
        hole_features(feats, f"pocket{i + 1}", (x, y, 0), (0, 0, 1), d / 2, depth=t - pd, bolt=P["bolt"], kind="clearance")
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z = 0 on the bed)", "bottom", False, "a plate: no supports"))
