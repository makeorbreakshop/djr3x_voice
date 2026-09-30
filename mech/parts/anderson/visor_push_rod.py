"""Visor push rod (Anderson, `visor-push-rod`) - the link from the horn's tip to the rod tab.

A 5 mm-thick stadium: two r3.5 ends 58 mm apart, a 3.5 mm pin hole through each.

Frame = his STEP / STL's: the pin axes along Y (the rod y = -thickness .. 0), pin_a at the
origin, pin_b toward -X. Features (the workbench's `visor_push_rod`): `pin_a`, `pin_b`.
"""

from __future__ import annotations

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from . import HOLE_SIZES

REFERENCE = "r3x-internal - visor-push-rod.step"
LABEL = "Visor push rod (parametric)"
DESIGNED = {"pin": "clearance"}   # M3 pivots (his 3.5 mm M3 clearance)
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    centres=58.0, end_r=3.5, thickness=5.0, pin_d=3.5, pin_bolt="M3",
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import BuildSketch, Cylinder, Locations, Plane, Pos, Rot, SlotCenterToCenter, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown visor_push_rod parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED)
    c, r, t = P["centres"], P["end_r"], P["thickness"]
    d = P["pin_d"] + P["fit"]
    with BuildSketch(Plane.XZ) as sk:                     # local (x, z), normal -Y: extrude toward -Y
        with Locations((-c / 2, 0)):
            SlotCenterToCenter(c, 2 * r)
    body = extrude(sk.sketch, amount=t)
    feats: dict = {"face_0": plane((-c / 2, 0, 0), (0, 1, 0)), "face_1": plane((-c / 2, -t, 0), (0, -1, 0))}
    for name, x in (("pin_a", 0.0), ("pin_b", -c)):
        if types["pin"] == DESIGNED["pin"]:
            body -= Pos(x, -t / 2, 0) * Rot(90, 0, 0) * Cylinder(d / 2, t + 0.02)
            hole_features(feats, name, (x, 0, 0), (0, -1, 0), d / 2, depth=t, bolt=P["pin_bolt"], kind="clearance")
        else:
            body = cut_hole(body, feats, name, (x, 0, 0), (0, -1, 0), P["pin_bolt"], types["pin"], t, P["fit"],
                            HOLE_SIZES)
        feats[name] = dict(feats[f"hole_{name}"])
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (a 5 mm face on the bed)", "face_0", False, "flat: no supports"))
