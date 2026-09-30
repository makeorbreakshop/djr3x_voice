"""Head-lift rack (Anderson, `head-lift-straight-gear`) - the straight rack the lift gear climbs.

A 6 mm backing bar with ten square-rooted trapezoid teeth on a 9 mm pitch, and three blind 5.5 mm
holes in its back to fix it.

Frame = the STEP's: back face x = 0, teeth toward +X, the rack along Z (centred), y = +/- width / 2.
"""

from __future__ import annotations

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._gear import trapezoid

REFERENCE = "r3x-internal - head-lift-straight-gear.step"
LABEL = "Head-lift rack (parametric)"

DESIGNED = {"mount": "heat_set"}      # 5.5 x 4.5 blind pockets: short M4 inserts for the wall's M4 screws
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    bolt="M4",
    length=87.0, width=20.0, back=6.0,
    pitch=9.0, teeth=10, first_tooth_z=-41.0,
    tooth=(5.0, 2.0, 2.0, 6.0),     # root width, tip land, straight-wall height, total height (above the bar)
    mount=(5.5, 4.5, (-28.5, 0.0, 28.5)),   # blind holes from the back: diameter, depth, z positions
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import BuildPart, BuildSketch, Box, Cylinder, Plane, Polygon, Pos, Rot, extrude, Mode

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown lift_rack parameters: {sorted(unknown)}")
    L, W, B = P["length"], P["width"], P["back"]
    rw, tw, wall, th = P["tooth"]
    tooth = trapezoid(rw, tw, B + wall, B + th, B - 1.0)          # (across = z, up = x)
    body = Pos(B / 2, 0, 0) * Box(B, W, L)
    # teeth: sketched on the XZ plane (local u = x, v = z), extruded across Y
    with BuildPart() as tp:
        with BuildSketch(Plane.XZ.offset(-W / 2)):
            for k in range(P["teeth"]):
                zc = P["first_tooth_z"] + k * P["pitch"]
                Polygon(*[(y_up, z + zc) for z, y_up in tooth], align=None)
        extrude(amount=W)
    body += tp.part & Pos((B + th) / 2, 0, 0) * Box(B + th + 2, W, L)   # clipped to the bar's length
    feats: dict = {"back": plane((0, 0, 0), (-1, 0, 0)), "pitch_line": plane((B + th / 2, 0, 0), (1, 0, 0))}
    md, mdep, zs = P["mount"]
    mt = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)["mount"]
    for i, z in enumerate(zs):
        if mt != DESIGNED["mount"]:
            body = cut_hole(body, feats, f"mount{i + 1}", (0, 0, z), (1, 0, 0), P["bolt"], mt, mdep, P["fit"])
            continue
        body -= Pos(mdep / 2, 0, z) * Rot(0, 90, 0) * Cylinder((md + P["fit"]) / 2, mdep + 0.02)
        hole_features(feats, f"mount{i + 1}", (0, 0, z), (1, 0, 0), (md + P["fit"]) / 2, depth=mdep, bolt=P["bolt"],
                      kind="heat_set")
        feats[f"hole_mount{i + 1}"]["insert"] = f"as drawn: {md} x {mdep} pocket (a short M4 insert)"
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("on a side face (y = -width / 2 on the bed)", "back", False,
                                     "on its side the tooth profile prints in-plane; no supports"))
