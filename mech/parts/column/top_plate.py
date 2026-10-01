"""The column's top plate: 6 mm 6061, 100 x 100, on the four post ends. The neck passes through
its centre hole (r 16 round the 26 mm tube)."""

from __future__ import annotations

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import box, clearance_hole, cyl_y

DEFAULTS = dict(y0=L.Y_POST_TOP, t=L.TOP["t"], half=L.HALF, post=L.POST_C, tube_r=L.TOP["tube_hole_r"])


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    y0, t, h = P["y0"], P["t"], P["half"]
    y1 = y0 + t
    body = box(-h, h, y0, y1, -h, h) - cyl_y(0, 0, P["tube_r"], y0 - 1, y1 + 1)
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "bottom": plane((0, y0, 0), (0, -1, 0))}
    c = P["post"]
    for i, (x, z) in enumerate([(sx * c, sz * c) for sx in (-1, 1) for sz in (-1, 1)]):
        body = clearance_hole(body, feats, f"post{i + 1}", (x, y1, z), (0, -1, 0), "M5", t)
    return finish(body, label="Column top plate", params=P, features=feats, reference="",
                  printability=Print("n/a (waterjet 6061)", "bottom", False, "aluminium"))
