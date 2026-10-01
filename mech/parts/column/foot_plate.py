"""The column's foot plate: 6.35 mm 6061, 150 x 150, on the Gil plate.

Holes: the four posts' M5 flat heads from below (countersunk, flush: the plate sits on the Gil
plate), four M5 clearance holes on to the Gil plate, a cable hole for the sled's service cable (the column's front opening).
"""

from __future__ import annotations

from parts.head._common import Print, finish, hole_features, plane

from . import _layout as L
from ._cad import box, clearance_hole, cyl, tapped_hole, cyl_y

DEFAULTS = dict(y0=L.Y_GIL_TOP, t=L.FOOT["t"], half=L.FOOT["half"], post=L.POST_C, bolt_at=L.FOOT["bolt_at"],
                cable=(L.COIL["x"], L.COIL["z"], 7.0))


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    y0, t, h = P["y0"], P["t"], P["half"]
    y1 = y0 + t
    body = box(-h, h, y0, y1, -h, h)
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "bottom": plane((0, y0, 0), (0, -1, 0))}
    c = P["post"]
    for i, (x, z) in enumerate([(sx * c, sz * c) for sx in (-1, 1) for sz in (-1, 1)]):
        # M5 flat head (ISO 10642: head 10 dia, 90 deg) from below: countersink + clearance
        body = body - cyl((x, y0 - 0.01, z), (0, 1, 0), 2.75, t + 0.02)
        from build123d import Cone, Pos, Rot

        body = body - Pos(x, y0 + 1.25 - 0.005, z) * Rot(-90, 0, 0) * Cone(5.2, 2.7, 2.5)
        hole_features(feats, f"post{i + 1}", (x, y0, z), (0, 1, 0), 2.75, depth=t, bolt="M5", kind="countersunk")
    b = P["bolt_at"]
    for i, (x, z) in enumerate([(sx * b, sz * b) for sx in (-1, 1) for sz in (-1, 1)]):
        body = clearance_hole(body, feats, f"gil{i + 1}", (x, y1, z), (0, -1, 0), "M5", t)
    cx, cz, cr = P["cable"]
    body = body - cyl_y(cx, cz, cr, y0 - 1, y1 + 1)
    return finish(body, label="Column foot plate", params=P, features=feats, reference="",
                  printability=Print("n/a (waterjet 6061, tapped M4, countersunk M5)", "bottom", False, "aluminium"))
