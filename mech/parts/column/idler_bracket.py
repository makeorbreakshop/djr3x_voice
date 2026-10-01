"""The belt idler's bracket: printed PETG, hung under the column's top plate in the back gap
between the back posts, the GT2 20T idler between its two ears on an M5 through-bolt with a lock
nut (a pivot: through-bolt + nut). Two M4 heat-set inserts in its top for the top plate's screws."""

from __future__ import annotations

from parts.head._common import Print, cut_hole, finish, plane

from . import _layout as L
from ._cad import box, clearance_hole

DEFAULTS = dict(c=L.IDLER_C, z=L.BELT["z"], half_x=14.0, ear_out=9.75, ear_in=4.75, y0=550.0, y1=L.Y_POST_TOP,
                slot_top=572.0, inserts=7.0)


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    (cx, cy), zc = P["c"], P["z"]
    hx = P["half_x"]
    body = box(cx - hx, cx + hx, P["y0"], P["y1"], zc - P["ear_out"], zc + P["ear_out"])
    body = body - box(cx - hx - 1, cx + hx + 1, P["y0"] - 1, P["slot_top"], zc - P["ear_in"], zc + P["ear_in"])
    feats = {"top": plane((cx, P["y1"], zc), (0, 1, 0)),
             "ear_in_b": plane((cx, cy, zc - P["ear_in"]), (0, 0, 1)), "ear_in_f": plane((cx, cy, zc + P["ear_in"]), (0, 0, -1))}
    body = clearance_hole(body, feats, "axle", (cx, cy, zc - P["ear_out"]), (0, 0, 1), "M5", 2 * P["ear_out"])
    for i, dx in enumerate((-P["inserts"], P["inserts"])):
        body = cut_hole(body, feats, f"ins{i + 1}", (cx + dx, P["y1"], zc), (0, -1, 0), "M4", "heat_set",
                        L.INSERT_M4["length_mm"] + 1.0)
    return finish(body, label="Belt idler bracket", params=P, features=feats, reference="",
                  printability=Print("top face down", "top", False, "the ears rise from the bed; no supports"))
