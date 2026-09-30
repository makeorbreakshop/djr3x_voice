"""Left / Right Side w alignment holes (Hunter's cut of the R-3X kit head) - the two side pieces:
each carries an ear ring, a quadrant of floor front and back, the chamfered skirt up to the top's
rim, the front shelf, and the rib that holds up the top's front.

References: `Left Side w alignment holes.stl`, `Right Side w alignment holes.stl` (kit coordinates,
_shell.py; pitched 1.07 deg like the top and bottom). The pieces are mirror images about the seam
(x = 0) apart from the front skirt's chin, which Hunter cut differently on each side (not modelled:
reported by the regression). `side_left` / `side_right` wrap this module.

Design (left piece, x >= 0; the right is its mirror):
  skirt      a conical band (29.1 deg from vertical) about the head's axis, from the floor's
             underside to the top's rim, open where the ear ring sits
  floor      the quadrants front and back that sit on the bottom pan, cut to the pan's opening
  shelf      the front shelf under the face
  ear ring   a tube along X with a thinner lip and four fingers that take the ear cap
  rib        the front rib: a web and post under a beam that follows the top's inside. Its profile
             is traced from the reference (the kit's organic outline has no simpler intent)
  holes      five 3.15 mm seam-pin holes (to the other side), four 1.75 mm filament pins and a
             5 mm magnet/pin hole down into the bottom pan, a 6 mm pin up into the top, the rib's
             stepped hole

Not reproduced (reported): the ear ring's latch pockets, end flanges and inboard end wall, the
fingers' clip notches, the skirt's inner ribs at the back, and the front chin.
"""

from __future__ import annotations

import math

import numpy as np

from ._common import Print, hole_features, plane, rot_x
from ._shell import SHELL_FROM_HEAD

TILT_DEG = 1.07

# reference features deliberately not modelled (design frame, left piece; the regression lists them
# instead of failing on them): the two angled latch pockets at the top of the ear ring
UNMODELLED = [{"note": "ear-cap latch pockets (two angled rectangular slots through the ring's top wall)",
               "box": [[110.0, 68.0, -40.0], [133.0, 92.0, -5.0]]}]
MIRROR_X = np.diag([-1.0, 1.0, 1.0, 1.0])

# the rib's profile (x, y), traced from the reference: to rib_z[1] (web, post, beam and the flange
# out to the skirt) and on to rib_z[2] (the beam, post and flange without the web)
RIB_FULL = ((0.0, 118.49), (17.76, 117.32), (30.89, 114.9), (45.06, 110.65), (54.86, 106.57), (64.82, 101.29),
            (75.06, 94.42), (80.98, 89.61), (81.24, 35.79), (82.79, 34.4), (84.52, 34.22), (86.1, 34.97),
            (86.93, 36.41), (86.98, 83.89), (92.49, 77.65), (97.37, 70.88), (103.47, 59.29), (106.29, 51.42),
            (108.21, 43.84), (110.1, 29.2), (43.62, 29.2), (52.37, 31.7), (59.54, 35.58), (65.77, 40.85),
            (70.76, 47.24), (63.79, 90.21), (55.52, 95.11), (45.05, 99.72), (34.4, 103.1), (23.01, 105.55),
            (20.98, 105.55), (18.76, 104.69), (15.22, 100.79), (13.07, 99.71), (0.0, 99.5))
RIB_FRONT = ((0.0, 118.49), (17.79, 117.32), (30.91, 114.9), (45.06, 110.65), (54.86, 106.57), (65.27, 101.03),
             (75.06, 94.42), (80.98, 89.61), (81.24, 35.79), (82.79, 34.4), (84.52, 34.22), (86.1, 34.97),
             (86.93, 36.41), (86.99, 83.89), (92.5, 77.64), (97.37, 70.88), (103.48, 59.28), (106.2, 51.72),
             (108.18, 43.97), (110.1, 29.2), (79.18, 29.2), (79.17, 80.31), (75.49, 84.01), (67.21, 90.62),
             (57.71, 96.45), (47.32, 101.21), (36.26, 104.87), (24.95, 107.44), (8.06, 109.49), (6.85, 106.24),
             (4.74, 103.65), (3.26, 102.74), (0.0, 102.0))

