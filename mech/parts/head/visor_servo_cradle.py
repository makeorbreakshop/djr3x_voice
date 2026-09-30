"""Visor servo cradle (ours, new): holds a standard servo (40 x 20 mm body, flange on top) on
Hunter's mount plate where the visor drive search put it. A 3 mm sleeve around the body under
the flange (the flange rests on the sleeve, M3 screws into the sleeve's tap holes) and a leg
down to the plate.

Design frame = the servo's canonical frame (parts/library.py: spline top at 0, output +Y, long
side +X with the body centre at +10, flange underside at y = -16.9); `leg_y` is where the leg
ends: `leg_dir` is "down" (toward the plate) in that frame, one of +-X or +-Z, and `leg_len`
how far past the sleeve the plate is (both measured by the assembly).
"""

from __future__ import annotations

from ._common import HOLES, Print, finish, plane

REFERENCE = None
LABEL = "Visor servo cradle (parametric)"

DEFAULTS = dict(body=(40.0, 20.0), body_x0=-10.0, flange_y=-16.9, sleeve_h=20.0, wall=3.0, fit=0.3,
                holes=((-14.0, -4.9), (-14.0, 4.9), (34.0, -4.9), (34.0, 4.9)), bolt="M3",
                leg_dir=(1.0, 0.0, 0.0), leg_len=0.0, leg_w=12.0)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    bl, bw = P["body"]
    x0 = P["body_x0"] - P["fit"] / 2
    x1 = P["body_x0"] + bl + P["fit"] / 2
    zw = bw / 2 + P["fit"] / 2
    w = P["wall"]
    y1 = P["flange_y"]
    y0 = y1 - P["sleeve_h"]

    def box(xa, xb, ya, yb, za, zb):
        return Pos((xa + xb) / 2, (ya + yb) / 2, (za + zb) / 2) * Box(xb - xa, yb - ya, zb - za)

    # the sleeve, with end blocks under the flange's screw ears
    part = box(x0 - w - 4.5, x1 + w + 4.5, y0, y1, -zw - w, zw + w) - box(x0, x1, y0 - 1, y1 + 1, -zw, zw)
    d = HOLES[P["bolt"]]["tap"]
    for x, z in P["holes"]:
        part -= Pos(x, y1 - 5, z) * Rot(90, 0, 0) * Cylinder(d / 2, 10.01)
    # the leg: out of the sleeve's side toward the plate, sleeve-high, leg_w wide
    xc = (x0 + x1) / 2
    ym = (y0 + y1) / 2
    dx, _, dz = P["leg_dir"]
    L = P["leg_len"]
    lw = P["leg_w"]
    foot = None
    if L > 0.5:
        if abs(dx) > 0.5:
            xa = x1 + w + 4.5 if dx > 0 else x0 - w - 4.5 - L
            part += box(xa, xa + L, y0, y1, -lw / 2, lw / 2)
            foot = ((xa + L) if dx > 0 else xa, ym, 0.0)
        else:
            za = zw + w if dz > 0 else -zw - w - L
            part += box(xc - lw / 2, xc + lw / 2, y0, y1, za, za + L)
            foot = (xc, ym, (za + L) if dz > 0 else za)
    feats = {"flange_seat": plane((xc, y1, 0), (0, 1, 0))}
    if foot is not None:
        feats["leg_foot"] = plane(foot, P["leg_dir"])
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("upright, leg foot on the bed", "leg_foot", False, "sleeve walls 3 mm"))
