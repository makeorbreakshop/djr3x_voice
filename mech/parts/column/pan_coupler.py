"""The pan coupler (after Jason Charlton's round aluminium coupler up into the neck): turned 6061 on
the pan servo's goBILDA 1906 hub, the neck tube's bottom in its socket. A flange on the hub (four M4
counterbored down into the hub's tapped holes, under the tube's end), a 25 mm socket with a slit and an
M4 pinch bolt across it: the servo turns the neck directly (1:1)."""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import box, clearance_hole, cyl, cyl_y

DEFAULTS = dict(c=L.COUPLER)


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    C = P["c"]
    y0, ys, yt = C["flange_y0"], C["stop"], C["top"]
    body = cyl_y(0, 0, C["r"], y0, yt) - cyl_y(0, 0, C["bore_r"], ys, yt + 1)
    body = body - box(-C["slit"] / 2, C["slit"] / 2, ys + 4.0, yt + 1, 0, C["r"] + 1)      # the clamp slit (+Z)
    feats = {"seat": plane((0, y0, 0), (0, -1, 0)), "stop": plane((0, ys, 0), (0, 1, 0)),
             "bore": axis((0, yt, 0), (0, -1, 0), C["bore_r"]), "axis": axis((0, y0, 0), (0, 1, 0), C["r"])}
    for i, (dx, dz) in enumerate([(L.HUB_TAP_R, 0), (0, L.HUB_TAP_R), (-L.HUB_TAP_R, 0), (0, -L.HUB_TAP_R)]):
        body = body - cyl((dx, ys - C["cb_depth"], dz), (0, 1, 0), C["cb_r"], C["cb_depth"] + 0.01)
        body = clearance_hole(body, feats, f"hub{i + 1}", (dx, ys - C["cb_depth"], dz), (0, -1, 0), "M4",
                              C["flange_t"] - C["cb_depth"])
    # the pinch bolt across the slit (M4, along X, 10 above the socket's top)
    yp = yt - 8.0
    body = clearance_hole(body, feats, "pinch", (C["r"], yp, (C["bore_r"] + C["r"]) / 2), (-1, 0, 0), "M4", 2 * C["r"])
    return finish(body, label="Pan coupler (turned 6061)", params=P, features=feats, reference="",
                  printability=Print("n/a (turned 6061)", "seat", False, "lathe part; four counterbored M4, slit, pinch bolt"))
