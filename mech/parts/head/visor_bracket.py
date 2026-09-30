"""Visor bearing bracket (ours, new): carries one F6001ZZ flanged bearing on the kit's visor
axis, standing on Hunter's mount-plate flange so the visor rides the head inside tilt and roll.

Anderson's visor (r3x - visor animation) turns on one 12 mm axle through the head's centre; on
Hunter's head the fixed hex post occupies that centre, so the axle is split into two stub
axles, each in one of these brackets (the driven one also carries the lever). The foot is
clamped by the plate's existing M4 screws into the head bottom's inserts (no new holes in the
plate): it has clearance holes on the plate's flange-hole pattern.

Design frame = the head frame (Hunter's gimbal centre, +Y up, +Z forward, +X the droid's left).
`side` = +1 (left, +X) or -1 (right). There is no reference file: this part is ours.
"""

from __future__ import annotations

from ._common import HOLES, Print, finish, hole_features, plane

REFERENCE = None
LABEL = "Visor bearing bracket (parametric)"

DEFAULTS = dict(
    side=1,
    axis_y=32.3,            # the kit visor axis (body y 770.6) in the head frame
    axis_z=0.9,
    upright_x=(64.0, 72.0),  # |x| of the upright (the bearing's width 8 mm), clear of the screw heads
    foot_x=(54.0, 72.0),     # |x| of the foot on the plate flange
    foot_z=(-34.0, 35.0),
    foot_y=0.85,             # the plate flange's top face (head frame)
    foot_t=4.0,
    upright_half_z=18.0,
    above_axis=17.0,
    bearing_od=28.0, bearing_flange_od=30.5, bearing_flange_t=1.5, fit=0.2,
    screws=((60.0, -28.47), (60.0, 0.27), (60.0, 29.01)),  # the plate's flange holes (x, z)
    bolt="M4",
)


def resolve(params, kw):
    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    return P


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot

    P = resolve(params, kw)
    s = 1 if P["side"] >= 0 else -1
    ux0, ux1 = P["upright_x"]
    fx0, fx1 = P["foot_x"]
    fz0, fz1 = P["foot_z"]
    y0, t = P["foot_y"], P["foot_t"]
    ay, az = P["axis_y"], P["axis_z"]

    def box(x0, x1, ya, yb, za, zb):
        xa, xb = sorted((s * x0, s * x1))
        return Pos((xa + xb) / 2, (ya + yb) / 2, (za + zb) / 2) * Box(xb - xa, yb - ya, zb - za)

    part = box(fx0, fx1, y0, y0 + t, fz0, fz1)
    part += box(ux0, ux1, y0, ay + P["above_axis"], az - P["upright_half_z"], az + P["upright_half_z"])
    # the bearing: a through bore for its body, a counterbore for its flange on the outboard face
    w = ux1 - ux0
    xc = s * (ux0 + ux1) / 2
    part -= Pos(xc, ay, az) * Rot(0, 90, 0) * Cylinder((P["bearing_od"] + P["fit"]) / 2, w + 1)
    part -= Pos(s * (ux1 - P["bearing_flange_t"] / 2), ay, az) * Rot(0, 90, 0) * Cylinder(
        (P["bearing_flange_od"] + 2 * P["fit"]) / 2, P["bearing_flange_t"] + 0.01)
    feats: dict = {}
    d_clear = HOLES[P["bolt"]]["clearance"]
    for i, (x, z) in enumerate(P["screws"]):
        part -= Pos(s * x, y0 + t / 2, z) * Rot(90, 0, 0) * Cylinder(d_clear / 2, t + 1)
        hole_features(feats, f"screw{i + 1}", (s * x, y0 + t, z), (0, -1, 0), d_clear / 2, depth=t,
                      bolt=P["bolt"], kind="clearance")
    feats["bearing_seat"] = {"type": "axis", "p": [s * ux1, ay, az], "d": [-s, 0.0, 0.0], "r": P["bearing_od"] / 2}
    feats["bearing_face"] = plane((s * (ux1 - P["bearing_flange_t"]), ay, az), (s, 0, 0))
    feats["face_bottom"] = plane((s * (fx0 + fx1) / 2, y0, (fz0 + fz1) / 2), (0, -1, 0))
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("foot on the bed", "face_bottom", False,
                                     "the upright prints vertical; the bearing counterbore faces up"))
