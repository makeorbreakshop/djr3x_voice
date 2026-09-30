"""Ring-drive servo mounts (Anderson) - the plates that hang the ring-animation servos off the
droid's rings:

- `lower`  (`lower-ring-servo-mount`): a 7 mm plate with the servo pocket and flange pattern, its
  outer edge on the ring's arc, a lip that hooks behind the lower ring and a crescent under it, one
  counterbored M5 through the lip;
- `main`   (`top-ring-servo-mount-main`): a 3 mm plate, the pocket open at the front, 6 mm legs
  either side carrying the flange pattern, a blind 4.5 mm hole on the arc side;
- `spacer` (`top-ring-servo-mount-spacer`): a 4 mm frame round the pocket, the flange pattern
  through it, its back edge on a smaller arc.

All in the STLs' shared frame (the servo's long side along X, the ring's centre at the origin, the
ring's arc toward +Y). His sources are STLs only; dimensions measured from them.

    make()                 the lower mount
    make(kind="main"), make(kind="spacer")
"""

from __future__ import annotations

import math

from parts.head._common import (Print, cut_hole, finish, hole_d, hole_features, hole_group_params, plane,
                                resolve_hole_types)

from . import HOLE_SIZES

REFERENCE = "r3x-lower-ring-animation - lower-ring-servo-mount.stl"
REF_SUBDIR = "r3x - lower ring animation"
MAIN = dict(kind="main")
MAIN_REFERENCE = ("r3x - top ring animation", "r3x-top-ring-animation - top-ring-servo-mount-main.stl")
SPACER = dict(kind="spacer")
SPACER_REFERENCE = ("r3x - top ring animation", "r3x-top-ring-animation - top-ring-servo-mount-spacer.stl")
LABEL = "Ring servo mount (parametric)"

DESIGNED = {"servo": "clearance", "ring": "clearance"}
INSERT_CANDIDATES = ("servo",)          # the servo's flange screws thread into the mount

