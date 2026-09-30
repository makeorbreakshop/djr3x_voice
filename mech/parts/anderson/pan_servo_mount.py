"""Neck-rotation (pan) servo mount (Anderson, `neck-rotation-servo-mount`) - holds the pan servo
by its flange above two rails.

A 4 mm plate with the servo pocket (the case + 1 mm along its length) and four M3 holes on the
servo's flange pattern, over two full-length rails that lift it clear; four M3 holes down through
plate and rails fix it.

Frame = the STEP's: plate top z = thickness, underside z = 0, rails down to z = -rail_h; the servo's
long side along X.
"""

from __future__ import annotations

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES
from parts.head.servos import servo as servo_spec

REFERENCE = "r3x-internal - neck-rotation-servo-mount.step"
LABEL = "Pan servo mount (parametric)"

DEFAULTS = dict(
    fit=0.0,
    size=(70.0, 38.0), thickness=4.0,
    servo="ds3218", pocket_extra=(1.0, 0.0),      # case + (along, across)
    flange_pattern=(48.3, 10.1),                   # Anderson's as-drawn; None = the catalogue's
    rail=(9.0, 20.0),                              # rail width (Y, each side), height below the plate
    bolt="M3", hole_sizes=HOLE_SIZES,
    mount=(50.0, 30.0),                            # the four mount holes: pitch X x Y
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown pan_servo_mount parameters: {sorted(unknown)}")
    fit = P["fit"]
    sv = servo_spec(P["servo"])
    L, W = P["size"]
    t = P["thickness"]
    rw, rh = P["rail"]
    pl = sv["body_l"] + P["pocket_extra"][0] + 2 * fit
    pw = sv["body_w"] + P["pocket_extra"][1] + 2 * fit
    body = Pos(0, 0, t / 2) * Box(L, W, t)
    for s in (-1, 1):
        body += Pos(0, s * (W / 2 - rw / 2), -rh / 2) * Box(L, rw, rh)
    body -= Pos(0, 0, t / 2) * Box(pl, pw, t + 1)
    d = hole_d(P["bolt"], "clearance", fit, P["hole_sizes"])
    feats: dict = {"top": plane((0, 0, t), (0, 0, 1)), "feet": plane((0, 0, -rh), (0, 0, -1))}
    fp = P["flange_pattern"] or (sv["pattern_l"], sv["pattern_w"])
    mx, my = P["mount"]
    for i, (x, y) in enumerate(sorted((sx * mx / 2, sy * my / 2) for sx in (-1, 1) for sy in (-1, 1))):
        body -= Pos(x, y, (t - rh) / 2) * Cylinder(d / 2, t + rh + 0.02)
        hole_features(feats, f"mount{i + 1}", (x, y, t), (0, 0, -1), d / 2, depth=t + rh, bolt=P["bolt"], kind="clearance")
    for i, (x, y) in enumerate(sorted((sx * fp[0] / 2, sy * fp[1] / 2) for sx in (-1, 1) for sy in (-1, 1))):
        body -= Pos(x, y, t / 2) * Cylinder(d / 2, t + 0.02)
        hole_features(feats, f"servo{i + 1}", (x, y, t), (0, 0, -1), d / 2, depth=t, bolt=P["bolt"], kind="clearance")
    feats["pocket"] = {"type": "axis", "p": [0.0, 0.0, t], "d": [0.0, 0.0, -1.0], "r": 0.0, "size": [pl, pw]}
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("plate down (z = thickness on the bed), rails up", "top", False,
                                     "upside down the rails rise from the plate: no supports"))
