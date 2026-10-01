"""Purchased parts the column uses that have no vendor CAD in the cache, modelled to their published
dimensions (cad = parametric), each in its own canonical frame. The assembly places them.

    make(kind="rail", length_mm=180)   MGN12 rail: along +Y from 0, base on z = 0, 12 x 8, M3 holes
                                       (3.5 thru, 6.0 x 4.5 counterbore) every 25 mm from 10
    make(kind="block")                 MGN12H carriage: centred on y = 0, rail base at z = 0; body
                                       27 x 45.4, z 3..13, a channel over the rail, 4 x M3 x 4 deep
                                       (20 x 20; the tapped holes drawn at the tap drill)
    make(kind="tnut", thread="M3")     drop-in T-nut for 2020 (5 series), in the slot frame: across t =
                                       x, depth s = z (the post's face at s = 10, its centre at 0),
                                       along the slot = y (10 long); filling the T cavity up
                                       through the lips (thread 5 mm, drawn at the tap drill)
    make(kind="collar")                one-piece clamp collar, 30 mm bore, 45 OD x 13 (its clamp
                                       screw belongs to it): axis +Y from 0
"""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L

DEFAULTS = dict(kind="rail", length_mm=L.RAIL["length"], thread="M3")


def make(params: dict | None = None, **kw):
    from ._cad import box, cyl, polygon_z

    P = {**DEFAULTS, **(params or {}), **kw}
    k = P["kind"]
    feats: dict = {}
    if k == "rail":
        Ln = P["length_mm"]
        body = box(-6, 6, 0, Ln, 0, 8)
        y = L.RAIL["first"]
        n = 0
        while y < Ln - 1:
            n += 1
            body = body - cyl((0, y, -0.01), (0, 0, 1), 1.75, 8.02) - cyl((0, y, 3.5), (0, 0, 1), 3.0, 4.6)
            feats[f"hole_r{n}"] = axis((0, y, 3.5), (0, 0, -1), 1.75)
            feats[f"face_r{n}"] = plane((0, y, 3.5), (0, 0, 1))
            y += L.RAIL["pitch"]
        feats["top"] = plane((0, Ln / 2, 8), (0, 0, 1))
        feats["base"] = plane((0, Ln / 2, 0), (0, 0, -1))
        label = f"MGN12 rail {Ln:g} mm"
    elif k == "block":
        B = L.BLOCK
        body = box(-B["w"] / 2, B["w"] / 2, -B["l"] / 2, B["l"] / 2, B["h1"], B["h"]) - box(-6.0, 6.0, -B["l"], B["l"], 0, 8.0)
        for i, (x, y) in enumerate([(sx * 10, sy * 10) for sx in (-1, 1) for sy in (-1, 1)]):
            body = body - cyl((x, y, B["h"] - L.MGN_THREAD), (0, 0, 1), 1.25, L.MGN_THREAD + 0.01)
            feats[f"thr{i + 1}"] = axis((x, y, B["h"]), (0, 0, -1), 1.25, depth=L.MGN_THREAD)
        feats["top"] = plane((0, 0, B["h"]), (0, 0, 1))
        feats["roof"] = plane((0, 0, 8.0), (0, 0, -1))
        label = "MGN12H carriage"
    elif k == "tnut":
        tap = {"M3": 1.25, "M4": 1.667, "M5": 2.1}[P["thread"]]
        pts = [(-2.95, 9.9), (2.95, 9.9), (2.95, 8.15), (5.35, 8.15), (5.35, 6.3), (3.05, 4.9), (-3.05, 4.9),
               (-5.35, 6.3), (-5.35, 8.15), (-2.95, 8.15)]
        body = _slot_frame(polygon_z(pts, -5, 5))
        body = body - cyl((0, 0, 3.0), (0, 0, 1), tap, 8.0)
        feats["thread"] = axis((0, 0, 9.9), (0, 0, -1), tap, depth=9.9 - 4.9)
        feats["seat"] = plane((0, 0, 8.15), (0, 0, 1))
        label = f"Drop-in T-nut {P['thread']} (2020)"
    elif k == "collar":
        body = cyl((0, 0, 0), (0, 1, 0), 22.5, 13.0) - cyl((0, -1, 0), (0, 1, 0), 15.0, 15.0)
        body = body - box(-0.5, 0.5, -1, 14, 15.0 - 0.5, 23)                  # the clamp slit
        feats["bore"] = axis((0, 0, 0), (0, 1, 0), 15.0)
        feats["top"] = plane((0, 13.0, 0), (0, 1, 0))
        label = "Clamp collar 30 mm bore, 45 x 13"
    else:
        raise ValueError(k)
    return finish(body, label=label, params=P, features=feats, reference="",
                  printability=Print("n/a (purchased)", next(iter(feats)) if feats else "", False, "purchased"))


def _slot_frame(shape):
    """(t, s, along) modelled as (x, y, z) -> the slot frame (x = t, y = along, z = s)."""
    import numpy as np

    from parts.head._common import transform

    m = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)  # x->x, y->z, z->y (a reflection)
    m[:3, 2] *= -1  # z -> -y: a proper rotation (det +1); the T-nut is symmetric along the slot
    return transform(shape, m)
