"""The ring drives' brackets: printed PETG L-brackets on the column's two back posts (the
"support plates at the ring heights"). An upright against the posts' back faces (four M4 into
T-nuts in the back slots) and a shelf out to r 96 (inside the kit's static core LS_IC_1 at r 98.5
and the top ring's r 98.5) that the servo hangs from: spline down, its flange under the shelf,
four M3 down through the shelf and the flange into lock nuts. The pinion and the servo's 1906 hub
sit under the flange (ring_pinion.py).

    make()               the lower ring's (shelf at y 401.6)
    make(ring="top")     the top ring's   (shelf at y 490.3)
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import box, clearance_hole, cyl_y

DEFAULTS = dict(ring="lower", c=L.RING_PINION_C, t=L.DRIVE_PLATE["t"], r_max=L.DRIVE_PLATE["r_max"],
                back=L.DRIVE_PLATE["back"], up_h=30.0, half_x=L.HALF, shelf_half=34.0, post=L.POST_C)


def flange_holes(cx, cz):
    """Spline-down servo, long side along +X: canonical (long, across) -> body (cx + long, cz - across)."""
    return [(cx + cl, cz - cw) for cl, cw in L.FLANGE_HOLES]


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    R = L.RING[P["ring"]]
    (cx, cz), t = P["c"], P["t"]
    y0, y1 = R["plate"]
    zb = -L.HALF
    # shelf: out from the posts' back faces to r_max, round the servo body
    shelf = box(-P["shelf_half"], P["shelf_half"], y0, y1, -P["r_max"], zb) & cyl_y(0, 0, P["r_max"], y0 - 1, y1 + 1)
    shelf = shelf - box(cx - 10.5, cx + 30.5, y0 - 1, y1 + 1, cz - 10.5, cz + 10.5)
    up = box(-P["half_x"], P["half_x"], y0, y0 + P["up_h"], zb - P["back"], zb)
    body = shelf + up
    feats = {"shelf_under": plane((cx, y0, cz), (0, -1, 0)), "up_face": plane((0, y0 + 10, zb), (0, 0, 1))}
    for i, (x, z) in enumerate(flange_holes(cx, cz)):
        body = clearance_hole(body, feats, f"fl{i + 1}", (x, y1, z), (0, -1, 0), "M3", t)
    k = 0
    for sx in (-1, 1):
        for y in R["post_y"]:
            k += 1
            body = clearance_hole(body, feats, f"post{k}", (sx * P["post"], y, zb - P["back"]), (0, 0, 1), "M4", P["back"])
            feats[f"seat{k}"] = plane((sx * P["post"], y, zb), (0, 0, 1))
    return finish(body, label=f"Ring drive bracket ({P['ring']})", params=P, features=feats, reference="",
                  printability=Print("upright's back face down", "up_face", False, "an L lying on its upright: no supports"))
