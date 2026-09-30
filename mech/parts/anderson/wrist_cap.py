"""Hero-arm wrist cap (Anderson, `Part 1` in his STEPs, `Part 1 (1)` in the hero-arm STLs) - the ring
that closes the wrist's rim over the hand's collar.

A 60 mm flange 1.5 thick on a 53.5 mm spigot (to z = 6) that drops into the wrist's 55 mm rim bore;
its 42 mm counterbore (4.5 deep) clears the hand's 41 mm collar, and the 38 mm lip above keeps it in.
No holes: it is held by the parts either side.

Frame = his STEP's: axis +Z, the flange z = 0 .. 1.5.
"""

from __future__ import annotations

from parts.head._common import Print, finish, hole_features, hole_group_params, plane, resolve_hole_types

REFERENCE = "r3x-internal - Part 1.step"
LABEL = "Hero-arm wrist cap (parametric)"
DESIGNED: dict = {}
INSERT_CANDIDATES = ()

DEFAULTS = dict(
    fit=0.0,
    flange=(30.0, 1.5), spigot=(26.75, 6.0),   # (r, top z) each
    counterbore=(21.0, 4.5), lip_r=19.0,
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Align, Cylinder, Pos

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown wrist_cap parameters: {sorted(unknown)}")
    resolve_hole_types(P, DESIGNED)
    fit = P["fit"]
    base = (Align.CENTER, Align.CENTER, Align.MIN)
    (fr, ft), (sr, st) = P["flange"], P["spigot"]
    cr, cd = P["counterbore"]
    body = Cylinder(fr, ft, align=base) + Cylinder(sr - fit / 2, st, align=base)
    body -= Cylinder(cr + fit / 2, cd, align=base).moved(Pos(0, 0, -0.01))
    body -= Cylinder(P["lip_r"] + fit / 2, st + 0.02, align=base).moved(Pos(0, 0, -0.01))
    feats: dict = {"flange_face": plane((0, 0, 0), (0, 0, -1)), "seat": plane((0, 0, ft), (0, 0, 1)),
                   "top": plane((0, 0, st), (0, 0, 1)),
                   "spigot": {"type": "axis", "p": [0.0, 0.0, st], "d": [0.0, 0.0, -1.0], "r": sr - fit / 2}}
    hole_features(feats, "counterbore", (0, 0, 0), (0, 0, 1), cr + fit / 2, depth=cd)
    hole_features(feats, "lip", (0, 0, st), (0, 0, -1), P["lip_r"] + fit / 2, depth=st - cd)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("flange down (z = 0 on the bed)", "flange_face", False,
                                     "the 1 mm lip bridges the counterbore as a ring: no supports"))