DEFAULTS = dict(
    kind="lower",
    fit=0.0,
    half_w=27.0, front_y=69.0,
    arc_r=111.083,                      # outer edge: an arc about the ring's centre
    pocket=(41.0, (73.0, 93.0)),        # servo case + 1 along its length: width X, y range
    bolt="M3", hole_sizes=HOLE_SIZES,
    flange=(48.3, 10.1, 83.0),          # servo flange holes: pitch X, pitch Y, centre y
    # lower
    plate_z=(-3.0, 4.0),
    lip=(-14.0, 101.459, -5.471),       # bottom z; inner edge arc radius, centre y
    crescent=(-22.0, 92.12, 73.523, 24.814, 97.977),   # bottom z; front y; arc radius, centre y; flat top y
                                        # (between the lip's inner arc and this one)
    m5=((0.0, 104.0), 5.0, 10.0, -7.0),  # position, bore, counterbore diameter, counterbore floor z
    # main
    main_plate=(0.0, 3.0), legs=(6.0, 6.5),          # plate z; leg depth below, leg width (X, each side)
    main_back=98.227,                                 # the legs' back
    main_hole=((0.0, 104.0), 4.5, 0.12),              # blind from the top: position, diameter, floor z
    # spacer
    spacer_z=(3.0, 7.0), spacer_back=98.227, spacer_arc=98.227,
    ring_bolt="M5",
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Box, BuildSketch, Circle, Cylinder, Locations, Mode, Plane, Pos, Rectangle, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown ring_servo_mount parameters: {sorted(unknown)}")
    kind, fit = P["kind"], P["fit"]
    W, y0 = P["half_w"], P["front_y"]
    pw, (py0, py1) = P["pocket"]
    pw += 2 * fit

    def slab(z0, z1, y_front, arc_r, arc_cy=0.0, y_cap=None):
        """x in +/-W, from y_front to the arc (radius arc_r about (0, arc_cy)), optionally capped."""
        with BuildSketch(Plane.XY.offset(z0)) as s:
            with Locations((0, (y_front + arc_cy + arc_r) / 2)):
                Rectangle(2 * W, arc_cy + arc_r - y_front + 0.02)
            with Locations((0, arc_cy)):
                Circle(arc_r, mode=Mode.INTERSECT)
            if y_cap is not None:
                with Locations((0, (y_front + y_cap) / 2)):
                    Rectangle(2 * W, y_cap - y_front, mode=Mode.INTERSECT)
        return extrude(s.sketch, amount=z1 - z0)

    def box(xa, xb, ya, yb, za, zb):
        return Pos((xa + xb) / 2, (ya + yb) / 2, (za + zb) / 2) * Box(xb - xa, yb - ya, zb - za)

    feats: dict = {}
    d3 = hole_d(P["bolt"], "clearance", fit, P["hole_sizes"])
    fx, fy, fc = P["flange"]
    if kind == "lower":
        z0, z1 = P["plate_z"]
        body = slab(z0, z1, y0, P["arc_r"])
        lz, lr, lcy = P["lip"]
        body += slab(lz, z0, y0, P["arc_r"]) - slab(lz - 1, z0 + 1, y0 - 1, lr, lcy)
        cz, cy_flat, cr, ccy, ctop = P["crescent"]
        body += slab(cz, lz, cy_flat, cr, ccy, ctop) - slab(cz - 1, lz + 1, y0 - 1, lr, lcy)   # outside the lip's arc
        body -= box(-pw / 2, pw / 2, py0 - fit, py1 + fit, z0 - 1, z1 + 1)
        (mx, my), md, cbd, cbz = P["m5"]
        ring_type = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)["ring"]
        if ring_type != "clearance":
            raise ValueError("the ring bolt is a through-bolt clamping the mount to the ring: clearance only")
        body -= Pos(mx, my, (lz + cbz) / 2) * Cylinder((md + fit) / 2, cbz - lz + 0.02)
        body -= Pos(mx, my, (cbz + z1) / 2) * Cylinder((cbd + fit) / 2, z1 - cbz + 0.02)
        hole_features(feats, "ring", (mx, my, cbz), (0, 0, -1), (md + fit) / 2, depth=cbz - lz)
        hole_features(feats, "ring_head", (mx, my, z1), (0, 0, -1), (cbd + fit) / 2, depth=z1 - cbz)
        zt, zb = z1, z0
        feats["top"] = plane((0, 80.0, z1), (0, 0, 1))
    elif kind == "main":
        z0, z1 = P["main_plate"]
        body = slab(z0, z1, y0, P["arc_r"])
        ld, lw = P["legs"]
        for s in (-1, 1):                                   # legs, their backs on the spacer's arc
            body += slab(z0 - ld, z0, y0, P["spacer_arc"]) & box(min(s * W, s * (W - lw)), max(s * W, s * (W - lw)),
                                                                y0, P["main_back"], z0 - ld, z0)
        body += slab(z0 - ld, z0, py1, P["spacer_arc"]) & box(-W, W, py1, P["main_back"], z0 - ld, z0)
        body -= box(-pw / 2, pw / 2, y0 - 1, py1 + fit, z0 - ld - 1, z1 + 1)
        (hx, hy), hd, hz = P["main_hole"]
        body -= Pos(hx, hy, (hz + z1) / 2 + 0.01) * Cylinder((hd + fit) / 2, z1 - hz)
        hole_features(feats, "top", (hx, hy, z1), (0, 0, -1), (hd + fit) / 2, depth=z1 - hz)
        zt, zb = z1, z0 - ld
        feats["top_face"] = plane((0, 80.0, z1), (0, 0, 1))
    elif kind == "spacer":
        z0, z1 = P["spacer_z"]
        body = slab(z0, z1, y0, P["spacer_arc"])
        body -= box(-pw / 2, pw / 2, py0 - fit, py1 + fit, z0 - 1, z1 + 1)
        zt, zb = z1, z0
        feats["top_face"] = plane((0, 80.0, z1), (0, 0, 1))
    else:
        raise ValueError("kind is 'lower', 'main' or 'spacer'")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    for i, (x, y) in enumerate(sorted((sx * fx / 2, fc + sy * fy / 2) for sx in (-1, 1) for sy in (-1, 1))):
        if types["servo"] == DESIGNED["servo"]:
            body -= Pos(x, y, (zb + zt) / 2) * Cylinder(d3 / 2, zt - zb + 0.02)
            hole_features(feats, f"servo{i + 1}", (x, y, zt), (0, 0, -1), d3 / 2, depth=zt - zb, bolt=P["bolt"],
                          kind="clearance")
        else:
            body = cut_hole(body, feats, f"servo{i + 1}", (x, y, zt), (0, 0, -1), P["bolt"], types["servo"], zt - zb,
                            fit, P["hole_sizes"], grow_boss=True)
    feats["bottom"] = plane((0, 80.0, zb), (0, 0, -1))
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("top face down", "top_face" if kind != "lower" else "top", False,
                                     "top down, the lip and legs rise from the plate; no supports"))
