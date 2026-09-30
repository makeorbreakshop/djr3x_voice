"""Base centre (Anderson, `base-center`) - the pan base: a disc on the neck tube carrying the pan
servo's housing and the tube clamp channel.

- disc: 10 mm, a bore for the neck tube with a keyway, four M5 on a circle, and a window for the
  pan servo's case framed by a 2 mm lip with the servo mount's 50 x 30 M3 pattern (counterbored);
- servo housing: `base_servo_top`'s outline (without its slot) standing to the servo top's seat, a
  pocket open to the front for the servo, two counterbored M3 clamp screws under the pocket floor
  and four blind M3 from the top for the servo top;
- channel: two legs and a bridge round the keyway, up to the top, blended into the housing (r8,
  r5) and cut back at 59 deg over the housing; four M5 through the bridge.

Frame = the STEP's: axis +Z at the origin, disc underside z = 0.
"""

from __future__ import annotations

import math

from parts.head._common import (Print, cut_hole, finish, hole_d, hole_features, hole_group_params, plane,
                                resolve_hole_types)

from . import HOLE_SIZES
from . import base_servo_top as _top

REFERENCE = "r3x-internal - base-center.step"
LABEL = "Base centre (parametric)"

DESIGNED = {"top": "clearance", "clamp": "clearance", "disc": "clearance", "bridge": "clearance",
            "servo_mount": "clearance"}
INSERT_CANDIDATES = ("top",)             # base_servo_top's four screws thread into the housing

