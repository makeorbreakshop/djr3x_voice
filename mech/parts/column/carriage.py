"""The lift carriage: one printed PETG part that rides the two MGN12H blocks and carries the pan.

* a bearing housing on the axis for the hub's two 6806-2RS (30 x 42 x 7), each standing 1.5 mm
  proud of its end so only the inner races touch the rotating hub flange and collar;
* a front plate on the blocks (8 x M3 into the blocks' threads, heads to the front);
* a web from the housing to the front plate, through the gap between the front posts;
* a cradle on the +X side for the pan servo (goBILDA 2000, spline up), its flange on the cradle,
  four M3 into lock nuts under it;
* a belt clamp on the back-left: the belt's clamped run passes through a slot the width and
  thickness of the belt (toothed in print; the teeth are not modelled);
* three ears on top of the housing for the clock-spring cassette's standoffs.
"""

from __future__ import annotations

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import at_angle, box, clearance_hole, cyl_y

DEFAULTS = dict(housing=L.HOUSING, ear=L.EAR, standoff=L.STANDOFF, front=L.FRONT, web=L.WEB, cradle=L.CRADLE,
                clamp=L.CLAMP, rail_x=L.RAIL["x"], block_holes=L.BLOCK["holes"], yc=L.YC)


def cradle_y(P=None):
    """(bottom, top) of the cradle: the pan servo's flange sits on its top."""
    top = L.PAN_SERVO_SPLINE[1] + L.FLANGE_Y[0]
    return top - L.CRADLE["t"], top


def pan_flange_holes():
    sx, _, sz = L.PAN_SERVO_SPLINE
    return [(sx + cl, sz + cz) for cl, cz in L.FLANGE_HOLES]


def belt_x():
    """(outer, inner) faces of the clamped run (x)."""
    inner = -(L.R_DRIVE - L.BELT["tooth_pd_off"])
    return inner - L.BELT["thick"], inner


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    H, E, S, F, W, C, K = P["housing"], P["ear"], P["standoff"], P["front"], P["web"], P["cradle"], P["clamp"]
    feats: dict = {}
    # housing: bearing seats at both ends, a smaller bore between (the races' shoulders)
    body = cyl_y(0, 0, H["r"], H["y0"], H["y1"])
    lo, up = L.Y_BRG_LO, L.Y_BRG_UP
    feats["seat_lo"] = axis((0, H["y0"], 0), (0, 1, 0), H["seat_r"])
    feats["seat_up"] = axis((0, H["y1"], 0), (0, -1, 0), H["seat_r"])
    feats["shoulder_lo"] = plane((0, lo[1], 0), (0, -1, 0))
    feats["shoulder_up"] = plane((0, up[0], 0), (0, 1, 0))
    # ears for the cassette's standoffs
    y_ear = (H["y1"] - E["t"], H["y1"])
    for i, a in enumerate(S["angles"]):
        import math

        t = math.radians(a)
        u, v = (math.sin(t), math.cos(t)), (math.cos(t), -math.sin(t))
        w, r0, r1 = E["w"] / 2, H["r"] - 3.0, E["r"]
        pts = [(u[0] * r + v[0] * s * w, u[1] * r + v[1] * s * w) for r, s in ((r0, -1), (r1, -1), (r1, 1), (r0, 1))]
        from ._cad import polygon_y

        body = body + polygon_y(pts, *y_ear)
        x, z = at_angle(S["r"], a)
        body = clearance_hole(body, feats, f"so{i + 1}", (x, y_ear[0], z), (0, 1, 0), "M4", E["t"])
        feats[f"so_top{i + 1}"] = plane((x, y_ear[1], z), (0, 1, 0))
    # web and front plate
    body = body + box(-W["half"], W["half"], W["y0"], W["y1"], W["z0"], F["z0"])
    body = body + box(-F["half"], F["half"], F["y0"], F["y1"], F["z0"], F["z0"] + F["t"])
    feats["front_back"] = plane((0, P["yc"], F["z0"]), (0, 0, -1))
    feats["web_under"] = plane((L.COIL["x"], W["y0"], L.COIL["z"]), (0, -1, 0))
    k = 0
    for sx in (-1, 1):
        for dx in (-P["block_holes"] / 2, P["block_holes"] / 2):
            for dy in (-P["block_holes"] / 2, P["block_holes"] / 2):
                k += 1
                body = clearance_hole(body, feats, f"blk{k}", (sx * P["rail_x"] + dx, P["yc"] + dy, F["z0"] + F["t"]),
                                      (0, 0, -1), "M3", F["t"])
    # pan servo cradle
    c0, c1 = cradle_y()
    sx_, _, sz_ = L.PAN_SERVO_SPLINE
    body = body + box(C["x0"], C["x1"], c0, c1, -C["half_z"], C["half_z"])
    body = body - box(sx_ - 10.5, sx_ + 30.5, c0 - 1, c1 + 1, sz_ - 10.5, sz_ + 10.5)
    feats["cradle_top"] = plane((sx_, c1, sz_), (0, 1, 0))
    for i, (x, z) in enumerate(pan_flange_holes()):
        body = clearance_hole(body, feats, f"cr{i + 1}", (x, c1, z), (0, -1, 0), "M3", C["t"])
    # belt clamp: a block round the clamped run, the belt through a slot its own size
    bo, bi = belt_x()
    zb, bw = L.BELT["z"], L.BELT["width"]
    body = body + box(K["x0"], K["x1"], K["y0"], K["y1"], K["z0"], K["z1"])
    body = body + box(-16.0, K["x1"], max(K["y0"], H["y0"]), K["y1"], K["z1"] - 0.01, -20.0)
    body = body - box(bo, bi, K["y0"] - 1, K["y1"] + 1, zb - bw / 2, zb + bw / 2)
    # the housing's bores last (the web, the cradle and the clamp's bridge reach into its wall)
    body = body - cyl_y(0, 0, H["seat_r"], H["y0"] - 1, lo[1]) - cyl_y(0, 0, H["seat_r"], up[0], H["y1"] + 1)
    body = body - cyl_y(0, 0, H["mid_r"], H["y0"] - 1, H["y1"] + 1)
    feats["clamp_slot"] = plane((bi, (K["y0"] + K["y1"]) / 2, zb), (-1, 0, 0))   # the slot's inner wall (the belt's back)
    return finish(body, label="Lift carriage", params=P, features=feats, reference="",
                  printability=Print("front plate down", "front_back", True,
                                     "front plate on the bed, the housing standing up; supports under the cradle and "
                                     "the clamp (or print the cradle and the clamp as separate bolted pieces)"))
