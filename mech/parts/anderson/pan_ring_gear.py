"""Pan ring-gear sector (Anderson, `headlift-base-mount`) - the internal gear segment the pan
servo's pinion walks round.

A 26 mm-deep annular sector (a 60-tooth internal gear, 16 teeth kept) between the root circle and
an outer arc: teeth with semicircular tips and straight flanks widening 1:4 to the root, r2 fillets
at every root corner, two M5 clearance holes to fix it.

Frame = the STEP's: gear axis +Z at the origin, z = 0 .. width; the sector from `sector[0]` to
`sector[1]` deg (x toward 0 deg).
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES

REFERENCE = "r3x-internal - headlift-base-mount.step"
LABEL = "Pan ring-gear sector (parametric)"

DEFAULTS = dict(
    fit=0.0,
    width=26.0, outer_r=127.5, root_r=111.5,
    sector=(176.138, 273.862),        # deg
    teeth=60, first_tooth=180.0, count=16,   # a 60-tooth ring, 16 teeth from `first_tooth`
    tip=(2.0, 105.5),                 # semicircular tip radius, its centre's radius
    flank_slope=0.25,                 # tooth half-width grows 0.25 per mm toward the root
    root_fillet=2.0,
    bolt="M5", hole_sizes=HOLE_SIZES, holes=(118.5, (180.0, 270.0)),
)


def make(params: dict | None = None, **kw):
    from build123d import BuildSketch, Circle, Cylinder, Locations, Mode, Polygon, Pos, extrude, fillet

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown pan_ring_gear parameters: {sorted(unknown)}")
    Ro, Rr = P["outer_r"], P["root_r"]
    a0, a1 = (math.radians(a) for a in P["sector"])
    tr, tc = P["tip"]
    k = P["flank_slope"]
    pol = lambda r, a: (r * math.cos(a), r * math.sin(a))
    with BuildSketch() as sk:
        # the sector between the root circle and the outer arc
        Circle(Ro)
        Circle(Rr, mode=Mode.SUBTRACT)
        big = 2 * Ro
        n = 32
        Polygon((0, 0), *[pol(big, a0 + (a1 - a0) * i / n) for i in range(n + 1)], align=None, mode=Mode.INTERSECT)
        for j in range(P["count"]):
            a = math.radians(P["first_tooth"] + 360.0 * j / P["teeth"])
            u = (math.cos(a), math.sin(a))
            v = (-u[1], u[0])
            r_out = Rr + 1.5
            lat = tr + (r_out - tc) * k
            Polygon((tc * u[0] + tr * v[0], tc * u[1] + tr * v[1]), (r_out * u[0] + lat * v[0], r_out * u[1] + lat * v[1]),
                    (r_out * u[0] - lat * v[0], r_out * u[1] - lat * v[1]), (tc * u[0] - tr * v[0], tc * u[1] - tr * v[1]),
                    align=None)
            with Locations((tc * u[0], tc * u[1])):
                Circle(tr)
        roots = [v for v in sk.vertices() if abs(math.hypot(v.X, v.Y) - Rr) < 1e-6]
        fillet(roots, P["root_fillet"])
    body = extrude(sk.sketch, amount=P["width"])
    w = P["width"]
    feats: dict = {"bottom": plane((0, 0, 0), (0, 0, -1)), "top": plane((0, 0, w), (0, 0, 1)),
                   "gear_axis": {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": tc}}
    d = hole_d(P["bolt"], "clearance", P["fit"], P["hole_sizes"])
    r, angs = P["holes"]
    for i, a in enumerate(angs):
        x, y = pol(r, math.radians(a))
        body -= Pos(x, y, w / 2) * Cylinder(d / 2, w + 0.02)
        hole_features(feats, f"h{i + 1}", (x, y, w), (0, 0, -1), d / 2, depth=w, bolt=P["bolt"], kind="clearance")
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flat (z = 0 on the bed)", "bottom", False, "an extruded profile: no supports"))
