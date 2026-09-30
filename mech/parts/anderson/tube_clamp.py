"""Tube clamp (Brian Anderson, R-3X internals) - `neck-rod-clamp` and `pipeclamp` are this one part.

A split ring round a tube, pinched by one M3 screw through two ears, with a mounting block on its
side: two blind M3 holes in the block's end face, each crossing a nut slot open to the top (the
nut drops in, the screw from the part it mounts to pulls the clamp on).

Frame = the STEP's: tube axis +Z at the origin, the part z = 0 .. height, the ears toward -Y, the
block toward +X.

    make()                     Anderson's: 26 mm tube
    make(tube_d=32.0)          Hunter's 32 mm neck tube (the ring grows, the block stays put)
"""

from __future__ import annotations

from parts.head._common import (Print, cut_hole, finish, hole_d, hole_features, hole_group_params, plane,
                                resolve_hole_types)

from . import HOLE_SIZES

REFERENCE = "r3x-internal - neck-rod-clamp.step"
ALSO = ("r3x-internal - pipeclamp.step",)       # the same solid under a second name
LABEL = "Tube clamp (parametric)"

DESIGNED = {"clamp": "clearance", "mount": "nut_trap"}   # both clamp: they stay (the design rule)
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,              # print fit, mm on the bore and every hole
    tube_d=26.0,          # the tube the ring grips (as drawn: no clearance; the split closes it)
    wall=4.0,             # ring wall
    height=12.0,
    split=2.0,            # the slot the clamp screw closes
    ear=(4.0, 25.0),      # ear width (X, each side of the split), reach from the axis (-Y)
    clamp_bolt="M3",
    clamp_bolt_y=-20.624, # clamp screw axis (along X), at mid-height
    block=(23.0, 16.0),   # mounting block: end face x, half-width in Y
    mount_bolt="M3",
    mount_bolt_y=10.5,    # two blind holes at y = +/-this, mid-height, along X from the end face
    mount_depth=10.0,
    nut=(17.75, 2.5, 7.0, 2.25),   # nut slots: near face x, thickness (X), width (Y), floor z (open to the top)
    hole_sizes=HOLE_SIZES,         # printed hole diameters by bolt (Anderson: M3 clearance 3.5)
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import (Box, BuildPart, BuildSketch, Circle, Cylinder, Locations, Mode, Plane, Pos, Rectangle,
                           Rot, extrude)

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown tube_clamp parameters: {sorted(unknown)}")
    fit = P["fit"]
    rb = (P["tube_d"] + fit) / 2
    ro = P["tube_d"] / 2 + P["wall"]
    h = P["height"]
    ew, ey = P["ear"]
    bx, bw = P["block"]
    with BuildPart() as bp:
        with BuildSketch():
            Circle(ro)
            with Locations((bx / 2, 0)):
                Rectangle(bx, 2 * bw)
            with Locations((0, -(ey + rb) / 2)):
                Rectangle(2 * (ew + P["split"] / 2), ey - rb)
            Circle(rb, mode=Mode.SUBTRACT)
            with Locations((0, -(ey + 1) / 2)):
                Rectangle(P["split"], ey + 1, mode=Mode.SUBTRACT)
        extrude(amount=h)
    body = bp.part

    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, h), (0, 0, 1)),
                   "mount_face": plane((bx, 0, h / 2), (1, 0, 0))}
    hole_features(feats, "bore", (0, 0, 0), (0, 0, 1), rb, depth=h)
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    if types["clamp"] != "clearance":
        raise ValueError("clamp_hole: the clamp screw pinches the split ring - clearance (with its nut) only")
    # clamp screw across the ears (clearance both sides of the split)
    dc = hole_d(P["clamp_bolt"], "clearance", fit, P["hole_sizes"])
    xe = P["split"] / 2 + ew
    body -= Pos(0, P["clamp_bolt_y"], h / 2) * Rot(0, 90, 0) * Cylinder(dc / 2, 2 * xe + 0.02)
    hole_features(feats, "clamp", (-xe, P["clamp_bolt_y"], h / 2), (1, 0, 0), dc / 2, depth=2 * xe,
                  bolt=P["clamp_bolt"], kind="clearance")
    # mount screws into the block's end face, each through a nut slot
    dm = hole_d(P["mount_bolt"], "clearance", fit, P["hole_sizes"])
    nx, nt, nw, nz = P["nut"]
    for i, sy in enumerate((-1, 1)):
        y = sy * P["mount_bolt_y"]
        if types["mount"] != "nut_trap":
            body = cut_hole(body, feats, f"mount{i + 1}", (bx, y, h / 2), (-1, 0, 0), P["mount_bolt"], types["mount"],
                            P["mount_depth"], fit, P["hole_sizes"], grow_boss=False)
            continue
        body -= Pos(bx - P["mount_depth"] / 2, y, h / 2) * Rot(0, 90, 0) * Cylinder(dm / 2, P["mount_depth"] + 0.02)
        body -= Pos(nx + nt / 2, y, (nz + h + 1) / 2) * Box(nt + fit, nw + fit, h + 1 - nz)
        hole_features(feats, f"mount{i + 1}", (bx, y, h / 2), (-1, 0, 0), dm / 2, depth=P["mount_depth"],
                      bolt=P["mount_bolt"], kind="nut_trap")
        feats[f"nut{i + 1}"] = plane((nx, y, h / 2), (-1, 0, 0))
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z = 0 on the bed)", "bottom", False,
                                     "every feature is vertical except the three M3 holes, which print as "
                                     "horizontal 3.5 mm holes; the nut slots are open to the top"))
