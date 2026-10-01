"""The Gil plate: David Ferreira's Gil drive base plate, the plate the column stands on.

Built to the dimensions measured from Morton's "Gil-Drive-with-Base-Plate_Electronics stack v8.stl"
(parts/column/_layout.py GIL): 169.7 x 169.7 x 8.0 mm, corners R 17.25, its own eight 6.0 mm holes
at (+-80, +-64) / (+-64, +-80) (left free: they are the drive's), and four 5.5 mm holes for the
column's foot plate inside them. Only its height is inferred (in Morton's skid plate's window).
"""

from __future__ import annotations

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import clearance_hole, cyl, cyl_y

DEFAULTS = dict(top_y=L.GIL["top_y"], t=L.GIL["t"], half=L.GIL["half"], corner_r=L.GIL["corner_r"],
                holes=L.GIL["holes"], hole_d=L.GIL["hole_d"], bolt_at=L.FOOT["bolt_at"], bolt=L.GIL["foot_bolt"])


def make(params: dict | None = None, **kw):
    from ._cad import box

    P = {**DEFAULTS, **(params or {}), **kw}
    y1 = P["top_y"]
    y0 = y1 - P["t"]
    h, R = P["half"], P["corner_r"]
    body = box(-h + R, h - R, y0, y1, -h, h) + box(-h, h, y0, y1, -h + R, h - R)
    for sx in (-1, 1):
        for sz in (-1, 1):
            body = body + cyl_y(sx * (h - R), sz * (h - R), R, y0, y1)
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "bottom": plane((0, y0, 0), (0, -1, 0))}
    for a, b in P["holes"]:
        for sx in (-1, 1):
            for sz in (-1, 1):
                body = body - cyl((sx * a, y0 - 1, sz * b), (0, 1, 0), P["hole_d"] / 2, P["t"] + 2)
    c = P["bolt_at"]
    for i, (x, z) in enumerate([(sx * c, sz * c) for sx in (-1, 1) for sz in (-1, 1)]):
        body = clearance_hole(body, feats, f"foot{i + 1}", (x, y1, z), (0, -1, 0), P["bolt"], P["t"])
    return finish(body, label="Gil base plate (Ferreira; measured)", params=P, features=feats, reference="",
                  printability=Print("n/a (aluminium plate)", "bottom", False, "the Gil drive's plate"))
