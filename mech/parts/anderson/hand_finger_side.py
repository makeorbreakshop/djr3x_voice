"""Hero-arm hand, finger side (Anderson, `hand-finger-side`) - the fork the fingers hinge in.

A 60 mm disc 6.2 thick (0.75 chamfer on its upper edge) on the arm side's top face, carrying two
6 mm cheeks 12.5 apart that rise 30 mm and taper (from 52.77 long to 20.77 across the top); every
free cheek edge has a 0.75 chamfer. Each cheek's outer face has a half-round boss (r5.5, 1.5 proud)
round a 5.5 mm blind pivot hole, 5 deep, for the finger. The disc takes the horn's centre screw
(6 mm), a 5 mm hole beside it, and the arm side's four 2 mm join holes (blind).

Frame = his STEP's: axis +Z, the disc z = 7.8 .. 14 (stacked on `hand_arm_side`), the cheeks at
x = +/-(6.25 .. 12.25), the pivot axis along X at y = -19.385, z = 22.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._sketch import profile

REFERENCE = "r3x-internal - hand-finger-side.step"
LABEL = "Hero-arm hand, finger side (parametric)"
DESIGNED = {"join": "tapped"}          # the arm side's M2 self-tap into the blind 2 mm holes
INSERT_CANDIDATES = ()                 # 4.2 mm deep: too shallow for an M2 insert

DEFAULTS = dict(
    fit=0.0,
    join_bolt="M2",
    radius=30.0, z0=7.8, disc_t=6.2, chamfer=0.75,
    cheek=(6.25, 6.0, 26.385, 24.0, 10.385, 44.0),   # inner x, thickness, half-length at the base, straight to z,
                                                      # half-length at the top, top z
    edge_chamfer=0.75,
    sharp_corner=True,                  # as drawn: the left cheek's outer back edge has no chamfer
    pivot=(-19.385, 22.0, 5.5, 1.5, 5.5, 5.0),       # y, z, boss r, boss proud, hole d, hole depth
    centre_d=6.0, side_hole=(-18.152, 5.0),          # the second disc hole: y, diameter
    join=(4, 25.25, 2.0, 4.2),                        # count, radius, diameter, depth
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Align, Box, Cone, Cylinder, Plane, Pos, chamfer, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown hand_finger_side parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    fit, Rd, z0, T, c = P["fit"], P["radius"], P["z0"], P["disc_t"], P["chamfer"]
    zt = z0 + T
    body = (Pos(0, 0, z0) * Cylinder(Rd, T - c, align=(Align.CENTER, Align.CENTER, Align.MIN))
            + Pos(0, 0, zt - c) * Cone(Rd, Rd - c, c, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    xi, ct, hb, zs, ht, ztop = P["cheek"]
    py, pz, br, bp, hd, hdep = P["pivot"]
    ec = P["edge_chamfer"]
    for s in (1, -1):
        prof = profile([(-hb, zt), (hb, zt), (hb, zs), (ht, ztop), (-ht, ztop), (-hb, zs)])
        x_start = xi if s > 0 else -xi - ct
        cheek = extrude(Plane.YZ.offset(x_start) * prof, amount=ct)
        def along_x(e, y, z):
            return (e.geom_type.name == "LINE" and abs(e.tangent_at(0.5).X) > 0.99
                    and abs(abs(e.center().Y) - y) < 1e-6 and abs(e.center().Z - z) < 1e-6)

        xo_ = s * (xi + ct)
        free = [e for e in cheek.edges()
                if e.center().Z > zt + 1e-6                                          # not on the disc
                and not along_x(e, hb, zs) and not along_x(e, ht, ztop)              # the two creases
                and not (P["sharp_corner"] and s < 0 and abs(e.center().X - xo_) < 1e-6
                         and abs(e.center().Y + hb) < 1e-6)]                         # as drawn: one left sharp
        cheek = chamfer(free, ec)
        # the slope / top crease: the same chamfer measured along the bisector (his: legs ec * tan(half-angle))
        slope_n = math.atan2(hb - ht, ztop - zs)                                     # the slope lean from vertical
        cheek = chamfer([e for e in cheek.edges() if along_x(e, ht, ztop)], ec * math.tan((math.pi / 2 - slope_n) / 2))
        xo = s * (xi + ct)
        boss = (Plane(origin=(xo, py, pz), z_dir=(s, 0, 0)).location
                * Cylinder(br, bp, align=(Align.CENTER, Align.CENTER, Align.MIN)))
        block = Pos(xo + s * bp / 2, py, (zt + pz) / 2) * Box(bp, 2 * br, pz - zt)   # the boss down to the disc
        body = body + cheek + boss + block
    feats: dict = {"bed": plane((0, 0, z0), (0, 0, -1)), "disc_top": plane((0, 0, zt), (0, 0, 1))}
    for s, side in ((1, "l"), (-1, "r")):
        xo = s * (xi + ct + bp)
        body -= (Plane(origin=(xo + s * 0.01, py, pz), z_dir=(-s, 0, 0)).location
                 * Cylinder((hd + fit) / 2, hdep + 0.01, align=(Align.CENTER, Align.CENTER, Align.MIN)))
        hole_features(feats, f"pivot_{side}", (xo, py, pz), (-s, 0, 0), (hd + fit) / 2, depth=hdep)
    feats["pivot"] = {"type": "axis", "p": [0.0, py, pz], "d": [1.0, 0.0, 0.0], "r": (hd + fit) / 2}
    body -= Pos(0, 0, z0 + T / 2) * Cylinder((P["centre_d"] + fit) / 2, T + 0.02)
    hole_features(feats, "centre", (0, 0, z0), (0, 0, 1), (P["centre_d"] + fit) / 2, depth=T)
    sy, sd = P["side_hole"]
    body -= Pos(0, sy, z0 + T / 2) * Cylinder((sd + fit) / 2, T + 0.02)
    hole_features(feats, "side", (0, sy, z0), (0, 0, 1), (sd + fit) / 2, depth=T)
    n, jr, jd, jdep = P["join"]
    for k in range(n):
        a = 2 * math.pi * k / n
        x, y = jr * math.cos(a), jr * math.sin(a)
        if types["join"] == DESIGNED["join"]:
            body -= Pos(x, y, z0 - 0.01) * Cylinder((jd + fit) / 2, jdep + 0.01, align=(Align.CENTER, Align.CENTER, Align.MIN))
            hole_features(feats, f"join{k + 1}", (x, y, z0), (0, 0, 1), (jd + fit) / 2, depth=jdep,
                          bolt=P["join_bolt"], kind="tapped")
        else:
            body = cut_hole(body, feats, f"join{k + 1}", (x, y, z0), (0, 0, 1), P["join_bolt"], types["join"], jdep, fit)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("disc down (z = 7.8 on the bed)", "bed", False,
                                     "the cheeks' slopes are 39 deg from vertical and the pivot holes horizontal; "
                                     "no supports"))
