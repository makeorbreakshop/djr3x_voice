"""The lift's drive pulley: GT2 60T (pitch radius 19.10 mm, 0.333 mm of belt per servo degree),
printed PETG, 6 mm belt between two flanges, bolted to the lift servo's goBILDA 1906 hub with four
M4 into the hub's tapped holes. The teeth are not modelled: the band is drawn at the teeth's tip
radius (the belt's inner face)."""

from __future__ import annotations

import math

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import clearance_hole, cyl

DEFAULTS = dict(c=L.DRIVE_C, z=L.BELT["z"], r_tip=L.R_DRIVE - L.BELT["tooth_pd_off"], band=7.0, flange_t=1.5,
                flange_r=21.5, bore_r=4.5)


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    (x, y), zc = P["c"], P["z"]
    b, ft = P["band"], P["flange_t"]
    z0, z1 = zc - b / 2 - ft, zc + b / 2 + ft
    body = (cyl((x, y, z0), (0, 0, 1), P["flange_r"], ft) + cyl((x, y, zc - b / 2), (0, 0, 1), P["r_tip"], b)
            + cyl((x, y, zc + b / 2), (0, 0, 1), P["flange_r"], ft))
    body = body - cyl((x, y, z0 - 1), (0, 0, 1), P["bore_r"], z1 - z0 + 2)
    feats = {"hub_face": plane((x, y, z1), (0, 0, 1)), "axis": axis((x, y, z0), (0, 0, 1), P["bore_r"]),
             "band": axis((x, y, zc - b / 2), (0, 0, 1), P["r_tip"])}
    # the 1906 hub's tapped holes, on its own axes (hub canonical X -> body +X, Z -> body +Y)
    for i, (dx, dy) in enumerate([(L.HUB_TAP_R, 0), (0, L.HUB_TAP_R), (-L.HUB_TAP_R, 0), (0, -L.HUB_TAP_R)]):
        body = clearance_hole(body, feats, f"hub{i + 1}", (x + dx, y + dy, z0), (0, 0, 1), "M4", z1 - z0)
    return finish(body, label="GT2 60T drive pulley", params=P, features=feats, reference="",
                  printability=Print("flange down", "hub_face", True, "the far flange overhangs 2.4 mm: print in "
                                     "two halves bolted together, or with supports"))
