"""The joint ring: a rounded rectangular frame with an M4 hole through each wall at mid-thickness.

Hunter ships this one solid twice under two names - `Custom Joint Piece.step` (the gimbal cross in
`Head Joint Asm.step`: the tilt pins go through its side walls, the roll pins through its end
walls) and `RX Neck Joint Member V1.stl` (same geometry: volume 3829 vs 3828.5 mm3, identical
bounds). `custom_joint_piece` and `neck_joint_member` wrap this with their own references and
mate names.

Frame = the references': length along Z, width along X, thickness along Y, centred.
"""

from __future__ import annotations

from ._common import hole_d

DEFAULTS = dict(
    fit=0.0,            # print fit, mm added to every hole diameter
    length=50.0,        # along Z (the roll axis in the gimbal)
    width=20.0,         # along X (the tilt axis)
    thick=8.0,          # along Y
    wall=4.0,           # frame wall (window = length - 2 wall x width - 2 wall)
    corner_r=3.0,       # outer corners
    window_r=3.0,       # window corners
    bolt="M4",
    pivot_hole=None,    # both pivot groups at once; as designed: tapped (the pivot screws self-thread)
    tilt_hole=None,     # the side walls' pivots: clearance = a through-bolt with a lock nut on the inner
                        # face (the workbench's choice for the gimbal cross); nut_trap = its nut sunk into
                        # the wall's inner face; heat_set; tapped
    roll_hole=None,     # the end walls' pivots, the same choices
    shoulder_d=None,    # a shoulder bolt: its shoulder diameter runs in the clearance hole
    hole=None,          # the older name for pivot_hole
    inserts=False,
    insert="M4-kit",
)
DESIGNED = {"tilt": "tapped", "roll": "tapped"}


def build(P: dict):
    """The ring (build123d Part) and its four holes: {key: (entry point, direction into the part,
    diameter, hole type)}. Keys: xp / xn through the side walls (the tilt pivots in the gimbal),
    zp / zn through the end walls (the roll pivots)."""
    import math

    from build123d import (Align, BuildPart, BuildSketch, Cylinder, Mode, Plane, RectangleRounded, RegularPolygon,
                           extrude)

    from ._common import INSERTS, NUTS, resolve_hole_types

    L, W, T, wl = P["length"], P["width"], P["thick"], P["wall"]
    if 2 * wl >= min(L, W):
        raise ValueError("wall too thick for the ring")
    if P.get("hole") and not P.get("pivot_hole"):
        P["pivot_hole"] = P["hole"]
    for g in ("tilt", "roll"):                      # pivot_hole sets both; tilt_hole / roll_hole one
        if P.get(f"{g}_hole") is None and P.get("pivot_hole") is not None:
            P[f"{g}_hole"] = P["pivot_hole"]
    types = resolve_hole_types(P, DESIGNED)
    P["hole"] = types["tilt"] if types["tilt"] == types["roll"] else None

    def dia(t):
        if t == "heat_set":
            return INSERTS[P["insert"]]["d"] + P["fit"]
        if t in ("clearance", "nut_trap") and P.get("shoulder_d"):
            return P["shoulder_d"] + P["fit"]           # a shoulder bolt's shoulder runs in the wall
        return hole_d(P["bolt"], "tap" if t == "tapped" else "clearance", P["fit"])

    pl = Plane.XZ.offset(-T / 2)          # origin at y = +T/2, normal -Y: extrude T down to -T/2
    with BuildPart() as bp:
        with BuildSketch(pl):
            RectangleRounded(W, L, P["corner_r"])
            RectangleRounded(W - 2 * wl, L - 2 * wl, P["window_r"], mode=Mode.SUBTRACT)
        extrude(amount=T)
    body = bp.part
    spec = {"zp": ((0.0, 0.0, L / 2), (0.0, 0.0, -1.0), "roll"), "zn": ((0.0, 0.0, -L / 2), (0.0, 0.0, 1.0), "roll"),
            "xp": ((W / 2, 0.0, 0.0), (-1.0, 0.0, 0.0), "tilt"), "xn": ((-W / 2, 0.0, 0.0), (1.0, 0.0, 0.0), "tilt")}
    holes = {}
    for k, (p, n, g) in spec.items():
        t = types[g]
        d = dia(t)
        body -= Plane(origin=p, z_dir=n).location * Cylinder(d / 2, wl + 0.02, align=(Align.CENTER, Align.CENTER, Align.MIN))
        if t == "nut_trap":                         # the nut in the wall's inner face
            af, nt = NUTS[P["bolt"]]
            inner = tuple(p[i] + n[i] * wl for i in range(3))
            with BuildSketch(Plane(origin=inner, z_dir=tuple(-v for v in n))) as hx:
                RegularPolygon((af + P["fit"]) / math.sqrt(3), 6)
            body -= extrude(hx.sketch, amount=min(nt + 0.2, wl - 0.6))
        holes[k] = (p, n, d, t)
    return body, holes

def resolve(params, kw, extra_defaults=None):
    P = dict(DEFAULTS)
    P.update(extra_defaults or {})
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS) - set(extra_defaults or {})
    if unknown:
        raise TypeError(f"unknown joint ring parameters: {sorted(unknown)}")
    return P
