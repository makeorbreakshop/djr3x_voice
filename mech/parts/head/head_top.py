"""RX Head Top (Hunter's cut of the R-3X kit head) - the dome.

Reference: `RX Head Top.stl` (kit coordinates, see _shell.py). In the head frame Hunter's top is
tilted 1.077 deg nose-up about X: its ceiling, rim and front face are exactly level in its own
frame, so it is designed there (+Y up the dome's axis, +Z forward) and carried out by
EXPORT_FRAME.

Design:
  dome       an ellipsoid of revolution (outer) over a flat rim at y = rim_y, faced off at the front
  wall       an inner ellipsoid (the kit's dome is two ellipsoids, not a constant offset: ~4.1 mm)
  crown      the cavity stops at a flat ceiling (a 14 mm crown) carrying five blind holes on a
             25.4 mm square + centre (the kit's top mount)
  rear band  below it the cavity is a vertical cylinder: a thick lower band at the back
  front band a 13.4 mm band at the face, hollowed by an elliptic arch along Z
  ear cuts   a cylinder along X clears the ear rings (the side pieces carry them)
  slots      two 6 mm slots from the crown to the front edge (Hunter's visor arms)
  split      the rim sits on the side pieces: three 6 mm alignment-pin holes in it
"""

from __future__ import annotations

from ._common import Print, finish, hole_features, plane, rot_x
from ._shell import SHELL_FROM_HEAD, apply_clearance_cuts, elliptic_prism_z, spheroid

REFERENCE = "RX Head Top.stl"
LABEL = "RX Head Top (parametric)"
TILT_DEG = 1.077                                   # nose-up about X, in the head frame
EXPORT_FRAME = SHELL_FROM_HEAD @ rot_x(TILT_DEG)   # design frame -> the STL's

DEFAULTS = dict(
    fit=0.0,                          # print fit, mm on every hole diameter
    outer=(131.13, 107.08, 29.12),    # outer ellipsoid: radius, height semi-axis, centre height
    outer_axis_z=3.94,                # its axis (x = 0)
    inner=(127.01, 104.97, 27.16),    # inner ellipsoid (the wall between them is ~4.1 mm)
    inner_axis_z=2.82,
    rim_y=28.6,                       # the split plane onto the side pieces
    ceiling_y=122.72,                 # the crown's underside
    band=(120.43, 5.16),              # rear band: the cavity's vertical cylinder (radius, axis z)
    front_z=70.74,                    # front face
    front_band_z=57.36,               # the cavity stops here; ahead of it, the arch
    arch=(109.92, 89.05, 29.42),      # front arch (elliptic, along Z): half-width, height, centre y
    ear=(58.84, 30.79, 0.88),         # ear-ring clearance cylinder along X: radius, centre y, z
    slot=(84.0, 6.0, 5.14),           # visor-arm slots: |x|, width, z of the round end's centre; None = none
    pin_d=6.0,                        # alignment pins into the side pieces
    pin_depth=5.0,
    pins=((0.0, -121.37), (-80.0, -92.62), (80.0, -92.62)),   # (x, z) in the rim
    crown_hole_d=7.04,
    crown_hole_depth=3.38,
    crown_pattern=25.4,               # square, centred on (0, crown_z), plus a centre hole
    crown_z=-0.24,
    clearance_cuts=(),                # STL paths (the reference STL's frame) subtracted from the shell
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot, SlotCenterToCenter, BuildPart, BuildSketch, Plane, Locations, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown head_top parameters: {sorted(unknown)}")
    fit = P["fit"]
    A, B, yc = P["outer"]
    Ai, Bi, yci = P["inner"]
    y0 = P["rim_y"]
    big = 400.0
    zf, zs = P["front_z"], P["front_band_z"]

    dome = spheroid(A, B, yc, y0, (0.0, P["outer_axis_z"]))
    dome &= Pos(0, 0, zf - big / 2) * Box(big, big, big)                      # front face
    # the cavity: inner ellipsoid, inside the rear band, under the ceiling, behind the front band
    cav = spheroid(Ai, Bi, yci, y0 - 10, (0.0, P["inner_axis_z"]))
    rb, rbz = P["band"]
    cav &= Pos(0, 0, rbz) * Rot(90, 0, 0) * Cylinder(rb, big)          # vertical
    cav &= Pos(0, P["ceiling_y"] - big / 2, zs - big / 2) * Box(big, big, big)
    fa, fb, fyc = P["arch"]
    cav += elliptic_prism_z(fa, fb, 0.0, fyc, zs - 1.0, zf + 1.0)
    body = dome - cav
    er, ey, ez = P["ear"]
    body -= Pos(0, ey, ez) * Rot(0, 90, 0) * Cylinder(er, big)
    if P["slot"] is not None:
        sx, sw, sz = P["slot"]
        for s in (-1, 1):
            with BuildPart() as sl:
                with BuildSketch(Plane.XZ.offset(-big / 2)):          # normal -Y, local (u, v) = (x, z)
                    with Locations((s * sx, (sz + zf + 10) / 2)):
                        SlotCenterToCenter(zf + 10 - sz, sw + fit, rotation=90)
                extrude(amount=big)
            body -= sl.part
    feats: dict = {}
    dp = P["pin_d"] + fit
    for i, (x, z) in enumerate(sorted(P["pins"])):
        body -= Pos(x, y0 + P["pin_depth"] / 2, z) * Rot(90, 0, 0) * Cylinder(dp / 2, P["pin_depth"] + 0.02)
        hole_features(feats, f"pin{i + 1}", (x, y0, z), (0, 1, 0), dp / 2, depth=P["pin_depth"], kind="pin")
    dc = P["crown_hole_d"] + fit
    h = P["crown_pattern"] / 2
    cz = P["crown_z"]
    crown = [(0.0, cz)] + [(sx * h, cz + sz * h) for sx in (-1, 1) for sz in (-1, 1)]
    for i, (x, z) in enumerate(sorted(crown)):
        yt = P["ceiling_y"]
        body -= Pos(x, yt + P["crown_hole_depth"] / 2, z) * Rot(90, 0, 0) * Cylinder(dc / 2, P["crown_hole_depth"] + 0.02)
        hole_features(feats, f"crown{i + 1}", (x, yt, z), (0, 1, 0), dc / 2, depth=P["crown_hole_depth"])
    body = apply_clearance_cuts(body, P["clearance_cuts"], EXPORT_FRAME)
    feats["rim"] = plane((0, y0, P["band"][1]), (0, -1, 0))
    feats["ceiling"] = plane((0, P["ceiling_y"], 0), (0, -1, 0))
    feats["front"] = plane((0, 100.0, zf), (0, 0, 1))
    feats["ear_l"] = {"type": "axis", "p": [100.0, ey, ez], "d": [-1.0, 0.0, 0.0], "r": er}
    feats["ear_r"] = {"type": "axis", "p": [-100.0, ey, ez], "d": [1.0, 0.0, 0.0], "r": er}
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE, frame=EXPORT_FRAME,
                  printability=Print("crown down (on a printed cradle) or rim down with tree supports", "rim", True,
                                     "rim down the dome overhangs past 60 deg only near the crown and the ceiling "
                                     "bridges 105 mm: supports under the ceiling; crown down needs a cradle"))
