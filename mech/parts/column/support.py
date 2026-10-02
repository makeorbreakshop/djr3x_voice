"""The column's ring plates: the body's rings carried on the column at their own levels (after
Jason Charlton's aluminium ring plates clamped to his column, Hunter Smoke's ring plate up his
tower, Sam Morton's lower and upper frame rings), instead of down the kit's printed pedestal.

    make(kind="core")    the core plate (6061, 6 mm) under the lower lazy susan's inner race, in the
                         place of the pedestal cap P_M_3's top (the kit's guide p11: P_M_3 is for
                         static droids, "a working build would instead mount to an internal frame"):
                         LS_IC_1 clamps the race to it with the kit's four screws, now into its
                         tapped holes, so the lower ring (on the race's outer side) and the middle
                         ring (on LS_IC_1's pillars) stand on the column
    make(kind="top")     the top-ring plate (6061, 6 mm) under the top lazy susan's lower race
                         carrier TR-MR_SC: the race's four screws run on through the carrier's flange
                         into its tapped holes; open at the back round the top ring's drive and sector
    make(kind="bracket", sx=1, sz=1, y_top=...)   a 2020 corner bracket under a plate, on a post's
                         outer X face (one M5 into a T-nut in the post, one M5 up through the plate)

Both plates: a square cut-out round the posts (the sled runs inside them), a notch on the right
(-X) for the lift servo and its hanger over the travel. Numbers: _layout.SUPPORT.
"""

from __future__ import annotations

from parts.head._common import Print, finish, plane

from . import _layout as L
from ._cad import at_angle, box, clearance_hole, cyl_y, tapped_hole

DEFAULTS = dict(kind="core", sx=1, sz=1, y_top=None, support=L.SUPPORT, bracket=L.BRACKET, half=L.HALF)


def _wedge(a0, a1, r0, r1, y0, y1):
    """The annular sector r0..r1, a0..a1 deg (from +Z toward +X, as at_angle), y0..y1."""
    from ._cad import polygon_y

    n = max(4, int(abs(a1 - a0) / 4))
    outer = [at_angle(r1, a0 + (a1 - a0) * i / n) for i in range(n + 1)]
    inner = [at_angle(r0, a1 - (a1 - a0) * i / n) for i in range(n + 1)] if r0 > 0 else [(0.0, 0.0)]
    return polygon_y(outer + inner, y0, y1)


def bracket_points(level: str = "core", B=None):
    """The plate's bracket bolts (x, z): one per post, on the post's outer X face's side."""
    B = B or L.BRACKET
    return [(sx * (L.HALF + B.get("plate_hole", B["leg"] / 2)), sz * L.POST_C) for sx in (-1, 1) for sz in (-1, 1)]


def _plate(level: str, S, B):
    lv = S[level]
    y1 = lv["y_top"]
    y0 = y1 - S["t"]
    body = cyl_y(0, 0, lv["r"], y0, y1)
    h = S["hole_half"]
    body = body - box(-h, h, y0 - 1, y1 + 1, -h, h)
    nx0, nz0, nz1 = S["notch"]
    body = body - box(nx0, -L.HALF + 0.1, y0 - 1, y1 + 1, nz0, nz1)
    for a0, a1, r0 in lv.get("open", ()):
        body = body - _wedge(a0, a1, r0, lv["r"] + 1, y0 - 1, y1 + 1)
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "under": plane((0, y0, 0), (0, -1, 0))}
    for i, a in enumerate(lv["screws"]):
        x, z = at_angle(S["screw_r"], a)
        body = tapped_hole(body, feats, f"race{i + 1}", (x, y1, z), (0, -1, 0), "M4", S["t"])
    for i, (x, z) in enumerate(bracket_points(level, B)):
        body = clearance_hole(body, feats, f"brk{i + 1}", (x, y1, z), (0, -1, 0), "M5", S["t"])
    return body, feats


def _bracket(sx, sz, y_top, B):
    """An L: the vertical leg on the post's outer X face (x = sx*50), the horizontal leg under the plate."""
    xf = sx * L.HALF
    x_out = xf + sx * B["leg"]
    zc = sz * L.POST_C
    yv0 = y_top - B["leg"]
    v = box(*sorted((xf, xf + sx * B["t"])), yv0, y_top, zc - B["w"] / 2, zc + B["w"] / 2)
    hz = box(*sorted((xf, x_out)), y_top - B["t"], y_top, zc - B["w"] / 2, zc + B["w"] / 2)
    body = v + hz
    feats = {"post_face": plane((xf, yv0 + B["leg"] / 2, zc), (-sx, 0, 0)),
             "plate_face": plane((xf + sx * B["leg"] / 2, y_top, zc), (0, 1, 0))}
    yp = yv0 + (B["leg"] - B["t"]) / 2
    body = clearance_hole(body, feats, "post", (xf + sx * B["t"], yp, zc), (-sx, 0, 0), "M4", B["t"])
    xh = xf + sx * B.get("plate_hole", B["leg"] / 2)
    body = clearance_hole(body, feats, "plate", (xh, y_top, zc), (0, -1, 0), "M5", B["t"])
    feats["nut_face"] = plane((xh, y_top - B["t"], zc), (0, -1, 0))
    return body, feats


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    k = P["kind"]
    if k in ("core", "top"):
        body, feats = _plate(k, P["support"], P["bracket"])
        label = "Core plate (lower + middle ring races)" if k == "core" else "Top-ring plate (top lazy susan's lower race)"
        pr = Print("n/a (waterjet 6061)", "under", False, "aluminium, tapped M4 x 4")
    elif k == "bracket":
        y_top = P["y_top"] if P["y_top"] is not None else P["support"]["core"]["y_top"] - P["support"]["t"]
        body, feats = _bracket(int(P["sx"]), int(P["sz"]), float(y_top), P["bracket"])
        label = "2020 corner bracket (cast aluminium)"
        pr = Print("n/a (purchased)", "post_face", False, "purchased")
    else:
        raise ValueError(k)
    return finish(body, label=label, params={kk: v for kk, v in P.items()}, features=feats, reference="", printability=pr)

