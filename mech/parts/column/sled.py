"""The lift sled (after Jason Charlton's build): the inner assembly round the neck that glides up and
down the column's four V-slot posts on eight V-wheels, carries the lift servo behind a plate on its
right side and the pan servo under its bottom plate, and holds the neck in two bearings in its top
plate. Every piece is modelled in the body frame at rest (_layout.py):

    make(kind="front") / "back"     goBILDA-pattern grid plates (aluminium) square to Z between the
                                    posts, four V-wheel axles each (M5, the wheels on eccentric spacers
                                    outside, in the posts' inner X slots)
    make(kind="side_l") / "side_r"  goBILDA-pattern grid plates square to X, tying front and back
    make(kind="bottom")             6061 plate under the box: the pan servo hangs under it, spline up on
                                    the axis (its case's top through a cut-out), a tab for the service cable
    make(kind="top")                the bearing housing (two 6806-2RS) in a plate inside the box's top;
                                    four standoff holes for the clock-spring cassette
    make(kind="hanger")             the lift servo's hanger (printed PETG): a plate behind its flange, an
                                    arm to the right side plate above the pinion and one under the bottom
                                    plate below it

The grid plates are drawn to goBILDA's pattern (4 mm holes on an 8 mm grid); their thickness and the
part numbers (or a cut-down plate) are to choose: inferred.
"""

from __future__ import annotations

import numpy as np

from parts.head._common import Print, finish, plane, axis

from . import _layout as L
from ._cad import at_angle, box, clearance_hole, cyl, cyl_y

DEFAULTS = dict(kind="front")


