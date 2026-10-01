"""The lift's pinion: a brass servo gear, Mod 0.8, 48 teeth (pitch radius 19.2: 0.335 mm of lift
per servo degree), 6 mm, on the lift servo's 25T spline with the servo's M3 centre screw (the
goBILDA brass servo gear pattern; modelled to spec, its part number to be confirmed). `turn` phases
its teeth to the rack at rest."""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import cyl

DEFAULTS = dict(turn=0.0, teeth=L.LIFT_PINION["teeth"], thick=L.LIFT_PINION["thick"], c=L.LIFT_PINION_C,
                module=L.RACK["module"])


def make(params: dict | None = None, **kw):
    from build123d import Pos, Rot

    from ._cad import involute_outline, polygon_z

    P = {**DEFAULTS, **(params or {}), **kw}
    xm, y, z = P["c"]
    t = P["thick"]
    x0, x1 = xm - t / 2, xm + t / 2
    g = polygon_z(involute_outline(P["teeth"], P["module"]), 0.0, t)   # (bd_warehouse's SpurGear fails at 48T Mod 0.8)
    body = Pos(x0, y, z) * Rot(0, 90, 0) * Rot(0, 0, P["turn"]) * g          # its axis +Z -> body +X
    spl = L.SPLINE_ABOVE_BOSS
    body = body - cyl((x0 - 0.01, y, z), (1, 0, 0), 3.0, spl + 0.21) - cyl((x0, y, z), (1, 0, 0), 1.7, t + 0.01)
    feats = {"seat": plane((x0, y, z), (-1, 0, 0)), "outer": plane((x1, y, z), (1, 0, 0)),
             "spline": {"type": "spline", "p": [x0 + spl, y, z], "d": [1.0, 0.0, 0.0], "teeth": 25},
             "hole_c": axis((x1, y, z), (-1, 0, 0), 1.7, depth=t - spl, bolt="M3", kind="clearance"),
             "face_c": plane((x1, y, z), (1, 0, 0)),
             "axis": axis((x0, y, z), (1, 0, 0), L.R_LIFT)}
    return finish(body, label=f"Lift pinion, brass, Mod 0.8, {P['teeth']}T", params=P, features=feats, reference="",
                  printability=Print("n/a (brass)", "seat", False, "purchased"))
