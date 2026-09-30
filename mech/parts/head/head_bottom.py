"""RX Head Bottom with mount holes (Hunter's cut of the R-3X kit head) - the pan the mech plate
bolts under.

Reference: `RX Head Bottom with mount holes.stl` (kit coordinates, _shell.py). Like the top it is
pitched 1.07 deg in the head frame (its seat face, and so its six insert bosses, are level only in
its own frame - the workbench's yaw-only shell fit leaves a 1.07 mm step across the bosses), so
it is designed level and carried out by EXPORT_FRAME.

Design:
  slab      the seat (y = seat_y) over an octagon with two side wings, 45 deg chamfered underneath
  opening   the tombstone opening for the neck: straight sides, an arch at the front
  fins      two rocker fins under the octagon, their bottoms an arc (the kit's head-tilt rockers)
  pads      a pad under each wing (the kit's grille pad and the side panels' key blocks)
  holes     six M4 heat-set holes (drill-pointed) on the plate's 120 x 57.5 pattern; eight 1.75 mm
            filament alignment-pin holes into the side pieces; eight small holes in the pads

Not reproduced (reported by the regression): the grille's 27 pockets in the wing pads, the key
notches in the pad end blocks, the tick marks along the fins' arcs, and the opening's back edge
flaring out below the slab (modelled straight).
"""

from __future__ import annotations

import math

from ._common import (Print, cut_hole, finish, hole_d, hole_features, hole_group_params, plane, resolve_hole_types,
                      rot_x)
from ._shell import SHELL_FROM_HEAD

REFERENCE = "RX Head Bottom with mount holes.stl"
LABEL = "RX Head Bottom (parametric)"
TILT_DEG = 1.07
EXPORT_FRAME = SHELL_FROM_HEAD @ rot_x(TILT_DEG)

DESIGNED = {"ins": "heat_set", "pad": "tapped"}
INSERT_CANDIDATES = ("pad",)