def _grid(body, origin, u, v, u_span, v_span, keep_out, margin=4.0):
    """goBILDA-pattern holes (4 mm on an 8 mm grid, centred) through a plate: `origin` its centre on
    the back face, u/v its in-plane axes, n = u x v its thickness direction; holes within `keep_out`
    [(point, r)] are left out."""
    from build123d import Compound

    g, r, t = L.SLED["grid"], L.SLED["grid_r"], L.SLED["t"]
    origin, u, v = (np.asarray(x, float) for x in (origin, u, v))
    n = np.cross(u, v)
    cuts = []
    nu, nv = int((u_span / 2 - margin) // g), int((v_span / 2 - margin) // g)
    for i in range(-nu, nu + 1):
        for j in range(-nv, nv + 1):
            p = origin + u * i * g + v * j * g
            if any(np.linalg.norm(p - np.asarray(q)) < rr for q, rr in keep_out):
                continue
            cuts.append(cyl(p - n * 0.5, n, r, t + 1.0))
    return body - Compound(cuts) if cuts else body


def _front_back(sz):
    S, W = L.SLED, L.VWHEEL
    z0, z1 = sorted((sz * S["z_in"], sz * S["z_out"]))
    y0, y1 = S["y0"], S["y1"]
    body = box(-S["half_x"], S["half_x"], y0, y1, z0, z1)
    feats = {"inner": plane((0, (y0 + y1) / 2, sz * S["z_in"]), (0, 0, -sz)),
             "outer": plane((0, (y0 + y1) / 2, sz * S["z_out"]), (0, 0, sz)),
             "bottom": plane((0, y0, sz * (S["z_in"] + S["t"] / 2)), (0, -1, 0))}
    keep = []
    k = 0
    for yw in L.WHEEL_Y:
        for sx in (-1, 1):
            k += 1
            # entered from the inside face: the axle screw's head bears there and it runs out through the
            # eccentric spacer and the wheel to its nut (assemblies/column: col_scr_axle_*)
            p = (sx * L.WHEEL_X, yw, sz * S["z_in"])
            body = clearance_hole(body, feats, f"axle{k}", p, (0, 0, sz), "M5", S["t"])
            keep.append(((sx * L.WHEEL_X, yw, sz * S["z_in"]), 6.0))
    body = _grid(body, (0, (y0 + y1) / 2, sz * S["z_in"]), (1, 0, 0), (0, 1, 0), 2 * S["half_x"], y1 - y0, keep)
    return body, feats


def _side(sx):
    S = L.SLED
    sx_in = S["side_x_l"] if sx > 0 else S["side_x"]
    x0, x1 = sorted((sx * sx_in, sx * (sx_in + S["t"])))
    y0, y1 = S["y0"], S["y1"]
    body = box(x0, x1, y0, y1, -S["z_in"], S["z_in"])
    feats = {"inner": plane((sx * sx_in, (y0 + y1) / 2, 0), (-sx, 0, 0)),
             "outer": plane((sx * (sx_in + S["t"]), (y0 + y1) / 2, 0), (sx, 0, 0)),
             "bottom": plane((sx * (sx_in + S["t"] / 2), y0, 0), (0, -1, 0)),
             "top_edge": plane((sx * (sx_in + S["t"] / 2), y1, 0), (0, 1, 0))}
    keep = []
    if sx < 0:  # the hanger's top arm bolts here (two M4)
        _, ys, zs = L.LIFT_SPLINE
        ya = sum(L.HANGER["arm_top"]) / 2
        for i, dz in enumerate((-6.0, 6.0)):
            p = (x0, ya, zs + dz)
            body = clearance_hole(body, feats, f"arm{i + 1}", p, (1, 0, 0), "M4", S["t"])
            keep.append(((sx * sx_in, ya, zs + dz), 6.0))
    body = _grid(body, (sx * sx_in, (y0 + y1) / 2, 0), (0, 0, 1), (0, 1, 0), 2 * S["z_in"], y1 - y0, keep)
    if sx > 0:  # the pan servo's two +X flange screws come up under this plate: a relief over their lock nuts
        (fx, fz0), (_, fz1) = sorted(pan_flange_holes())[-2:]
        rz = max(abs(fz0), abs(fz1)) + 4.0                       # the M3 nut's corners (3.1) + 0.9
        body = body - box(x0 - 0.1, x1 + 0.1, y0 - 0.1, y0 + 6.0, -rz, rz)
        feats["pan_relief"] = plane((sx * (sx_in + S["t"] / 2), y0 + 6.0, 0), (0, -1, 0))
    return body, feats


def pan_flange_holes():
    """The pan servo's flange holes (x, z): spline on the axis, long side +X."""
    return [(cl, cw) for cl, cw in L.FLANGE_HOLES]


def _bottom():
    S, B, T = L.SLED, L.BOTTOM, L.TAB
    y0, y1 = B["y0"], B["y1"]
    body = box(-S["half_x"], S["half_x"], y0, y1, -S["z_out"], S["z_out"])
    body = body + box(S["half_x"] - 1, 40.5, y0, y1, -10.0, 10.0)          # under the pan servo's +X ears
    body = body + box(-T["half_x"], T["half_x"], y0, y1, S["z_out"] - 1, T["z1"])   # the service cable's tab
    # the case's top through the plate (spline on the axis, long side +X: case x -10..30, z +-10)
    body = body - box(-10.5, 30.5, y0 - 1, y1 + 1, -10.5, 10.5)
    # the servo case's chamfer rises 1 mm off the flange's top at each end (goBILDA STEP, measured): a 1 mm step
    # milled into the plate's underside over it
    body = body - box(-12.6, 32.6, y0 - 1, y0 + 1.0, -10.5, 10.5)
    body = body - box(-17.0, 37.0, y0 - 1, y0 + 1.0, -1.2, 1.2)     # and its centre rib, out to the flange's ends
    feats = {"top": plane((0, y1, 0), (0, 1, 0)), "under": plane((0, y0, 0), (0, -1, 0)),
             "cable_under": plane((L.COIL["x"], y0, L.COIL["z"]), (0, -1, 0))}
    for i, (x, z) in enumerate(pan_flange_holes()):
        body = clearance_hole(body, feats, f"pf{i + 1}", (x, y0, z), (0, 1, 0), "M3", B["t"])
    _, _, zs = L.LIFT_SPLINE
    for i, (x, dz) in enumerate(((-29.0, -6.0), (-29.0, 6.0))):
        body = clearance_hole(body, feats, f"arm{i + 1}", (x, y0, zs + dz), (0, 1, 0), "M4", B["t"])
    return body, feats


def _top():
    S, H, T = L.SLED, L.HOUSING, L.TOP_PLATE_SLED
    body = box(-S["side_x"], S["side_x_l"], T["y0"], T["y1"], -S["z_in"], S["z_in"])
    body = body + cyl_y(0, 0, H["r"], H["y0"], H["y1"])
    lo, up = L.Y_BRG_LO, L.Y_BRG_UP
    feats = {"seat_lo": axis((0, H["y0"], 0), (0, 1, 0), H["seat_r"]), "seat_up": axis((0, H["y1"], 0), (0, -1, 0), H["seat_r"]),
             "shoulder_lo": plane((0, lo[1], 0), (0, -1, 0)), "shoulder_up": plane((0, up[0], 0), (0, 1, 0)),
             "top": plane((0, T["y1"], 0), (0, 1, 0))}
    for i, a in enumerate(L.STANDOFF["angles"]):
        x, z = at_angle(L.STANDOFF["r"], a)
        body = clearance_hole(body, feats, f"so{i + 1}", (x, T["y0"], z), (0, 1, 0), "M4", T["t"])
        feats[f"so_top{i + 1}"] = plane((x, T["y1"], z), (0, 1, 0))
    body = body - cyl_y(0, 0, H["seat_r"], H["y0"] - 1, lo[1]) - cyl_y(0, 0, H["seat_r"], up[0], H["y1"] + 1)
    body = body - cyl_y(0, 0, H["mid_r"], H["y0"] - 1, H["y1"] + 1)
    return body, feats


def lift_flange_holes():
    """The lift servo's flange holes: (y, z); its long side runs down (-Y), across is +Z."""
    _, y, z = L.LIFT_SPLINE
    return [(y - cl, z + cw) for cl, cw in L.FLANGE_HOLES]


def lift_flange_x():
    """(back face, spline-side face) x of the lift servo's flange."""
    x = L.LIFT_SPLINE[0]
    return x + L.FLANGE_Y[0], x + L.FLANGE_Y[1]


def _hanger():
    K, S = L.HANGER, L.SLED
    xb, _ = lift_flange_x()
    _, ys, zs = L.LIFT_SPLINE
    px0, px1 = xb - K["t"], xb
    body = box(px0, px1, K["arm_bot"][0], K["y_top"], zs - K["half_z"], zs + K["half_z"])
    body = body + box(px0, -(S["side_x"] + S["t"]), K["arm_top"][0], K["arm_top"][1], zs - 10.0, zs + 10.0)
    body = body + box(px0, K["arm_bot_x1"], K["arm_bot"][0], K["arm_bot"][1], zs - 10.0, zs + 10.0)
    body = body - box(px0 - 1, px1 + 1, ys - 30.5, ys + 10.5, zs - 10.5, zs + 10.5)
    feats = {"hanger_face": plane((px1, ys, zs), (1, 0, 0)),
             "arm_face": plane((-(S["side_x"] + S["t"]), sum(K["arm_top"]) / 2, zs), (1, 0, 0)),
             "arm_top_face": plane((-29.0, K["arm_bot"][1], zs), (0, 1, 0))}
    for i, (y, z) in enumerate(lift_flange_holes()):
        body = clearance_hole(body, feats, f"lh{i + 1}", (px1, y, z), (-1, 0, 0), "M3", K["t"])
    return body, feats


def make(params: dict | None = None, **kw):
    P = {**DEFAULTS, **(params or {}), **kw}
    k = P["kind"]
    if k in ("front", "back"):
        body, feats = _front_back(1 if k == "front" else -1)
        label, pr = f"Sled {k} plate (goBILDA-pattern grid plate)", Print("n/a (aluminium)", "inner", False, "purchased / waterjet")
    elif k in ("side_l", "side_r"):
        body, feats = _side(1 if k == "side_l" else -1)
        label, pr = f"Sled side plate ({k[-1]})", Print("n/a (aluminium)", "inner", False, "purchased / waterjet")
    elif k == "bottom":
        body, feats = _bottom()
        label, pr = "Sled bottom plate (pan servo)", Print("n/a (6061)", "under", False, "waterjet")
    elif k == "top":
        body, feats = _top()
        label, pr = "Sled top plate + bearing housing", Print("n/a (6061)", "top", False, "machined (bearing seats)")
    elif k == "hanger":
        body, feats = _hanger()
        label, pr = "Lift servo hanger", Print("plate on the bed", "hanger_face", True, "the arms bridge: supports under them")
    else:
        raise ValueError(k)
    return finish(body, label=label, params=P, features=feats, reference="", printability=pr)

