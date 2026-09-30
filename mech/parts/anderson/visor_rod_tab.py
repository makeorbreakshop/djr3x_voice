"""Visor rod tab (Anderson, `visor-rod-tab`) - the lever clamped on the visor axle's flat.

A 7.75 mm-thick lever: two jaws either side of an 8.5 mm U-slot that takes the axle's 8 mm
across-flats section (the flat under the bridge), a 3.5 mm cross pin through both jaws on the
axle's axis, the jaws' outer faces tapering in to an r5 tip with the push rod's 3.5 mm pin hole.

Frame = his STEP / STL's: the lever along +Z, the axle along Y (the tab y = -thickness .. 0), the
slot opening toward -Z. Features (the workbench's `visor_tab`): `socket` (the axle axis, 1.5 mm
behind the origin along the lever), `tip` (19.5 mm from it), `cross` (the cross pin).
"""

from __future__ import annotations

import math

from parts.head._common import (Print, cut_hole, finish, hole_features, hole_group_params, lock_nut, plane,
                                resolve_hole_types)

from . import HOLE_SIZES

REFERENCE = "r3x-internal - visor-rod-tab.step"
LABEL = "Visor rod tab (parametric)"
# M3 (his 3.5 mm clearance). `tip` is the push rod's pivot: already a through-bolt, it gets a lock nut.
DESIGNED = {"tip": "clearance", "cross": "clearance"}
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    thickness=7.75, half_w=8.75, bottom=-6.0, corner_r=1.5,
    slot=(8.5, 2.5),                 # width, bridge underside z (the axle's flat bears on it)
    taper_from=7.5, taper_angle=19.65,  # sides straight to z, then leaning in by the angle to the tip circle
    tip=(18.0, 5.0),                 # the tip circle: centre z, radius
    socket_z=-1.5,                   # the axle's axis (8 mm across flats: 4 below the bridge)
    cross=(3.5, -1.637),             # cross pin: diameter, z (as drawn; the socket axis is at -1.5)
    tip_d=3.5, bolt="M3",
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Plane, Pos, Rot, extrude

    from ._sketch import profile

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown visor_rod_tab parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED)
    fit, t, W, zb, r = P["fit"], P["thickness"], P["half_w"], P["bottom"], P["corner_r"]
    sw, sz = P["slot"]
    sw += fit
    tz, tr = P["tip"]
    zt = P["taper_from"]
    # the tapered side: from (W, zt) toward the tip, meeting the tip circle where it first crosses it
    ux, uz = -math.sin(math.radians(P["taper_angle"])), math.cos(math.radians(P["taper_angle"]))
    b = ux * (W - 0) + uz * (zt - tz)
    s_hit = -b - math.sqrt(max(b * b - (W ** 2 + (zt - tz) ** 2 - tr ** 2), 0.0))
    hx, hz = W + ux * s_hit, zt + uz * s_hit
    # the profile in the XZ plane (u = x, v = z), extruded along -Y
    face = profile([(-sw / 2, zb), (-sw / 2, sz), (sw / 2, sz), (sw / 2, zb), (W - r, zb), ("arc", (W - r, zb + r)),
                    (W, zb + r), (W, zt), (hx, hz), ("arc", (0.0, tz)), (0.0, tz + tr), ("arc", (0.0, tz)), (-hx, hz),
                    (-W, zt), (-W, zb + r), ("arc", (-W + r, zb + r)), (-W + r, zb)])
    body = extrude(Plane.XZ * face, amount=t)
    feats: dict = {"face_0": plane((0, 0, tz / 2), (0, 1, 0)), "face_1": plane((0, -t, tz / 2), (0, -1, 0)),
                   "bridge": plane((0, -t / 2, sz), (0, 0, -1)),
                   "socket": {"type": "axis", "p": [0.0, 0.0, P["socket_z"]], "d": [0.0, -1.0, 0.0], "r": sw / 2}}
    d = P["tip_d"] + fit
    if types["tip"] == DESIGNED["tip"]:
        body -= Pos(0, -t / 2, tz) * Rot(90, 0, 0) * Cylinder(d / 2, t + 0.02)
        hole_features(feats, "tip", (0, 0, tz), (0, -1, 0), d / 2, depth=t, bolt=P["bolt"], kind="clearance")
    else:
        body = cut_hole(body, feats, "tip", (0, 0, tz), (0, -1, 0), P["bolt"], types["tip"], t, fit, HOLE_SIZES)
    if types["tip"] == "clearance":         # the push rod's pivot: a through-bolt with a lock nut
        lock_nut(feats, "tip", (0, -t, tz), (0, -1, 0), P["bolt"])
    feats["tip"] = dict(feats["hole_tip"])
    cd, cz = P["cross"]
    if types["cross"] == DESIGNED["cross"]:
        body -= Pos(0, -t / 2, cz) * Rot(0, 90, 0) * Cylinder((cd + fit) / 2, 2 * W + 0.02)
        hole_features(feats, "cross", (-W, -t / 2, cz), (1, 0, 0), (cd + fit) / 2, depth=2 * W, bolt=P["bolt"],
                      kind="clearance")
    else:   # through both jaws; a nut trap / insert lands in the far (+x) jaw
        body = cut_hole(body, feats, "cross", (-W, -t / 2, cz), (1, 0, 0), P["bolt"], types["cross"], 2 * W, fit,
                        HOLE_SIZES)
    feats["cross"] = dict(feats["hole_cross"])
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (a 7.75 mm face on the bed)", "face_0", False,
                                     "flat: the cross pin prints as a horizontal hole; no supports"))
