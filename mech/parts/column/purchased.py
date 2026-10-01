"""Purchased parts the column uses that have no vendor CAD in the cache, modelled to their published
dimensions (cad = parametric), each in its own canonical frame. The assembly places them.

    make(kind="vwheel")                OpenBuilds solid V-wheel (24.39 OD, 10.23 wide, two 625 bearings):
                                       axis +Z, centred; its V drawn to bear on the V-slot's flanks with
                                       the tip 1.5 mm in the slot (_layout.VWHEEL)
    make(kind="ecc")                   eccentric spacer, 6 mm, 10 OD (5 mm bore 0.8 off centre): axis +Z
                                       from 0
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

DEFAULTS = dict(kind="vwheel", thread="M3")


def make(params: dict | None = None, **kw):
    from ._cad import box, cyl, polygon_z

    P = {**DEFAULTS, **(params or {}), **kw}
    k = P["kind"]
    feats: dict = {}
    if k == "vwheel":
        from build123d import Axis, BuildSketch, Plane, Polygon, revolve

        W = L.VWHEEL
        R, hw, tip = W["od"] / 2, W["w"] / 2, W["tip_hw"]
        r_rim = R - (hw - tip)                       # the V's 45 deg flanks run from the faces to the tip
        pts = [(W["bore"] / 2, -hw), (r_rim, -hw), (R, -tip), (R, tip), (r_rim, hw), (W["bore"] / 2, hw)]
        with BuildSketch(Plane.XZ) as sk:
            Polygon(*pts, align=None)
        body = revolve(sk.sketch, Axis.Z)
        feats["bore"] = axis((0, 0, -hw), (0, 0, 1), W["bore"] / 2)
        feats["face_a"] = plane((0, 0, -hw), (0, 0, -1))
        feats["face_b"] = plane((0, 0, hw), (0, 0, 1))
        label = "V-wheel, solid (OpenBuilds), 24.4 x 10.2"
    elif k == "ecc":
        E = L.ECC
        body = cyl((0, 0, 0), (0, 0, 1), E["od"] / 2, E["l"]) - cyl((0.79, 0, -0.01), (0, 0, 1), 2.6, E["l"] + 0.02)
        feats["bore"] = axis((0.79, 0, 0), (0, 0, 1), 2.6)
        feats["bottom"] = plane((0, 0, 0), (0, 0, -1))
        feats["top"] = plane((0, 0, E["l"]), (0, 0, 1))
        label = "Eccentric spacer 6 mm (V-wheel preload)"
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
