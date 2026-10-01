"""The pan's spur gears, 1:1: module 2, 25 teeth, 20 deg pressure angle, 8 mm, printed PETG (an
involute pair, bd_warehouse SpurGear).

    make()                  the hub gear: on the pan hub's flange, bore 30.1 round the hub's
                            journal, three M3 clearance holes into the flange's tapped holes
    make(kind="pinion")     the servo's: on the pan servo's goBILDA 1906 hub, four M4 clearance
                            holes over the hub's tapped holes, 50 mm away (+X)

`turn` (deg about the gear's own axis) phases the teeth so the pair meshes at rest; the holes stay
where the hub puts them.
"""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import at_angle, clearance_hole, cyl_y

DEFAULTS = dict(kind="hub", module=L.PAN_GEAR["module"], teeth=L.PAN_GEAR["teeth"], thick=L.PAN_GEAR["thick"],
                y0=L.G0, turn=0.0, cd=L.PAN_CD, bore_r=L.HUB["r"] + 0.05, screw_r=L.HUB["screw_r"], screws=(90.0, 210.0, 330.0))


def make(params: dict | None = None, **kw):
    from bd_warehouse.gear import SpurGear
    from build123d import Align, Pos, Rot

    P = {**DEFAULTS, **(params or {}), **kw}
    y0, t = P["y0"], P["thick"]
    cx = 0.0 if P["kind"] == "hub" else P["cd"]
    g = SpurGear(P["module"], P["teeth"], 20.0, t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    # gear axis +Z -> body +Y: Rot(-90, 0, 0) maps +Z to +Y; turn about the gear's own axis first
    body = Pos(cx, y0, 0) * Rot(-90, 0, 0) * Rot(0, 0, P["turn"]) * g
    feats = {"bottom": plane((cx, y0, 0), (0, -1, 0)), "top": plane((cx, y0 + t, 0), (0, 1, 0)),
             "axis": axis((cx, y0, 0), (0, 1, 0), P["module"] * P["teeth"] / 2)}
    if P["kind"] == "hub":
        body = body - cyl_y(0, 0, P["bore_r"], y0 - 1, y0 + t + 1)
        feats["bore"] = axis((0, y0, 0), (0, 1, 0), P["bore_r"])
        for i, a in enumerate(P["screws"]):
            x, z = at_angle(P["screw_r"], a)
            body = clearance_hole(body, feats, f"scr{i + 1}", (x, y0 + t, z), (0, -1, 0), "M3", t)
    else:
        body = body - cyl_y(cx, 0, 4.5, y0 - 1, y0 + t + 1)
        for i, (dx, dz) in enumerate([(L.HUB_TAP_R, 0), (0, L.HUB_TAP_R), (-L.HUB_TAP_R, 0), (0, -L.HUB_TAP_R)]):
            body = clearance_hole(body, feats, f"hub{i + 1}", (cx + dx, y0 + t, dz), (0, -1, 0), "M4", t)
    return finish(body, label=f"Pan gear 25T m2 ({P['kind']})", params=P, features=feats, reference="",
                  printability=Print("flat", "bottom", False, "no supports; 100 % infill round the screw holes"))
