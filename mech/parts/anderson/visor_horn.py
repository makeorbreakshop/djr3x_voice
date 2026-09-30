"""Visor servo horn (Anderson, `visor-servo-horn`) - the printed lever on the visor servo's spline.

A 5 mm tapered arm: an r5 hub over the servo's spline (a 6 mm bore, 4 mm deep from the servo side)
and an r4 tip carrying the push rod's pin (3 mm hole) 25 mm out; straight flanks tangent to both.

Frame = his STEP / STL's: hub axis +Z at the origin, the servo side z = 0, the arm toward -X.
Features for the visor drive (the workbench's interface, id `visor_horn`): `spline` (the hub axis,
out of the servo side, 25T), `servo_face` (the hub's servo-side face), `tip` (the pin hole).
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from . import HOLE_SIZES

REFERENCE = "r3x-internal - visor-servo-horn.step"
LABEL = "Visor servo horn (parametric)"
DESIGNED = {"tip": "tapped"}   # 3.0 mm: the push rod's M3 screw threads into it (5 mm is too thin for an insert)
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    thickness=5.0, arm=25.0, hub_r=5.0, tip_r=4.0,
    spline=(6.0, 4.0, 25),       # bore diameter, depth from the servo side, teeth (the servo's spline)
    tip_d=3.0, tip_bolt="M3",
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Pos, extrude

    from ._sketch import profile

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown visor_horn parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED)
    fit, t, L, R, r = P["fit"], P["thickness"], P["arm"], P["hub_r"], P["tip_r"]
    a = math.asin((R - r) / L)                   # the flanks' tilt: tangent to both circles
    hub_t = (R * math.sin(a), R * math.cos(a))
    tip_t = (-L + r * math.sin(a), r * math.cos(a))
    face = profile([hub_t, ("arc", (0.0, 0.0)), (R, 0.0), ("arc", (0.0, 0.0)), (hub_t[0], -hub_t[1]),
                    (tip_t[0], -tip_t[1]), ("arc", (-L, 0.0)), (-L - r, 0.0), ("arc", (-L, 0.0)), tip_t])
    body = extrude(face, amount=t, dir=(0, 0, 1))
    sd, sdep, teeth = P["spline"]
    body -= Pos(0, 0, sdep / 2 - 0.01) * Cylinder((sd + fit) / 2, sdep + 0.02)
    feats: dict = {"servo_face": plane((0, 0, 0), (0, 0, -1)), "outer_face": plane((0, 0, t), (0, 0, 1)),
                   "spline": {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": (sd + fit) / 2,
                              "teeth": teeth, "depth": sdep}}
    hole_features(feats, "spline", (0, 0, 0), (0, 0, 1), (sd + fit) / 2, depth=sdep)
    if types["tip"] == DESIGNED["tip"]:
        body -= Pos(-L, 0, t / 2) * Cylinder((P["tip_d"] + fit) / 2, t + 0.02)
        hole_features(feats, "tip", (-L, 0, 0), (0, 0, 1), (P["tip_d"] + fit) / 2, depth=t, bolt=P["tip_bolt"],
                      kind="tapped")
    else:
        body = cut_hole(body, feats, "tip", (-L, 0, 0), (0, 0, 1), P["tip_bolt"], types["tip"], t, fit, HOLE_SIZES)
    feats["tip"] = dict(feats["hole_tip"])
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("servo face down (z = 0 on the bed)", "servo_face", False,
                                     "flat: no supports; the spline bore opens on the bed face"))
