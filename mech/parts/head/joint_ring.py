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
    hole="tap",         # 'tap': the pivot screws self-thread (Hunter) | 'heatset' | 'clearance'
)


def build(P: dict):
    """The ring (build123d Part) and its four hole centres/axes (entry point, direction into the part)."""
    from build123d import BuildPart, BuildSketch, Hole, Locations, Mode, Plane, RectangleRounded, extrude

    L, W, T, wl = P["length"], P["width"], P["thick"], P["wall"]
    if 2 * wl >= min(L, W):
        raise ValueError("wall too thick for the ring")
    d = hole_d(P["bolt"], P["hole"], P["fit"])
    # sketch on the XZ footprint (Plane.XZ: local (u, v) = (x, z), normal -Y), extruded symmetric in Y
    pl = Plane.XZ.offset(-T / 2)          # origin at y = +T/2, normal -Y: extrude T down to -T/2
    with BuildPart() as bp:
        with BuildSketch(pl):
            RectangleRounded(W, L, P["corner_r"])
            RectangleRounded(W - 2 * wl, L - 2 * wl, P["window_r"], mode=Mode.SUBTRACT)
        extrude(amount=T)
        # through each end wall (along Z) and each side wall (along X), at mid-thickness
        for p, n in (((0, 0, L / 2), (0, 0, -1)), ((0, 0, -L / 2), (0, 0, 1)),
                     ((W / 2, 0, 0), (-1, 0, 0)), ((-W / 2, 0, 0), (1, 0, 0))):
            with Locations(Plane(p, z_dir=tuple(-v for v in n))):
                Hole(d / 2, wl)
    holes = {
        "zp": ((0.0, 0.0, L / 2), (0.0, 0.0, -1.0)),
        "zn": ((0.0, 0.0, -L / 2), (0.0, 0.0, 1.0)),
        "xp": ((W / 2, 0.0, 0.0), (-1.0, 0.0, 0.0)),
        "xn": ((-W / 2, 0.0, 0.0), (1.0, 0.0, 0.0)),
    }
    return bp.part, holes, d


def resolve(params, kw, extra_defaults=None):
    P = dict(DEFAULTS)
    P.update(extra_defaults or {})
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS) - set(extra_defaults or {})
    if unknown:
        raise TypeError(f"unknown joint ring parameters: {sorted(unknown)}")
    return P
