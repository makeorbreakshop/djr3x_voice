"""Shell support ring round the pedestal's foot (after Morton's lower frame and Hunter's 2025-08-06
tower): the kit's base top (B_T, underside y 160.25) and the pedestal standing on it rest on this
ring, and the ring is bolted to the column, so their weight goes into the column and the Gil plate
instead of through the printed skirt. Printed PETG, 320 mm across (fits the 325 mm bed), four tabs
down the posts' outer faces (front posts' fronts, back posts' backs), an M4 into a T-nut through
each; four cable holes."""

from __future__ import annotations

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import at_angle, box, clearance_hole, cyl_y

DEFAULTS = dict(bt=L.BASE_RING["bt"], y1=L.BASE_RING["y1"], t=L.BASE_RING["t"], r=L.BASE_RING["r"], half=L.HALF, gap=0.4,
                tab_t=L.BASE_RING["tab_t"], tab_h=L.BASE_RING["tab_h"], post=L.POST_C, cable=(4, 110.0, 12.0))


def tab_y(P):
    y0 = P["y1"] - P["t"]
    return y0 - P["tab_h"] / 2


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    y1 = P["y1"]
    y0 = y1 - P["t"]
    h = P["half"] + P["gap"]
    body = cyl_y(0, 0, P["r"], y0, y1) - box(-h, h, y0 - 1, y1 + 1, -h, h)
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "bottom": plane((0, y0, 0), (0, -1, 0))}
    c, H, tt = P["post"], P["half"], P["tab_t"]
    ty = tab_y(P)
    for i, (sx, sz) in enumerate([(-1, -1), (1, -1), (-1, 1), (1, 1)]):
        z0, z1 = (H, H + tt) if sz > 0 else (-H - tt, -H)
        body = body + box(sx * c - 8, sx * c + 8, y0 - P["tab_h"], y1, z0, z1)
        entry = (sx * c, ty, sz * (H + tt))
        body = clearance_hole(body, feats, f"tab{i + 1}", entry, (0, 0, -sz), "M4", tt)
        feats[f"seat{i + 1}"] = plane((sx * c, ty, sz * H), (0, 0, -sz))
    n, rc, rr = P["cable"]
    for k in range(n):
        x, z = at_angle(rc, 45 + 360 / n * k)
        body = body - cyl_y(x, z, rr, y0 - 1, y1 + 1)
    # the base top B_T screwed down into it: four M4 heat-set inserts in its top (ours: B_T is drilled to suit)
    from parts.head._common import hole_d, hole_features

    rb, angs = P["bt"]
    ri = hole_d("M4", "heatset") / 2
    for i, a in enumerate(angs):
        x, z = at_angle(rb, a)
        body = body - cyl_y(x, z, ri, y1 - 8.6, y1 + 1)
        hole_features(feats, f"bt{i + 1}", (x, y1, z), (0, -1, 0), ri, depth=8.6, bolt="M4", kind="insert")
    return finish(body, label="Shell support ring", params=P, features=feats, reference="",
                  printability=Print("top face down", "top", False, "the tabs rise from the bed; no supports"))