DEFAULTS = dict(
    fit=0.0,                   # print fit, mm on every hole diameter
    pad_bolt="M3",
    seat_y=-3.69,              # the plate's seat (top face)
    chamfer_y=-9.67,           # the 45 deg underside chamfers start here
    bottom_y=-16.6,            # octagon underside
    octagon=(68.6, 88.57, -82.21, 33.88),   # half-width, front z, back z, 45 deg corner (both ends)
    wing=(97.0, 51.68, -43.32),             # wing half-span, front z, back z
    pad=(72.07, 93.5, -40.02, 48.18, -13.45),  # under each wing: x0, x1, z0, z1, bottom y
    opening=(36.6, -48.28, 31.46, 43.79, 50.25),  # half-width, back z, arch radius, arch centre z, ledge z
    fins=(36.6, 53.1, 70.1, 32.41, 1.0),    # x0, x1 (each side), arc radius, arc centre y, z
    bolt="M4",
    insert_pattern=(60.0, 0.36, 28.74),            # |x|, centre z, pitch: six heat-set holes (the plate's pattern)
    insert_depth=6.0,
    pin_d=1.944,                            # 1.75 mm filament pins into the side pieces
    pin_depth=5.5,
    pins=((34.47, -70.74), (52.19, -48.51), (28.63, 78.94), (52.57, 56.35)),   # (|x|, z), mirrored
    pad_hole_d=2.834,
    pad_hole_depth=3.0,
    pad_holes=((75.7, -36.13), (89.89, -36.13), (75.7, 44.29), (89.89, 44.29)),  # (|x|, z), mirrored
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import (Align, BuildLine, BuildPart, BuildSketch, Circle, Cone, Cylinder, Locations, Mode, Plane,
                           Polyline, Pos, Rectangle, Rot, extrude, make_face)

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown head_bottom parameters: {sorted(unknown)}")
    fit = P["fit"]
    ys, yc, yb = P["seat_y"], P["chamfer_y"], P["bottom_y"]
    X, zF, zB, C = P["octagon"]
    W, wF, wB = P["wing"]

    def plane_y(y):                    # sketch plane at height y, normal +Y: local (u, v) = (x, -z)
        return Plane(origin=(0, y, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))

    octo = [(X, zF - C), (X - C, zF), (-(X - C), zF), (-X, zF - C), (-X, zB + C), (-(X - C), zB), (X - C, zB), (X, zB + C)]
    slab = octo[:1] + [(X, wF), (W, wF), (W, wB), (X, wB)] + octo[7:] + octo[6:0:-1]
    slab = [(X, zF - C), (X - C, zF), (-(X - C), zF), (-X, zF - C), (-X, wF), (-W, wF), (-W, wB), (-X, wB),
            (-X, zB + C), (-(X - C), zB), (X - C, zB), (X, zB + C), (X, wB), (W, wB), (W, wF), (X, wF)]
    with BuildPart() as bp:
        # the slab: seat down to where the chamfers start
        with BuildSketch(plane_y(yc)):
            with BuildLine():
                Polyline(*[(x, -z) for x, z in slab], close=True)
            make_face()
        extrude(amount=ys - yc)
        # octagon and wings below, 45 deg chamfered (tapered extrusions downward)
        with BuildSketch(plane_y(yc)):
            with BuildLine():
                Polyline(*[(x, -z) for x, z in octo], close=True)
            make_face()
        extrude(amount=-(yc - yb), taper=45)
        px0, px1, pz0, pz1, py = P["pad"]
        for s in (-1, 1):
            with BuildSketch(plane_y(yc)):
                with Locations((s * (X + W) / 2, -(wF + wB) / 2)):
                    Rectangle(W - X, wF - wB)
            extrude(amount=-(px0 - X), taper=45)
            with BuildSketch(plane_y(yc - (px0 - X))):
                with Locations((s * (px0 + px1) / 2, -(pz0 + pz1) / 2)):
                    Rectangle(px1 - px0, pz1 - pz0)
            extrude(amount=-((yc - (px0 - X)) - py))
        # rocker fins: the circular segment under the octagon, between x0 and x1 each side
        fx0, fx1, fr, fyc, fzc = P["fins"]
        for s in (-1, 1):
            with BuildSketch(Plane(origin=(s * fx0 if s > 0 else -fx1, 0, 0), x_dir=(0, 0, 1), z_dir=(1, 0, 0))):
                # local (u, v) = (z, -y): x_dir = +Z, z_dir = +X, so y_dir = X x Z = -Y
                with Locations((fzc, -fyc)):
                    Circle(fr)
                with Locations((0, -yb - 100)):                    # keep only what hangs below the octagon
                    Rectangle(400, 200, mode=Mode.SUBTRACT)
            extrude(amount=fx1 - fx0)
        # the opening
        ox, oz0, ar, azc, lz = P["opening"]
        with BuildSketch(plane_y(yb - 30)):
            with Locations((0, -(oz0 + lz) / 2)):
                Rectangle(2 * ox, lz - oz0)
            with Locations((0, -azc)):
                Circle(ar)
        extrude(amount=ys - yb + 40, mode=Mode.SUBTRACT)
    body = bp.part

    feats: dict = {}
    d_ins = hole_d(P["bolt"], "heatset", fit)
    tip = (d_ins / 2) / math.tan(math.radians(59))
    ix, izc, ip = P["insert_pattern"]
    ins = sorted([(s * ix, izc + k * ip) for s in (-1, 1) for k in (-1, 0, 1)])
    for i, (x, z) in enumerate(ins):
        dep = P["insert_depth"]
        drill = Pos(x, ys - dep / 2 + 0.01, z) * Rot(90, 0, 0) * Cylinder(d_ins / 2, dep + 0.02)
        drill += Pos(x, ys - dep, z) * Rot(90, 0, 0) * Rot(180, 0, 0) * Cone(d_ins / 2, 0, tip, align=(Align.CENTER, Align.CENTER, Align.MIN))
        body -= drill
        hole_features(feats, f"ins{i + 1}", (x, ys, z), (0, -1, 0), d_ins / 2, depth=dep, bolt=P["bolt"], kind="heat_set")
    dp = P["pin_d"] + fit
    pins = sorted([(s * x, z) for x, z in P["pins"] for s in (-1, 1)])
    for i, (x, z) in enumerate(pins):
        body -= Pos(x, ys - P["pin_depth"] / 2, z) * Rot(90, 0, 0) * Cylinder(dp / 2, P["pin_depth"] + 0.02)
        hole_features(feats, f"pin{i + 1}", (x, ys, z), (0, -1, 0), dp / 2, depth=P["pin_depth"], kind="pin")
    dh = P["pad_hole_d"] + fit
    py = P["pad"][4]
    ph = sorted([(s * x, z) for x, z in P["pad_holes"] for s in (-1, 1)])
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    if types["ins"] != "heat_set":
        raise ValueError("ins_hole: the plate's screws need the kit's M4 inserts here")
    for i, (x, z) in enumerate(ph):
        if types["pad"] != "tapped":
            body = cut_hole(body, feats, f"pad{i + 1}", (x, py, z), (0, 1, 0), P["pad_bolt"], types["pad"],
                            P["pad_hole_depth"], fit, grow_boss=False)
            continue
        body -= Pos(x, py + P["pad_hole_depth"] / 2, z) * Rot(90, 0, 0) * Cylinder(dh / 2, P["pad_hole_depth"] + 0.02)
        hole_features(feats, f"pad{i + 1}", (x, py, z), (0, 1, 0), dh / 2, depth=P["pad_hole_depth"])
    feats["boss_top"] = plane((0, ys, izc), (0, 1, 0))
    feats["underside"] = plane((0, yb, 0), (0, -1, 0))
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE, frame=EXPORT_FRAME,
                  printability=Print("seat face down (y = seat_y on the bed)", "boss_top", True,
                                     "seat down, the slab is flat on the bed; the rocker fins and wing pads rise "
                                     "from it and need no supports, but the insert holes then print upside down "
                                     "(fine) and the 45 deg chamfers are overhangs at the limit"))
