"""The clock-spring cassette: the head's cables cross the pan joint here. A flat ribbon (the head
servos' and eyes' conductors laminated, or a flat flex cable) spirals between the neck tube (its
inner end clipped to the tube, r ~15) and this case's wall (its outer end fixed, r ~26), and leaves
through a slot to the carriage's service cable. A ribbon of length L winds L/(2 pi) (1/15 - 1/26)
turns between tight on the tube and loose on the wall: 0.75 turn (the pan's +-135) needs 166 mm;
we specify 250 mm, which allows +-200 deg. Printed PETG, on three M4 standoffs from the carriage's
ears (M4 screws down through its floor's ears into them)."""

from __future__ import annotations

import math

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import at_angle, box, clearance_hole, cyl_y, polygon_y

DEFAULTS = dict(case=L.CASE, standoff=L.STANDOFF, ear=L.EAR, ribbon_mm=250.0)


def ribbon_turns(length, r_in=L.CASE["inner_r"], r_out=L.CASE["r"] - L.CASE["wall"]):
    """Turns a spiral ribbon of `length` allows between wound tight on r_in and loose on r_out."""
    return length / (2 * math.pi) * (1 / r_in - 1 / r_out)


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    C, S, E = P["case"], P["standoff"], P["ear"]
    y0, y1 = C["y0"], C["y0"] + C["h"]
    body = cyl_y(0, 0, C["r"], y0, y1)
    body = body - cyl_y(0, 0, C["r"] - C["wall"], y0 + C["floor"], y1 - C["floor"])
    body = body - cyl_y(0, 0, C["hole_r"], y0 - 1, y0 + C["floor"] + 0.01)
    body = body - cyl_y(0, 0, C["top_hole_r"], y1 - C["floor"] - 0.01, y1 + 1)
    body = body - box(-4.0, 4.0, y0 + C["floor"], y1 - C["floor"], -C["r"] - 1, -C["r"] + C["wall"] + 0.5)   # ribbon exit
    feats = {"floor_under": plane((0, y0, 0), (0, -1, 0))}
    for i, a in enumerate(S["angles"]):
        t = math.radians(a)
        u, v = (math.sin(t), math.cos(t)), (math.cos(t), -math.sin(t))
        w, r0, r1 = E["w"] / 2, C["r"] - 3.0, E["r"]
        pts = [(u[0] * r + v[0] * s * w, u[1] * r + v[1] * s * w) for r, s in ((r0, -1), (r1, -1), (r1, 1), (r0, 1))]
        body = body + polygon_y(pts, y0, y0 + C["floor"])
        x, z = at_angle(S["r"], a)
        body = clearance_hole(body, feats, f"so{i + 1}", (x, y0 + C["floor"], z), (0, -1, 0), "M4", C["floor"])
        feats[f"so_under{i + 1}"] = plane((x, y0, z), (0, -1, 0))
    P["turns_allowed"] = round(ribbon_turns(P["ribbon_mm"]), 3)
    return finish(body, label="Clock-spring cassette", params=P, features=feats, reference="",
                  printability=Print("floor down", "floor_under", True, "the lid bridges 11.5 mm round the tube "
                                     "hole: print the lid as a separate ring and glue it, or bridge it"))
