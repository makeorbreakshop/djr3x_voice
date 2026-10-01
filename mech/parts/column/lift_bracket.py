"""The lift servo's bracket: a printed PETG upright on the foot plate. The goBILDA 2000's flange
bears on its back face (the servo lies spline to the back, long side up, inside the column's
foot); four M3 through the flange and the upright into lock nuts; a base flange with four M4
into the foot plate's tapped holes."""

from __future__ import annotations

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import box, clearance_hole

SPLINE_Z = L.BELT["z"] + 5.0 + L.HUB_H - L.SPLINE_ABOVE_BOSS      # -33.6: the servo's spline top (z)
FLANGE_Z = SPLINE_Z - L.FLANGE_Y[0]                             # -16.7: the flange's face on the upright

DEFAULTS = dict(y_foot=L.Y_FOOT_TOP, flange_z=FLANGE_Z, t=8.0, base_t=6.0, half_x=18.0, y_top=28.0,
                window=(10.5, -25.5, 15.5), holes=None, base_holes=((-14.0, -1.0), (14.0, -1.0), (-14.0, 9.0), (14.0, 9.0)),
                base_z1=14.0)


def flange_holes(P=None):
    """The servo's flange holes in the body frame: (x, y) on the upright's back face."""
    yc = L.DRIVE_C[1]
    return [(-cz, yc + cl) for cl, cz in L.FLANGE_HOLES]


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    yf, zf, t = P["y_foot"], P["flange_z"], P["t"]
    hx = P["half_x"]
    up = box(-hx, hx, yf, P["y_top"], zf, zf + t)
    wx, wy0, wy1 = P["window"]
    up = up - box(-wx, wx, wy0, wy1, zf - 1, zf + t + 1)
    base = box(-hx, hx, yf, yf + P["base_t"], zf, P["base_z1"])
    body = up + base
    feats = {"bottom": plane((0, yf, 0), (0, -1, 0)), "face": plane((0, 0, zf), (0, 0, -1))}
    for i, (x, y) in enumerate(flange_holes()):
        body = clearance_hole(body, feats, f"fl{i + 1}", (x, y, zf), (0, 0, 1), "M3", t)
    for i, (x, z) in enumerate(P["base_holes"]):
        body = clearance_hole(body, feats, f"base{i + 1}", (x, yf + P["base_t"], z), (0, -1, 0), "M4", P["base_t"])
    return finish(body, label="Lift servo bracket", params=P, features=feats, reference="",
                  printability=Print("base flange down", "bottom", False, "an L: no supports"))
