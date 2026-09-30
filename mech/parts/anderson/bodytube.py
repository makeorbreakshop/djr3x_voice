"""Hero-arm body tube (Anderson, `bodytube`) - the forearm: a 32 mm rod, 90 long, flat underneath.

Solid, with a 1 mm flat along its underside (z = -15, the print bed). The wrist end (y = 0) takes
four M3 in short heat-set pockets on a 10 mm square; two more pockets come in from the top, 20 and 37
mm back, for the servo arm.

Frame = his STEP's: the tube along -Y from the wrist face y = 0, the flat at z = -15, x across.
"""

from __future__ import annotations

import math

from parts.head._common import Print, finish, hole_group_params, plane, resolve_hole_types

from ._holes import insert_pocket

REFERENCE = "r3x-internal - bodytube.step"
LABEL = "Hero-arm body tube (parametric)"

# 4.5 x 3.5 pockets with a screw-tip relief beyond: his short M3 inserts. The preset swaps in standard M3.
DESIGNED = {"end": "heat_set", "top": "heat_set"}
INSERT_CANDIDATES = ("end", "top")

DEFAULTS = dict(
    fit=0.0,
    bolt="M3", insert=None,              # None: his 4.5 x 3.5 pocket (M3-anderson); the preset: a standard M3
    radius=16.0, length=90.0, flat_z=-15.0,
    end=(10.0, 4.5, 3.5, 3.0, 11.5),     # square pitch, pocket d, pocket depth, relief d, total depth
    top=((-20.0, -37.0), 4.5, 12.0, 3.25, 2.0),   # y positions, pocket d, pocket floor z, relief d, relief floor z
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Box, Cylinder, Pos, Rot

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown bodytube parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    insert = P["insert"] or ("M3" if P["inserts"] else None)
    fit, R, L, zf = P["fit"], P["radius"], P["length"], P["flat_z"]
    body = Pos(0, -L / 2, 0) * Rot(90, 0, 0) * Cylinder(R, L)
    body -= Pos(0, -L / 2, zf - R) * Box(2 * R + 2, L + 2, 2 * R)      # the flat
    feats: dict = {"wrist_face": plane((0, 0, 0), (0, 1, 0)), "back_face": plane((0, -L, 0), (0, -1, 0)),
                   "flat": plane((0, -L / 2, zf), (0, 0, -1)),
                   "axis": {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, -1.0, 0.0], "r": R}}

    def pocket(name, entry, d_into, pd, pdep, rd, rdep, group):
        nonlocal body
        body = insert_pocket(body, feats, name, entry, d_into, pd=pd, pdep=pdep, rd=rd, rdep=rdep, bolt=P["bolt"],
                             insert_key="M3-anderson", hole_type=types[group], insert=insert, fit=fit)

    s, pd, pdep, rd, rdep = P["end"]
    k = 0
    for x in (-s / 2, s / 2):
        for z in (-s / 2, s / 2):
            k += 1
            pocket(f"end{k}", (x, 0.0, z), (0, -1, 0), pd, pdep, rd, rdep, "end")
    ys, pd, pz, rd, rz = P["top"]
    for k, y in enumerate(ys):
        pocket(f"top{k + 1}", (0, y, R), (0, 0, -1), pd, R - pz, rd, R - rz, "top")
        feats[f"hole_top{k + 1}"]["rim_z"] = math.sqrt(R * R - (pd / 2) ** 2)   # its rim on the curved top
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("on its flat (z = -15 on the bed)", "flat", False,
                                     "the end pockets print horizontally; no supports"))
