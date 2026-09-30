"""Head-lift gear (Anderson, `lift-gear`) - the pinion on the lift servo that drives the rack.

19 square-rooted trapezoid teeth (the rack's tooth on a circle) on a 25 mm root, 12 mm wide, with the
servo's disc horn let into its back: a 30.5 mm pocket, a 13 mm pocket for the spline boss, the M3
centre screw and eight 2 mm horn screws.

Frame = the STEP's: gear axis along Y, front face y = 0, the gear toward -Y; a tooth on +Z.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._gear import polygon_tooth, spur, trapezoid

REFERENCE = "r3x-internal - lift-gear.step"
LABEL = "Head-lift gear (parametric)"

DESIGNED = {"horn_screw": "tapped"}          # the disc horn's screws self-thread into the gear
INSERT_CANDIDATES = ("horn_screw",)

DEFAULTS = dict(
    fit=0.0,
    horn_bolt="M2",
    teeth=19, root_r=25.0, width=12.0,
    tooth=(5.0, 2.5, 26.0, 30.0),       # root width, tip land, straight-wall top, tip (radii along the tooth)
    centre_d=3.5, centre_depth=6.0,     # M3 through the horn's centre, from the front face
    horn=(30.5, 4.0, 13.0, 2.0),        # disc-horn pocket diameter/depth, spline-boss pocket diameter/depth (from the back)
    horn_screws=(12.0, 8, 2.0, 8.0),    # radius, count, diameter, depth from the front face
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Plane, Pos, Rot, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown lift_gear parameters: {sorted(unknown)}")
    fit, w = P["fit"], P["width"]
    rw, tw, wt, ta = P["tooth"]
    face = spur(P["teeth"], P["root_r"], polygon_tooth(trapezoid(rw, tw, wt, ta, P["root_r"] - 2.0)))
    body = extrude(Plane.XZ * face, amount=w)                    # Plane.XZ faces -Y: y 0 .. -w

    def along_y(d, y0, y1, x=0.0, z=0.0):                          # a hole from y0 down to y1 (y1 < y0)
        return Pos(x, (y0 + y1) / 2, z) * Rot(90, 0, 0) * Cylinder(d / 2, abs(y1 - y0) + 0.02)

    feats: dict = {"front": plane((0, 0, 0), (0, 1, 0)), "back": plane((0, -w, 0), (0, -1, 0))}
    cd = P["centre_d"] + fit
    body -= along_y(cd, 0, -P["centre_depth"])
    hole_features(feats, "centre", (0, 0, 0), (0, -1, 0), cd / 2, depth=P["centre_depth"])
    hd, hdep, bd, bdep = P["horn"]
    body -= along_y(hd + fit, -w, -w + hdep)
    body -= along_y(bd + fit, -w + hdep, -w + hdep + bdep)
    hole_features(feats, "horn", (0, -w, 0), (0, 1, 0), (hd + fit) / 2, depth=hdep)
    hole_features(feats, "spline_boss", (0, -w + hdep, 0), (0, 1, 0), (bd + fit) / 2, depth=bdep)
    r, n, sd, sdep = P["horn_screws"]
    ht = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)["horn_screw"]
    for k in range(n):
        a = 2 * math.pi * k / n
        x, z = r * math.cos(a), r * math.sin(a)
        if ht != DESIGNED["horn_screw"]:        # from the horn's face (the back pocket's floor) into the gear
            body = cut_hole(body, feats, f"horn_screw{k + 1}", (x, -w + hdep, z), (0, 1, 0), P["horn_bolt"], ht,
                            w - hdep, fit)
            continue
        body -= along_y(sd + fit, 0, -sdep, x, z)
        hole_features(feats, f"horn_screw{k + 1}", (x, 0, z), (0, -1, 0), (sd + fit) / 2, depth=sdep)
    feats["axis"] = {"type": "axis", "p": [0.0, -w, 0.0], "d": [0.0, 1.0, 0.0], "r": P["root_r"]}
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("front face down (y = 0 on the bed)", "front", False,
                                     "the horn pockets open upward; no supports"))
