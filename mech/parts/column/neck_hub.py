"""The pan hub: turned 6061, the head's pan axis. Two 6806-2RS on its 30 mm journal in the
carriage's housing, a flange on the upper bearing's inner race (the head's weight), a clamp collar
under the lower one; the pan gear on the flange (three M3 into tapped holes); the neck tube in its
26 mm bore, held by an M4 cross through-bolt and lock nut above the gear (the pan torque in shear)."""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import at_angle, clearance_hole, cyl, cyl_y, tapped_hole

DEFAULTS = dict(hub=L.HUB, flange=L.Y_FLANGE, cross_y=L.CROSS_Y, screws=(90.0, 210.0, 330.0))


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    Hb = P["hub"]
    f0, f1 = P["flange"]
    body = cyl_y(0, 0, Hb["r"], Hb["y0"], Hb["top"]) + cyl_y(0, 0, Hb["flange_r"], f0, f1)
    body = body - cyl_y(0, 0, Hb["bore_r"], Hb["y0"] - 1, Hb["top"] + 1)
    feats = {"journal": axis((0, Hb["y0"], 0), (0, 1, 0), Hb["r"]), "bore": axis((0, Hb["top"], 0), (0, -1, 0), Hb["bore_r"]),
             "flange_under": plane((0, f0, 0), (0, -1, 0)), "flange_top": plane((0, f1, 0), (0, 1, 0)),
             "bottom": plane((0, Hb["y0"], 0), (0, -1, 0))}
    for i, a in enumerate(P["screws"]):
        x, z = at_angle(Hb["screw_r"], a)
        body = tapped_hole(body, feats, f"gear{i + 1}", (x, f1, z), (0, -1, 0), "M3", f1 - f0)
    y = P["cross_y"]
    body = clearance_hole(body, feats, "cross", (0, y, Hb["r"]), (0, 0, -1), "M4", 2 * Hb["r"])
    feats["cross_far"] = plane((0, y, -Hb["r"]), (0, 0, -1))
    return finish(body, label="Pan hub", params=P, features=feats, reference="",
                  printability=Print("n/a (turned 6061)", "bottom", False, "lathe part; tapped M3 x3, cross-drilled 4.5"))
