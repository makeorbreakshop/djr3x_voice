"""Visor stub axle (ours, new): a short length of Anderson's 12 mm D-profile visor axle
(visor-center-rod: 11.7 mm across, a flat 4.1 mm off centre), one per side. It turns in the
bracket's F6001ZZ bearing and carries our visor hub on its outboard end; the left one also
carries Anderson's rod tab inboard, on a section milled to 8 mm across two side flats with a
Ø3.5 cross hole for the tab's 3 mm pin - the same interface as the middle of his centre rod
(x 83.25..91.25 there), which his tab straddles with its D flat under the tab's bridge.

Design frame = the head frame; the axle runs along X from |x0| to |x1| on the visor axis.
`flat_dir` = (y, z) of the D flat's outward normal (the tab's lever direction at rest on the
driven side). `tab` = (|x| from, |x| to) of the 8 mm section, or None.
"""

from __future__ import annotations

import math

from ._common import Print, axis, finish, plane

REFERENCE = None
LABEL = "Visor stub axle (parametric)"

DEFAULTS = dict(side=1, axis_y=32.3, axis_z=0.9, x=(56.0, 93.0), r=5.85, flat=4.1, flat_dir=(-1.0, 0.0),
                tab=None, tab_across=8.0, pin_d=3.5)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    s = 1 if P["side"] >= 0 else -1
    x0, x1 = P["x"]
    L = x1 - x0
    xc = s * (x0 + x1) / 2
    ay, az = P["axis_y"], P["axis_z"]
    r = P["r"]
    fy, fz = P["flat_dir"]
    ang = math.degrees(math.atan2(fz, fy))  # Rot(ang, 0, 0) turns +Y onto the flat's normal
    rod = Pos(xc, ay, az) * Rot(0, 90, 0) * Cylinder(r, L)
    frame = Pos(0, ay, az) * Rot(ang, 0, 0)
    rod -= Pos(xc, 0, 0) * frame * Pos(0, P["flat"] + r, 0) * Box(L + 1, 2 * r, 2 * r + 1)
    feats = {"axis": axis((s * x0, ay, az), (s, 0, 0), r),
             "end_in": plane((s * x0, ay, az), (-s, 0, 0)), "end_out": plane((s * x1, ay, az), (s, 0, 0))}
    if P["tab"]:
        ta, tb = P["tab"]
        tl, tcx = tb - ta, s * (ta + tb) / 2
        w = P["tab_across"] / 2
        for side in (1, -1):  # the two side flats, perpendicular to the D flat
            rod -= Pos(tcx, 0, 0) * frame * Pos(0, 0, side * (w + r)) * Box(tl, 2 * r + 2, 2 * r)
        rod -= Pos(tcx, 0, 0) * frame * Cylinder(P["pin_d"] / 2, 2 * r + 2)  # the cross hole, across the side flats
        n = (0.0, -math.sin(math.radians(ang)), math.cos(math.radians(ang)))  # +Z turned by ang about X
        feats["tab_pin"] = axis((tcx, ay + n[1] * w, az + n[2] * w), (0.0, -n[1], -n[2]), P["pin_d"] / 2)
    return finish(rod, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("bought: 12 mm D-shaft, milled (or printed lying on its flat)", "flat", False,
                                     "Anderson prints his axle; a steel D-shaft is stiffer"))