DEFAULTS = dict(
    fit=0.0,
    disc=(110.0, 10.0), bore_r=42.5, key=(21.0, 62.0),           # disc radius, thickness; keyway width, reach (y)
    disc_holes=(104.0, 4, 37.0),                                  # M5: radius, count, first angle
    window=((-32.5, 32.5), (-91.0, -69.0)),                       # servo case window
    frame=((-35.0, 35.0), (-99.0, -61.0), 2.0),                   # its lip: x, y, height above the disc
    servo_mount=((0.0, -80.0), (50.0, 30.0), 4.0),                # M3 pattern centre, pitch, counterbore depth
    housing_top=85.0, pocket=((-76.0, -45.5), 65.183, 19.5),      # pocket x range, back wall y, floor z
    clamp=((-69.25, -52.25), 14.75, 4.5, 29.5),                   # under the floor: x, z, head bore depth, total depth
    top_holes=(16.5,),                                            # blind M3 from the top: total depth
    channel=((-10.5, 10.5), 41.183, 62.0, 70.0, 18.5, 110.0),     # keyway x, legs' front y, bridge y0, y1, right leg x1, top z
    blends=(8.0, 5.0),                                            # concave blends: housing front / back
    slope=(59.03, -33.5),                                         # the cut over the housing: angle from horizontal, from x at the housing top
    bridge_holes=(23.0, 25.0, 4),                                 # M5 along Y at x = 0: first z, pitch, count
    bolt3="M3", bolt5="M5", hole_sizes=HOLE_SIZES,
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Plane, Pos, Rot, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown base_center parameters: {sorted(unknown)}")
    fit = P["fit"]
    R, T = P["disc"]
    d3 = hole_d(P["bolt3"], "clearance", fit, P["hole_sizes"])
    d5 = hole_d(P["bolt5"], "clearance", fit, P["hole_sizes"])

    def box(xa, xb, ya, yb, za, zb):
        return Pos((xa + xb) / 2, (ya + yb) / 2, (za + zb) / 2) * Box(xb - xa, yb - ya, zb - za)

    # ---- disc
    body = Pos(0, 0, T / 2) * Cylinder(R, T)
    (fx0, fx1), (fy0, fy1), fh = P["frame"]
    body += box(fx0, fx1, fy0, fy1, T, T + fh)
    rb = P["bore_r"] + fit / 2
    kw_, ky = P["key"]
    body -= Pos(0, 0, T / 2) * Cylinder(rb, T + 1) + box(-kw_ / 2, kw_ / 2, 0, ky, -1, T + 1)
    (wx0, wx1), (wy0, wy1) = P["window"]
    body -= box(wx0, wx1, wy0, wy1, -1, T + fh + 1)

    # ---- tower footprint: the servo top's outline, its right side running into the channel
    tp = dict(_top.DEFAULTS)
    (cx0, cx1), cy_front, by0, by1, cxr, ztop = P["channel"]
    rf, rbk = P["blends"]
    hx1, hy0, hy1 = tp["x"][1], tp["y"][0], tp["y"][1]
    round_c = tp["top_right_round"]
    c_front = (cx0 - 15.0, cy_front - rf)                          # the front blend's centre (tangent to the legs' face)
    x_front = c_front[0] - math.sqrt(rf ** 2 - (hy0 - c_front[1]) ** 2)
    c_back = (-28.5, 75.0)                                         # the back blend's centre (tangent to the round)
    tan = (round_c[0] + (c_back[0] - round_c[0]) / 2, round_c[1] + (c_back[1] - round_c[1]) / 2)
    right = [(x_front, hy0), ("arc", c_front), (c_front[0], cy_front), (cx0, cy_front), (cx0, by0), (cx1, by0),
             (cx1, cy_front), (cxr, cy_front), (cxr, by1), (c_back[0], by1), ("arc", c_back), tan, ("arc", round_c)]
    foot = _top.outline(tp, slot=False, right=right)
    zh = P["housing_top"]
    tower = extrude(Plane.XY.offset(T) * foot, amount=ztop - T)
    # above the housing only the channel stands: cut the housing region away, then the 59 deg slope
    ang, xs = P["slope"]
    tower -= box(tp["x"][0] - 1, xs, hy0 - 1, hy1 + 1, zh, ztop + 1)
    # the half-space above a plane through (xs, zh) rising at `ang` toward +X, right of xs only
    above = Pos(xs, (hy0 + hy1) / 2, zh) * Rot(0, -ang, 0) * Pos(0, 0, 100) * Box(400, 400, 200)
    tower -= above & box(xs, cx1, hy0 - 1, hy1 + 1, zh - 1, ztop + 1)
    body += tower
    # the servo pocket (open to the front) and its floor
    (px0, px1), pyb, pz = P["pocket"]
    body -= box(px0, px1, hy0 - 1, pyb, pz, zh + 1)

    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "disc_top": plane((0, 0, T), (0, 0, 1)),
                   "housing_top": plane((-60.0, 55.0, zh), (0, 0, 1)), "bridge_back": plane((0, by1, 60.0), (0, 1, 0))}
    hole_features(feats, "bore", (0, 0, 0), (0, 0, 1), rb, depth=T)
    r, n, a0 = P["disc_holes"]
    for k in range(n):
        a = math.radians(a0 + 360.0 * k / n)
        x, y = r * math.cos(a), r * math.sin(a)
        body -= Pos(x, y, T / 2) * Cylinder(d5 / 2, T + 0.02)
        hole_features(feats, f"disc{k + 1}", (x, y, 0), (0, 0, 1), d5 / 2, depth=T, bolt=P["bolt5"], kind="clearance")
    (mx, my), (mpx, mpy), cbd = P["servo_mount"]
    zt = T + fh
    for i, (x, y) in enumerate(sorted((mx + sx * mpx / 2, my + sy * mpy / 2) for sx in (-1, 1) for sy in (-1, 1))):
        body -= Pos(x, y, zt / 2) * Cylinder(d3 / 2, zt + 0.02)
        body -= Pos(x, y, zt - cbd / 2) * Cylinder((4.5 + fit) / 2, cbd + 0.02)
        hole_features(feats, f"servo_mount{i + 1}", (x, y, zt - cbd), (0, 0, -1), d3 / 2, depth=zt - cbd,
                      bolt=P["bolt3"], kind="clearance")
        hole_features(feats, f"servo_mount_head{i + 1}", (x, y, zt), (0, 0, -1), (4.5 + fit) / 2, depth=cbd)
    xs_c, zc, cbdep, dep = P["clamp"]
    for i, x in enumerate(xs_c):
        body -= Pos(x, hy0 + dep / 2, zc) * Rot(90, 0, 0) * Cylinder(d3 / 2, dep + 0.02)
        body -= Pos(x, hy0 + cbdep / 2, zc) * Rot(90, 0, 0) * Cylinder((4.5 + fit) / 2, cbdep + 0.02)
        hole_features(feats, f"clamp{i + 1}", (x, hy0 + cbdep, zc), (0, 1, 0), d3 / 2, depth=dep - cbdep,
                      bolt=P["bolt3"], kind="clearance")
        hole_features(feats, f"clamp_head{i + 1}", (x, hy0, zc), (0, 1, 0), (4.5 + fit) / 2, depth=cbdep)
    tdep = P["top_holes"][0]
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    for g in ("clamp", "disc", "bridge", "servo_mount"):
        if types[g] != "clearance":
            raise ValueError(f"{g}_hole: the {g} screws are through-bolts or clamps here; clearance only")
    for i, (x, y) in enumerate(tp["holes"]):
        if types["top"] != "clearance":
            body = cut_hole(body, feats, f"top{i + 1}", (x, y, zh), (0, 0, -1), P["bolt3"], types["top"], tdep, fit,
                            P["hole_sizes"], grow_boss=False)
            continue
        body -= Pos(x, y, zh - tdep / 2) * Cylinder(d3 / 2, tdep + 0.02)
        body -= Pos(x, y, zh - cbdep / 2) * Cylinder((4.5 + fit) / 2, cbdep + 0.02)
        hole_features(feats, f"top{i + 1}", (x, y, zh - cbdep), (0, 0, -1), d3 / 2, depth=tdep - cbdep,
                      bolt=P["bolt3"], kind="clearance")
        hole_features(feats, f"top_head{i + 1}", (x, y, zh), (0, 0, -1), (4.5 + fit) / 2, depth=cbdep)
    z0, pitch, n = P["bridge_holes"]
    for i in range(n):
        z = z0 + i * pitch
        body -= Pos(0, (by0 + by1) / 2, z) * Rot(90, 0, 0) * Cylinder(d5 / 2, by1 - by0 + 0.02)
        hole_features(feats, f"bridge{i + 1}", (0, by1, z), (0, -1, 0), d5 / 2, depth=by1 - by0, bolt=P["bolt5"],
                      kind="clearance")
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("disc down (z = 0 on the bed)", "bottom", True,
                                     "the pocket floor bridges the clamp screws and the 59 deg cut is fine, but "
                                     "the horizontal M5/M3 holes print as bridges; supports only in the window's lip "
                                     "if the printer can't bridge 65 mm"))
