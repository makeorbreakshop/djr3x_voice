"""Hero-arm wrist (Anderson, `wrist`) - the cup the hand turns on, with the wrist servo across it.

A conical cup: 25.5 mm across the bottom rising at 9.82 deg to 30 at z = 26, then straight to a
38 mm top rim; a 2.5 mm wall and a 7 mm floor with a 25.5 mm bore. A bridge across the cup (30 wide,
top at z = 28.5, its underside a 40 deg funnel from a 31 mm flat) carries the micro servo in a
24 x 14 slot, its two mounting screws self-threading into 1.5 mm holes 2 mm beyond the slot's ends.

Frame = his STEP's: axis +Z, the floor z = 0 .. 7, the bridge along X, the servo slot x = -17.7 .. 6.3.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._sketch import profile

REFERENCE = "r3x-internal - wrist.step"
LABEL = "Hero-arm wrist (parametric)"
DESIGNED = {"servo": "tapped"}          # the micro servo's M2 self-tap into the bridge
INSERT_CANDIDATES = ()                  # 2.5 mm of bridge at one end: too thin for an insert

DEFAULTS = dict(
    fit=0.0,
    servo_bolt="M2",
    base_r=25.5, flare_z=26.0, top_r=30.0, height=38.0, wall=2.5,   # outer: r at z = 0, r at flare_z; the rim
    floor=7.0, bore_d=25.5,
    bridge=(15.0, 28.5, 15.5, 26.0, 16.0 / 19.0),   # half-width, top z, underside flat r, flat z, funnel dr/dz
    slot=(-17.7, 6.3, 7.0),                          # x from, x to, half-width
    servo_screws=(1.5, 2.0),                         # diameter, beyond the slot's ends
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Axis, Box, Cylinder, Plane, Pos, revolve

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown wrist parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    fit = P["fit"]
    r0, zf, R, H, w, fl = P["base_r"], P["flare_z"], P["top_r"], P["height"], P["wall"], P["floor"]
    k = (R - r0) / zf                                 # the flare: dr/dz
    rb = (P["bore_d"] + fit) / 2
    cup = revolve(Plane.XZ * profile([(rb, 0.0), (r0, 0.0), (R, zf), (R, H), (R - w, H), (R - w, zf),
                                      (R - w - k * (zf - fl), fl), (rb, fl)]), Axis.Z)
    hw, zt, rf, zfl, s = P["bridge"]
    reach = R - w + 5.0
    funnel = revolve(Plane.XZ * profile([(0.0, zfl), (rf, zfl), (reach, zfl - (reach - rf) / s), (reach, zt),
                                         (0.0, zt)]), Axis.Z)
    bridge = funnel & Pos(0, 0, zt / 2) * Box(2 * reach, 2 * hw, zt) & Pos(0, 0, zt / 2) * Cylinder(R - w, zt)
    body = cup + bridge
    x0, x1, sh = P["slot"]
    body -= Pos((x0 + x1) / 2, 0, (zt + fl) / 2 + 1) * Box(x1 - x0 + fit, 2 * sh + fit, zt - fl)
    feats: dict = {"bed": plane((0, 0, 0), (0, 0, -1)), "rim": plane((0, 0, H), (0, 0, 1)),
                   "floor": plane((0, 0, fl), (0, 0, 1)), "servo_seat": plane(((x0 + x1) / 2, 0, zt), (0, 0, 1)),
                   "axis": {"type": "axis", "p": [0.0, 0.0, H], "d": [0.0, 0.0, -1.0], "r": R - w}}
    hole_features(feats, "rim_bore", (0, 0, H), (0, 0, -1), R - w, depth=H - zf)   # the hand's collar turns in it
    hole_features(feats, "bore", (0, 0, 0), (0, 0, 1), rb, depth=fl)
    sd, beyond = P["servo_screws"]
    for i, x in enumerate((x0 - beyond, x1 + beyond)):
        r = abs(x)
        under = zfl if r <= rf else zfl - (r - rf) / s           # the bridge's underside at the screw
        depth = zt - under + (sd / 2) / s + 0.5                   # through the funnel underside
        if types["servo"] == DESIGNED["servo"]:
            body -= Pos(x, 0, zt - depth / 2 + 0.01) * Cylinder((sd + fit) / 2, depth + 0.02)
            hole_features(feats, f"servo_screw{i + 1}", (x, 0, zt), (0, 0, -1), (sd + fit) / 2, depth=zt - under,
                          bolt=P["servo_bolt"], kind="tapped")
        else:
            body = cut_hole(body, feats, f"servo_screw{i + 1}", (x, 0, zt), (0, 0, -1), P["servo_bolt"],
                            types["servo"], zt - under, fit)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("floor down (z = 0 on the bed)", "bed", True,
                                     "the bridge's funnel underside is 50 deg from vertical and its 31 mm flat "
                                     "spans the cup: support under the bridge"))
