"""The short neck: 26 x 1.5 mm 6061 tube from the pan hub (its bottom 7 mm above the hub's) up into
Hunter's coupler (its top on the coupler's bore stop, y 696.3: the head where it is today). One
4.5 mm cross hole for the hub's M4 cross bolt."""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import clearance_hole, cyl_y

DEFAULTS = dict(tube=L.TUBE, cross_y=L.CROSS_Y)


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    T = P["tube"]
    ro, ri = T["od"] / 2, T["od"] / 2 - T["wall"]
    body = cyl_y(0, 0, ro, T["y0"], T["top"]) - cyl_y(0, 0, ri, T["y0"] - 1, T["top"] + 1)
    feats = {"axis": axis((0, T["y0"], 0), (0, 1, 0), ro), "top": plane((0, T["top"], 0), (0, 1, 0)),
             "bottom": plane((0, T["y0"], 0), (0, -1, 0))}
    body = clearance_hole(body, feats, "cross", (0, P["cross_y"], ro), (0, 0, -1), "M4", T["od"])
    return finish(body, label="Neck tube 26 x 1.5", params=P, features=feats, reference="",
                  printability=Print("n/a (6061 tube)", "bottom", False, f"cut to {T['top'] - T['y0']:.1f} mm, cross-drilled 4.5"))
