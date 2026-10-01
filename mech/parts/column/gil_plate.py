"""The Gil plate: David Ferreira's aluminium base plate, the floor the column stands on.

inferred (no file holds its outline; see _layout.py): a 6.35 mm 6061 disc, r 227.5 (flush with the
kit skirt's foot), its top at y -54.3 (the skirt's underside), three Gil-drive wheel slots. Holes:
the column foot plate's four M5 bolts. The rest of the plate (r 106..208 outside the foot) is free
for the electronics and the battery.
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import clearance_hole, cyl_y, polygon_y

DEFAULTS = dict(top_y=L.GIL["top_y"], t=L.GIL["t"], r=L.GIL["r"], slots=L.GIL["slots"], bolt_at=L.FOOT["bolt_at"],
                bolt=L.GIL["foot_bolt"])


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    y1 = P["top_y"]
    y0 = y1 - P["t"]
    body = cyl_y(0, 0, P["r"], y0, y1)
    angles, r0, r1, w = P["slots"]
    for a in angles:
        t = math.radians(a)
        u, v = (math.sin(t), math.cos(t)), (math.cos(t), -math.sin(t))
        pts = [(u[0] * r + v[0] * s * w / 2, u[1] * r + v[1] * s * w / 2) for r, s in ((r0, -1), (r1, -1), (r1, 1), (r0, 1))]
        body = body - polygon_y(pts, y0 - 1, y1 + 1)
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "bottom": plane((0, y0, 0), (0, -1, 0))}
    b = P["bolt_at"]
    for i, (x, z) in enumerate([(sx * b, sz * b) for sx in (-1, 1) for sz in (-1, 1)]):
        body = clearance_hole(body, feats, f"foot{i + 1}", (x, y1, z), (0, -1, 0), P["bolt"], P["t"])
    return finish(body, label="Gil base plate (inferred outline)", params=P, features=feats, reference="",
                  printability=Print("n/a (waterjet 6061)", "bottom", False, "aluminium plate, not printed"))
