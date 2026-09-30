"""Hero-arm servo mount (Anderson, `servomount`) - the elbow's C-channel, holding the body tube.

A 50 mm-wide channel extruded along X: a 7 mm floor, a 4 mm front wall (5.2 x 6.5 bevel into a
2.8 mm top at z = 41, a 3 mm pad in its middle 28 mm with a 2 mm triangular recess) and a 5 mm back
wall with a 3 mm lip over the top, r10 / r14 inside and r4 outside. Behind the back wall a 13 mm
collar (33 mm bore) takes the body tube's end, which four M3 on its 10 mm square pull against the
wall through 3 mm holes round a 6 mm centre hole.

Frame = his STEP's: x = 0 .. 50, the floor z = 0 .. 7, the back wall y = -62 .. -57, the collar on
the axis (x, z) = (25, 27) along -Y.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from . import HOLE_SIZES
from ._sketch import profile

REFERENCE = "r3x-internal - servomount.step"
LABEL = "Hero-arm servo mount (parametric)"
DESIGNED = {"tube": "clearance"}        # 3.0 as drawn: the tube's M3 pass through into its inserts
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    tube_bolt="M3",
    width=50.0,
    floor=(7.0, 6.0),                        # thickness, front face y
    front=(-2.0, 34.5, 3.2, 41.0),           # inner face y, bevel from z; bevel to y, top z
    back=(-62.0, -57.0, 54.0, 3.0, -32.0),   # outer y, inner y, top z, lip thickness, lip end y
    rounds=(10.0, 14.0, 4.0),                # floor corners, lip corner, outside corners
    pad=(11.0, 39.0, 9.0),                   # x from, x to, face y
    recess=(((16.688, 26.439), (33.312, 26.439), (25.0, 13.667)), 2.0, 2.0),   # corner centres (x, z), r, depth
    collar=((25.0, 27.0), 21.0, 16.5, 13.0), # axis (x, z), outer r, bore r, length
    tube_holes=(10.0, 3.0, 6.0),             # square, hole d, centre d
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Align, BuildSketch, Cylinder, Kind, Plane, Polygon, Pos, Rot, extrude, offset

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown servomount parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    fit, W = P["fit"], P["width"]
    ft, fy = P["floor"]
    yi, zb, yb, zt = P["front"]
    yo, ybi, ztop, lip, ylip = P["back"]
    r1, r2, ro = P["rounds"]
    # the channel's section in (y, z); Plane.YZ maps sketch (u, v) to (y, z) and extrudes along +X
    sec = profile([(fy - ro, 0.0), (yo + ro, 0.0), ("arc", (yo + ro, ro)), (yo, ro), (yo, ztop - ro),
                   ("arc", (yo + ro, ztop - ro)), (yo + ro, ztop), (ylip, ztop), (ylip, ztop - lip),
                   (ybi + r2, ztop - lip), ("arc", (ybi + r2, ztop - lip - r2)), (ybi, ztop - lip - r2),
                   (ybi, ft + r1), ("arc", (ybi + r1, ft + r1)), (ybi + r1, ft), (yi - r1, ft), ("arc", (yi - r1, ft + r1)),
                   (yi, ft + r1), (yi, zb), (yb, zt), (fy, zt), (fy, ro), ("arc", (fy - ro, ro))])
    body = extrude(Plane.YZ * sec, amount=W, dir=(1, 0, 0))
    x0, x1, py = P["pad"]
    pad = profile([(yi, 0.0), (py - ro, 0.0), ("arc", (py - ro, ro)), (py, ro), (py, zt), (yb, zt), (yi, zb)])
    body += extrude(Plane.YZ.offset(x0) * pad, amount=x1 - x0, dir=(1, 0, 0))
    corners, rr, rdep = P["recess"]
    with BuildSketch(Plane.XZ.offset(-py)) as tri:       # Plane.XZ: (u, v) = (x, z), normal -Y
        Polygon(*corners, align=None)
        offset(amount=rr, kind=Kind.ARC)
    body -= extrude(tri.sketch, amount=rdep + 0.01, dir=(0, -1, 0)).moved(Pos(0, 0.01, 0))
    (cx, cz), cr, cb, cl = P["collar"]
    body += Pos(cx, yo - cl / 2, cz) * Rot(90, 0, 0) * (Cylinder(cr, cl) - Cylinder(cb, cl))
    feats: dict = {"bed": plane((W / 2, (yo + fy) / 2, 0), (0, 0, -1)),
                   "floor_top": plane((W / 2, (ybi + yi) / 2, ft), (0, 0, 1)),
                   "back_wall": plane((cx, ybi, cz), (0, 1, 0)),
                   "collar_end": plane((cx, yo - cl, cz), (0, -1, 0)),
                   "front_top": plane((W / 2, (yb + fy) / 2, zt), (0, 0, 1))}
    hole_features(feats, "collar", (cx, yo - cl, cz), (0, 1, 0), cb, depth=cl)
    feats["tube_axis"] = {"type": "axis", "p": [cx, yo - cl, cz], "d": [0.0, 1.0, 0.0], "r": cb}
    sq, hd, cd = P["tube_holes"]
    wall = ybi - yo
    body -= Pos(cx, (yo + ybi) / 2, cz) * Rot(90, 0, 0) * Cylinder((cd + fit) / 2, wall + 0.02)
    hole_features(feats, "centre", (cx, ybi, cz), (0, -1, 0), (cd + fit) / 2, depth=wall)
    k = 0
    for dx in (-sq / 2, sq / 2):
        for dz in (-sq / 2, sq / 2):
            k += 1
            e = (cx + dx, ybi, cz + dz)
            if types["tube"] == DESIGNED["tube"]:
                body -= Pos(e[0], (yo + ybi) / 2, e[2]) * Rot(90, 0, 0) * Cylinder((hd + fit) / 2, wall + 0.02)
                hole_features(feats, f"tube{k}", e, (0, -1, 0), (hd + fit) / 2, depth=wall, bolt=P["tube_bolt"],
                              kind="clearance")
            else:
                body = cut_hole(body, feats, f"tube{k}", e, (0, -1, 0), P["tube_bolt"], types["tube"], wall, fit,
                                HOLE_SIZES)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("on its side (x = 0 on the bed)", "bed", False,
                                     "on its side the channel section prints in-plane and the collar stands "
                                     "horizontal (a 42 mm bridge-free circle); no supports"))
