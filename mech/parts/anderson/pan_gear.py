"""Head-pan servo gear (Anderson, `head-rotate-servo-gea`) - the pinion on the pan servo.

15 tapered teeth with round tips on a 20 mm root, 10 mm thick, bolted to the servo's cross horn: an
M3 centre hole and four M3 on a 14 mm cross.

Frame = the STEP's: axis +Z, z = 0 .. thickness, a tooth on +Y.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._gear import round_tip_tooth, spur

REFERENCE = "r3x-internal - head-rotate-servo-gea.step"
LABEL = "Head-pan servo gear (parametric)"

DESIGNED = {"horn": "clearance"}        # the screws go through into the servo's metal horn
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    bolt="M3",
    teeth=15, root_r=20.0, thickness=10.0,
    tooth=(3.3461, 2.0, 26.0),          # half-width on the root circle, tip radius, tip centre radius
    phase=90.0,                          # a tooth on +Y
    hole_d=3.5,                          # M3 clearance: centre and the horn's cross
    horn_pattern=(7.0, 4, 0.0),          # radius, count, first angle
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Pos, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown pan_gear parameters: {sorted(unknown)}")
    t = P["thickness"]
    rh, tr, tc = P["tooth"]
    face = spur(P["teeth"], P["root_r"], round_tip_tooth(rh, P["root_r"], tr, tc, P["root_r"] - 2.0), P["phase"])
    body = extrude(face, amount=t)
    d = P["hole_d"] + P["fit"]
    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, t), (0, 0, 1))}
    body -= Pos(0, 0, t / 2) * Cylinder(d / 2, t + 0.02)
    hole_features(feats, "centre", (0, 0, 0), (0, 0, 1), d / 2, depth=t)
    r, n, a0 = P["horn_pattern"]
    ht = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)["horn"]
    for k in range(n):
        a = math.radians(a0 + 360.0 * k / n)
        x, y = r * math.cos(a), r * math.sin(a)
        if ht != "clearance":
            body = cut_hole(body, feats, f"horn{k + 1}", (x, y, t), (0, 0, -1), P["bolt"], ht, t, P["fit"])
            continue
        body -= Pos(x, y, t / 2) * Cylinder(d / 2, t + 0.02)
        hole_features(feats, f"horn{k + 1}", (x, y, 0), (0, 0, 1), d / 2, depth=t)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z = 0 on the bed)", "bottom", False, "a plate gear: no supports"))