DEFAULTS = dict(
    fit=0.0,                     # print fit, mm on every hole diameter
    floor_y=(-3.7, 3.6),         # floor underside (on the bottom pan's seat) and top
    rim_y=28.6,                  # the top's rim
    skirt=(115.5, 0.557, 4.35, 5.0),   # outer cone radius at y = 0, dr/dy, axis z, wall (normal)
    ear_gap=(-46.2, 48.2),       # the skirt stops either side of the ear ring (z)
    floor_cut=(36.6, -48.3, -43.3, 50.3, 48.6, 31.46, 43.79, 97.0),  # the pan interface: x step, back z (x <
                                 # step / beyond), front ledge z, front z beyond, arch radius, arch centre z, floor end x
    shelf=(38.62, 8.42, 4.7, 62.4, 105.3),   # front shelf: top face's height at z = 0, its slope down toward
                                 # the front (deg), thickness, back edge z, outer x
    ear=(97.0, 134.7, 50.26, 57.57, 32.30, 1.0),   # x0, x1, inner r, outer r, centre y, z
    ear_lip=(139.9, 53.65),      # the ring's thinner lip: to x, outer r (inner r as the ring)
    ear_tabs=(38.8, 12.0, 4),    # fingers inside the lip: inner r, width, count (at 0/90/180/270 deg)
    rib_z=(62.4, 67.4, 70.7),    # rib back face, web/post front face, beam front face
    seam_pin_d=3.15,
    seam_pin_depth=6.5,
    seam_pins=((0.10, -103.4), (0.10, -56.39), (114.93, 65.48), (0.12, 83.62), (0.12, 111.64)),  # (y, z)
    floor_pin_d=1.95,            # 1.75 mm filament pins into the bottom pan
    floor_pin_depth=6.25,
    floor_pins=((34.44, -70.75), (52.16, -48.52), (28.61, 78.93), (52.55, 56.34)),   # (x, z)
    magnet=(8.71, 99.03, 5.06, 3.25),  # x, z, diameter, depth (up from the floor underside)
    top_pin=(79.96, -92.63, 6.0, 2.46),  # x, z, diameter, depth (down from the rim)
    rib_hole=(68.95, 37.0, 2.70, 4.72, 64.26, 67.26),  # x, y, bore, pocket, pocket z0, z1 (from the back face)
)


