"""The ring drives' pinions: Anderson's rounded teeth (parts/anderson/_gear.py, his
`lower-ring-servo-gear` / `top-ring-servo-gear` tooth fits, so they mesh his sectors on the rings),
10 mm, printed PETG; in place of his six-arm horn pocket, four M4 clearance holes over a goBILDA
1906 hub's tapped holes (the servo hangs spline-down above the pinion), screwed up from below.

    make()                 the lower ring's (25 teeth, at its place under Anderson's sector)
    make(ring="top")       the top ring's (20 teeth)

`turn` phases the teeth to his sector at rest; the holes stay on the hub."""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import clearance_hole, cyl_y

DEFAULTS = dict(ring="lower", turn=0.0, thick=L.RING_PINION_T, c=L.RING_PINION_C)


def make(params: dict | None = None, **kw):
    from build123d import Pos, Rot, extrude

    from parts.anderson._gear import rounded_spur

    P = {**DEFAULTS, **(params or {}), **kw}
    R = L.RING[P["ring"]]
    (cx, cz), t, y0 = P["c"], P["thick"], R["y0"]
    face = rounded_spur(R["teeth"], R["tip"], R["fillet"], R["phase"] + P["turn"])
    body = Pos(cx, y0, cz) * Rot(-90, 0, 0) * extrude(face, amount=t)
    body = body - cyl_y(cx, cz, 4.5, y0 - 1, y0 + t + 1)
    feats = {"top": plane((cx, y0 + t, cz), (0, 1, 0)), "bottom": plane((cx, y0, cz), (0, -1, 0)),
             "axis": axis((cx, y0, cz), (0, 1, 0), R["fillet"][1])}
    for i, (dx, dz) in enumerate([(L.HUB_TAP_R, 0), (0, L.HUB_TAP_R), (-L.HUB_TAP_R, 0), (0, -L.HUB_TAP_R)]):
        body = clearance_hole(body, feats, f"hub{i + 1}", (cx + dx, y0, cz + dz), (0, 1, 0), "M4", t)
    return finish(body, label=f"Ring pinion ({P['ring']}, {R['teeth']}T)", params=P, features=feats, reference="",
                  printability=Print("flat", "bottom", False, "no supports"))
