"""The lift's rack: Mod 0.8, 20 deg, a 10 mm face, fixed to the back-right post's inner face (the
column's rear, where Jason Charlton's is); the carriage's lift servo climbs it with a 48T brass
servo gear (lift_pinion.py). Teeth only where the pinion meets them (y 362..470: the lift's -37..+45
plus a few teeth); plain lands at the ends with an M3 counterbored into a T-nut each. Modelled to
spec (goBILDA-style aluminium gear rack, Mod 0.8; cut to length)."""

from __future__ import annotations

import math

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import box, cyl

DEFAULTS = dict(rack=L.RACK)


def make(params: dict | None = None, **kw):
    from build123d import BuildSketch, Plane, Polygon, extrude

    P = {**DEFAULTS, **(params or {}), **kw}
    R = P["rack"]
    m = R["module"]
    x0, x1 = R["x"] - R["face"] / 2, R["x"] + R["face"] / 2
    z_root, z_tip = R["pitch_z"] - 1.25 * m, R["pitch_z"] + m
    body = box(x0, x1, R["y0"], R["y1"], R["z_base"], z_root)
    p = math.pi * m
    t = math.tan(math.radians(20))
    hp = p / 4  # half tooth thickness at the pitch line
    pts = [(R["teeth_y"][0], z_root - 0.01)]
    y = R["teeth_y"][0] + p / 2
    while y + p / 2 <= R["teeth_y"][1]:
        pts += [(y - hp - 1.25 * m * t, z_root), (y - hp + m * t, z_tip), (y + hp - m * t, z_tip), (y + hp + 1.25 * m * t, z_root)]
        y += p
    pts.append((pts[-1][0], z_root - 0.01))
    pl = Plane(origin=(x0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
    with BuildSketch(pl) as s:
        Polygon(*pts, align=None)
    body = body + extrude(s.sketch, amount=R["face"])
    feats = {"base": plane((R["x"], (R["y0"] + R["y1"]) / 2, R["z_base"]), (0, 0, -1)),
             "pitch": plane((R["x"], (R["y0"] + R["y1"]) / 2, R["pitch_z"]), (0, 0, 1))}
    for i, ys in enumerate(R["screws_y"]):
        floor = R["z_base"] + R["cb_floor"]
        body = body - cyl((R["x"], ys, R["z_base"] - 0.01), (0, 0, 1), 1.7, z_root - R["z_base"] + 0.02)
        body = body - cyl((R["x"], ys, floor), (0, 0, 1), 3.0, z_root - floor + 0.01)
        feats[f"hole_s{i + 1}"] = axis((R["x"], ys, floor), (0, 0, -1), 1.7, depth=R["cb_floor"], bolt="M3", kind="clearance")
        feats[f"face_s{i + 1}"] = plane((R["x"], ys, floor), (0, 0, 1))
    return finish(body, label="Lift rack, Mod 0.8", params={k: v for k, v in P.items() if k != "rack"} | {"rack": dict(R)},
                  features=feats, reference="", printability=Print("n/a (aluminium rack)", "base", False, "purchased, cut"))