def make(side: str = "left", params: dict | None = None, **kw):
    from build123d import (Box, BuildLine, BuildPart, BuildSketch, Circle, Cone, Cylinder, Locations, Plane,
                           Polyline, Pos, Rectangle, Rot, extrude, make_face)

    if side not in ("left", "right"):
        raise ValueError("side is 'left' or 'right'")
    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown side parameters: {sorted(unknown)}")
    fit = P["fit"]
    fy0, fy1 = P["floor_y"]
    ry = P["rim_y"]
    r0, k, cz, wall = P["skirt"]
    big = 400.0

    def cone_solid(r_at_0, y0, y1):
        """A cone frustum about the head axis: radius r_at_0 + k*y, from y0 to y1."""
        ra, rb = r_at_0 + k * y0, r_at_0 + k * y1
        return Pos(0, (y0 + y1) / 2, cz) * Rot(-90, 0, 0) * Cone(ra, rb, y1 - y0)   # Cone runs +Z: turned to +Y

    half = Pos(big / 2, 0, 0) * Box(big, big, big)                 # x >= 0
    dr = wall * math.sqrt(1 + k * k)                                 # the wall along the radius
    skirt = cone_solid(r0, fy0, ry) - cone_solid(r0 - dr, fy0 - 1, ry + 1)
    g0, g1 = P["ear_gap"]
    skirt -= Pos(0, 0, (g0 + g1) / 2) * Box(big, big, g1 - g0)
    # floor: inside the skirt, cut to the pan's opening, to x = floor end
    sx, zb0, zb1, zl, zf1, ar, azc, xend = P["floor_cut"]
    floor = cone_solid(r0, fy0, fy1) & Pos(xend / 2, 0, 0) * Box(xend, big, big)
    with BuildPart() as cut:
        with BuildSketch(Plane(origin=(0, fy0 - 1, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):   # local (x, -z)
            with Locations((0, -(zb0 + zl) / 2)):
                Rectangle(2 * sx, zl - zb0)
            with Locations((0, -azc)):
                Circle(ar)
            with Locations(((sx + xend + 1) / 2, -(zb1 + zf1) / 2)):
                Rectangle(xend + 1 - sx, zf1 - zb1)
        extrude(amount=fy1 - fy0 + 2)
    floor -= cut.part
    # front shelf
    sht, sha, sh_t, shz, shx = P["shelf"]
    slab = Pos(shx / 2, sht, 0) * Rot(sha, 0, 0) * Pos(0, -sh_t / 2, 0) * Box(shx, sh_t, big)    # falls toward +Z
    shelf = cone_solid(r0 - dr, fy0, ry + 20) & slab & Pos(shx / 2, 0, shz + big / 2) * Box(shx, big, big)
    # ear ring
    ex0, ex1, eri, ero, ey, ez = P["ear"]
    ring = Pos((ex0 + ex1) / 2, ey, ez) * Rot(0, 90, 0) * (Cylinder(ero, ex1 - ex0) - Cylinder(eri, ex1 - ex0 + 2))
    lx, lro = P["ear_lip"]
    ring += Pos((ex1 + lx) / 2, ey, ez) * Rot(0, 90, 0) * (Cylinder(lro, lx - ex1) - Cylinder(eri, lx - ex1 + 2))
    tr, tw, tn = P["ear_tabs"]
    for i in range(tn):
        a = 360.0 * i / tn
        ring += (Pos((ex1 + lx) / 2, ey, ez) * Rot(-a, 0, 0) * Pos(0, (tr + eri + 0.5) / 2, 0)
                 * Box(lx - ex1, eri + 0.5 - tr, tw))
    body = (skirt & half) + floor + shelf + ring
    body -= Pos((ex0 + ex1) / 2, ey, ez) * Rot(0, 90, 0) * Cylinder(eri, ex1 - ex0 - 0.02)   # keep the bore clear
    # the rib: web + post to rib_z[1], the beam on to rib_z[2]
    rz0, rz1, rz2 = P["rib_z"]
    for pts, z0, z1 in ((RIB_FULL, rz0, rz1), (RIB_FRONT, rz1 - 0.01, rz2)):
        with BuildPart() as rb:
            with BuildSketch(Plane.XY.offset(z0)):
                with BuildLine():
                    Polyline(*pts, close=True)
                make_face()
            extrude(amount=z1 - z0)
        body += rb.part

    feats: dict = {}
    # holes (left piece; mirrored with the part below)
    ds = P["seam_pin_d"] + fit
    for i, (y, z) in enumerate(sorted(P["seam_pins"], key=lambda q: q[1])):
        body -= Pos(P["seam_pin_depth"] / 2, y, z) * Rot(0, 90, 0) * Cylinder(ds / 2, P["seam_pin_depth"] + 0.02)
        hole_features(feats, f"seam{i + 1}", (0, y, z), (1, 0, 0), ds / 2, depth=P["seam_pin_depth"], kind="pin")
    dfp = P["floor_pin_d"] + fit
    for i, (x, z) in enumerate(sorted(P["floor_pins"])):
        body -= Pos(x, fy0 + P["floor_pin_depth"] / 2, z) * Rot(90, 0, 0) * Cylinder(dfp / 2, P["floor_pin_depth"] + 0.02)
        hole_features(feats, f"pin{i + 1}", (x, fy0, z), (0, 1, 0), dfp / 2, depth=P["floor_pin_depth"], kind="pin")
    mx, mz, md, mdep = P["magnet"]
    body -= Pos(mx, fy0 + mdep / 2, mz) * Rot(90, 0, 0) * Cylinder((md + fit) / 2, mdep + 0.02)
    hole_features(feats, "magnet", (mx, fy0, mz), (0, 1, 0), (md + fit) / 2, depth=mdep)
    tx, tz, td, tdep = P["top_pin"]
    body -= Pos(tx, ry - tdep / 2, tz) * Rot(90, 0, 0) * Cylinder((td + fit) / 2, tdep + 0.02)
    hole_features(feats, "top_pin", (tx, ry, tz), (0, -1, 0), (td + fit) / 2, depth=tdep, kind="pin")
    hx, hy, hb, hp, hz0, hz1 = P["rib_hole"]
    body -= Pos(hx, hy, (rz0 + hz0) / 2) * Cylinder((hb + fit) / 2, hz0 - rz0 + 0.02)
    body -= Pos(hx, hy, (hz0 + hz1) / 2) * Cylinder((hp + fit) / 2, hz1 - hz0)
    hole_features(feats, "rib", (hx, hy, rz0), (0, 0, 1), (hb + fit) / 2, depth=hz0 - rz0)
    hole_features(feats, "rib_pocket", (hx, hy, hz0), (0, 0, 1), (hp + fit) / 2, depth=hz1 - hz0)
    feats["seam"] = plane((0, 50.0, 0), (-1, 0, 0))
    feats["floor_under"] = plane((50.0, fy0, 0), (0, -1, 0))
    feats["rim"] = plane((100.0, ry, -80.0), (0, 1, 0))
    feats["ear_axis"] = {"type": "axis", "p": [ex1, ey, ez], "d": [-1.0, 0.0, 0.0], "r": eri}

    frame = SHELL_FROM_HEAD @ rot_x(TILT_DEG)
    if side == "right":
        body = body.mirror(Plane.YZ)
        from ._common import moved

        feats = {k2: moved(v, MIRROR_X) for k2, v in feats.items()}
        feats = {(k2.replace("_l", "_r") if k2.endswith("_l") else k2): v for k2, v in feats.items()}
    return body, feats, P, frame


def printability():
    return Print("floor underside on the bed", "floor_under", True,
                 "the ear ring and the rib's beam overhang: supports under the ring and the beam; the skirt's "
                 "29 deg lean is self-supporting")
