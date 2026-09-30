"""Head-lift slide platform (Anderson, `slide-platform`) - the carriage plate the head lift rides on.

The neck support platform's plate (an MGN12H carriage's 20 x 20 M3 pattern, a shallow pocket with
two M3 holes) with a 9 mm side wall along its length - grooved on its outer face and fixed through
the groove by three M4 - and an end wall.

Frame = the STEP's: plate underside z = 0, x = -width .. 0, y = y0 .. y1; the side wall at -X, the
end wall at -Y.
"""

from __future__ import annotations

from parts.head._common import Print, finish, hole_d, hole_features, plane

from . import HOLE_SIZES

REFERENCE = "r3x-internal - slide-platform.step"
LABEL = "Slide platform (parametric)"

DEFAULTS = dict(
    fit=0.0,
    x0=-48.0, y=(-77.0, 10.0), plate_t=8.0, height=43.0,
    side_wall=9.0,                       # at x0, full length and height
    groove=(5.0, 11.0, 32.0),            # outer face groove: depth, z from, z to
    end_wall=6.0,                        # at y0, full width and height
    bolt="M3", hole_sizes=HOLE_SIZES,
    carriage=((-19.5, -31.0), 20.0),     # MGN12H: centre, square pitch
    pocket=((-36.0, -3.0), (-63.5, -50.5), 2.0),
    pocket_holes=((-30.0, -57.0), (-9.0, -57.0)),
    wall_bolt="M4", wall_holes=(21.5, (-62.0, -33.5, -5.0)),   # z, y positions (through the groove's floor)
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown slide_platform parameters: {sorted(unknown)}")
    fit = P["fit"]
    x0 = P["x0"]
    y0, y1 = P["y"]
    t, H = P["plate_t"], P["height"]
    sw, ew = P["side_wall"], P["end_wall"]

    def box(xa, xb, ya, yb, za, zb):
        return Pos((xa + xb) / 2, (ya + yb) / 2, (za + zb) / 2) * Box(xb - xa, yb - ya, zb - za)

    body = box(x0, 0, y0, y1, 0, t) + box(x0, x0 + sw, y0, y1, 0, H) + box(x0, 0, y0, y0 + ew, 0, H)
    gd, gz0, gz1 = P["groove"]
    body -= box(x0 - 1, x0 + gd, y0 - 1, y1 + 1, gz0, gz1)
    (px0, px1), (py0, py1), pd = P["pocket"]
    body -= box(px0, px1, py0, py1, t - pd, t + 0.01)
    d3 = hole_d(P["bolt"], "clearance", fit, P["hole_sizes"])
    feats: dict = {"bottom": plane((x0 / 2, (y0 + y1) / 2, 0), (0, 0, -1)), "top": plane((x0 / 2, (y0 + y1) / 2, t), (0, 0, 1)),
                   "wall_face": plane((x0 + gd, (y0 + y1) / 2, (gz0 + gz1) / 2), (-1, 0, 0))}
    (cx, cy), pitch = P["carriage"]
    for i, (x, y) in enumerate(sorted((cx + sx * pitch / 2, cy + sy * pitch / 2) for sx in (-1, 1) for sy in (-1, 1))):
        body -= Pos(x, y, t / 2) * Cylinder(d3 / 2, t + 0.02)
        hole_features(feats, f"carriage{i + 1}", (x, y, 0), (0, 0, 1), d3 / 2, depth=t, bolt=P["bolt"], kind="clearance")
    for i, (x, y) in enumerate(P["pocket_holes"]):
        body -= Pos(x, y, (t - pd) / 2) * Cylinder(d3 / 2, t - pd + 0.02)
        hole_features(feats, f"pocket{i + 1}", (x, y, 0), (0, 0, 1), d3 / 2, depth=t - pd, bolt=P["bolt"], kind="clearance")
    d4 = hole_d(P["wall_bolt"], "clearance", fit, P["hole_sizes"])
    z, ys = P["wall_holes"]
    th = sw - gd
    for i, y in enumerate(ys):
        body -= Pos(x0 + gd + th / 2, y, z) * Rot(0, 90, 0) * Cylinder(d4 / 2, th + 0.02)
        hole_features(feats, f"wall{i + 1}", (x0 + gd, y, z), (1, 0, 0), d4 / 2, depth=th, bolt=P["wall_bolt"], kind="clearance")
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("plate down (z = 0 on the bed)", "bottom", True,
                                     "the side wall's outer groove is a 21 mm overhang: supports in the groove, or "
                                     "print it on the side wall's outer face"))
