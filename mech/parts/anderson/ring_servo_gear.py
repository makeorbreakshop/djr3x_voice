"""Ring-drive servo gears (Anderson, `lower-ring-servo-gear` / `top-ring-servo-gear`) - the pinions
on the ring-animation servos.

Rounded teeth (a round tip joined by straight flanks to a round root fillet mid-gap, _gear.py), 10 mm
thick, with the servo's six-arm horn let into the underside: a star pocket (r3 arm tips over the
horn's six screw holes), a 7 mm hole for the horn's centre screw above it.

His sources for these are STLs only (no B-rep): the tooth dimensions are fitted to them (mean
section deviation ~0.01 mm, the STLs' own faceting), the rest measured.

    make()                   the top ring's gear (20 teeth)
    make(**LOWER)            the lower ring's (25 teeth)

Frame = the STLs': axis +Z, z = 0 .. thickness, the horn side down.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._gear import rounded_spur

REFERENCE = "r3x-top-ring-animation - top-ring-servo-gear.stl"
REF_SUBDIR = "r3x - top ring animation"
LABEL = "Ring servo gear (parametric)"

LOWER = dict(teeth=25, tip=(1.4629, 31.5408), fillet=(1.6342, 29.2397), phase=3.6)
LOWER_REFERENCE = ("r3x - lower ring animation", "r3x-lower-ring-animation - lower-ring-servo-gear.stl")

DESIGNED = {"horn": "tapped"}          # the horn's screws self-thread into the gear
INSERT_CANDIDATES = ("horn",)

DEFAULTS = dict(
    fit=0.0,
    horn_bolt="M2",
    teeth=20, tip=(1.4666, 27.0372), fillet=(1.8833, 24.9144), phase=0.0,   # tip r / centre r, fillet r / centre r, tooth angle
    thickness=10.0,
    horn=(6, 13.5, 3.0, 7.84, 3.0),     # arms, tip-centre radius, arm-tip radius, inner-corner radius, pocket depth
    horn_screws=(1.5, 6.0),             # diameter, depth from the underside (under each arm tip)
    centre=(7.0,),                       # centre hole above the pocket
    **hole_group_params(DESIGNED),
)


def horn_star(arms: int, R: float, r: float, rc: float):
    """The horn pocket's outline: `arms` arms, each a round tip (radius r at radius R) with straight
    flanks back to the inner corners (radius rc, between the arms)."""
    from build123d import BuildSketch, Circle, Locations, Polygon

    with BuildSketch() as sk:
        corners = [(rc * math.cos(math.radians(360 / arms * (k + 0.5))), rc * math.sin(math.radians(360 / arms * (k + 0.5))))
                   for k in range(arms)]
        Polygon(*corners, align=None)
        for k in range(arms):
            a = math.radians(360 / arms * k)
            P = (R * math.cos(a), R * math.sin(a))
            u = (math.cos(a), math.sin(a))                      # the arm's axis
            pts = []
            for C in (corners[k - 1], corners[k]):
                dx, dy = P[0] - C[0], P[1] - C[1]
                d = math.hypot(dx, dy)
                base = math.atan2(dy, dx)
                off = math.acos(r / d)
                # the two tangent points seen from C; keep the one on C's side of the arm's axis
                cand = [(P[0] - r * math.cos(base + s * off), P[1] - r * math.sin(base + s * off)) for s in (1, -1)]
                side = u[0] * C[1] - u[1] * C[0]
                pts.append(max(cand, key=lambda q: side * (u[0] * (q[1] - P[1]) - u[1] * (q[0] - P[0]))))
            Polygon(corners[k - 1], pts[0], pts[1], corners[k], (0.0, 0.0), align=None)
            with Locations(P):
                Circle(r)
    return sk.sketch_local


def make(params: dict | None = None, **kw):
    from build123d import Cylinder, Plane, Pos, extrude, Mode

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown ring_servo_gear parameters: {sorted(unknown)}")
    fit, t = P["fit"], P["thickness"]
    body = extrude(rounded_spur(P["teeth"], P["tip"], P["fillet"], P["phase"]), amount=t)
    arms, R, r, rc, depth = P["horn"]
    body -= extrude(horn_star(arms, R, r + fit / 2, rc), amount=depth)
    feats: dict = {"underside": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, t), (0, 0, 1)),
                   "horn_seat": plane((0, 0, depth), (0, 0, -1))}
    cd = P["centre"][0] + fit
    body -= Pos(0, 0, (depth + t) / 2) * Cylinder(cd / 2, t - depth + 0.02)
    hole_features(feats, "centre", (0, 0, depth), (0, 0, 1), cd / 2, depth=t - depth)
    sd, sdep = P["horn_screws"]
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    for k in range(arms):
        a = math.radians(360 / arms * k)
        x, y = R * math.cos(a), R * math.sin(a)
        if types["horn"] == DESIGNED["horn"]:
            body -= Pos(x, y, sdep / 2) * Cylinder((sd + fit) / 2, sdep + 0.02)
            hole_features(feats, f"horn_screw{k + 1}", (x, y, 0), (0, 0, 1), (sd + fit) / 2, depth=sdep,
                          bolt=P["horn_bolt"], kind="tapped")
        else:
            body = cut_hole(body, feats, f"horn_screw{k + 1}", (x, y, depth), (0, 0, 1), P["horn_bolt"], types["horn"],
                            sdep - depth, fit, grow_boss=False)
    feats["axis"] = {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": P["fillet"][1]}
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("top down (z = thickness on the bed)", "top", False,
                                     "the horn pocket opens upward; no supports"))
