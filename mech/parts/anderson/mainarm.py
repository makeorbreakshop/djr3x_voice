"""Hero-arm main arm (Anderson, `mainarm`) - the upright the hero arm swings on.

Stacked on one vertical axis (x = -26, y = -30): a 52 x 60 x 14 foot; a 39 mm square column to
z = 62 with a 3 mm bottle-shaped pocket in its +X face (the servo's cable run: a 12 mm neck from the
foot widening at ~45 deg, r3 rounds, to 19 mm, closed with r3 corners at z = 56.5); a 61 mm drum
(z = 62 .. 88) with two 1 x 1.5 mm grooves for the elbow covers; a 32 mm spigot to z = 120 and a
25 mm one to z = 180. A 6 mm bore runs up the axis to z = 70, 18 mm from there to the top.

Screw points, all as drawn: three short M4 insert pockets in each of the foot's +/-Y faces, four
short M3 pockets under it on a 10 mm square round the axis (the spacer block's pattern), and two
4.5 mm pockets with a 3 mm 45 deg lead-in in the drum's top face.

Frame = his STEP's: the foot's bed z = 0, x = -52 .. 0, y = -60 .. 0; the axis +Z.
"""

from __future__ import annotations

from parts.head._common import Print, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._holes import insert_pocket
from ._sketch import profile

REFERENCE = "r3x-internal - mainarm.step"
LABEL = "Hero-arm main arm (parametric)"

# every screw point is a blind pocket for a short insert with a screw-tip relief beyond; the preset
# swaps each for the standard insert of its size
DESIGNED = {"side": "heat_set", "base": "heat_set", "top": "heat_set"}
INSERT_CANDIDATES = ("side", "base", "top")

DEFAULTS = dict(
    fit=0.0,
    insert=None,                         # None: his pockets as drawn; the preset: standard inserts
    axis=(-26.0, -30.0),
    foot=(52.0, 60.0, 14.0),             # x (from 0 toward -x), y (from 0 toward -y), height
    column=(39.0, 62.0),                 # square side, top z
    drum=(30.5, 62.0, 88.0, 1.5, ((72.0, 73.0), (77.0, 78.0))),   # r, z from, z to, groove depth, grooves (z, z)
    spigots=((16.0, 120.0), (12.5, 180.0)),                      # (r, top z)
    bore=(6.0, 70.0, 18.0),              # lower d, step z, upper d
    pocket=(3.0, 12.0, 19.0, 27.5, 1.0201, 3.0, 56.5),   # depth, neck width, width, flare top z, flare dz/dy, round r,
                                                          # top z
    side=("M4", "M4-anderson", (-46.0, -26.0, -6.0), 7.0, 5.5, (5.5, 4.5), 4.25, 8.0),
    # bolt, insert, x positions, z, pocket d, pocket depths (+Y face, -Y face), relief d, relief depth
    base=("M3", "M3-anderson-5", 10.0, 5.0, 3.5, 3.0, 8.0),   # bolt, insert, square, pocket d, depth, relief d, total
    top=("M3", "M3-anderson", 23.25, 4.5, 7.0, 3.0),          # bolt, insert, offset along Y, d, depth, lead-in
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Align, Box, Cylinder, Plane, Pos, extrude, fillet

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown mainarm parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    fit = P["fit"]
    ax, ay = P["axis"]
    fx, fy, fh = P["foot"]
    cs, ct = P["column"]
    base = (Align.CENTER, Align.CENTER, Align.MIN)
    body = Pos(-fx / 2, -fy / 2, 0) * Box(fx, fy, fh, align=base)
    body += Pos(ax, ay, fh) * Box(cs, cs, ct - fh, align=base)
    dr, dz0, dz1, gd, grooves = P["drum"]
    body += Pos(ax, ay, dz0) * Cylinder(dr, dz1 - dz0, align=base)
    for g0, g1 in grooves:
        body -= Pos(ax, ay, g0) * (Cylinder(dr + 1, g1 - g0, align=base) - Cylinder(dr - gd, g1 - g0, align=base))
    z = dz1
    for r, zt in P["spigots"]:
        body += Pos(ax, ay, z) * Cylinder(r, zt - z, align=base)
        z = zt
    d0, zs, d1 = P["bore"]
    body -= Pos(ax, ay, -0.01) * Cylinder((d0 + fit) / 2, zs + 0.02, align=base)
    body -= Pos(ax, ay, zs) * Cylinder((d1 + fit) / 2, z - zs + 0.01, align=base)

    # the bottle pocket in the column's +X face: sketched in (y, z), rounds r3 at its four turns
    pdp, nw, ww, zw, k, rr, ptop = P["pocket"]
    zn = zw - (ww - nw) / 2 * k                       # where the neck starts to flare
    xf = ax + cs / 2
    pts = [(ay - nw / 2, fh - 1.0), (ay + nw / 2, fh - 1.0), (ay + nw / 2, zn), (ay + ww / 2, zw), (ay + ww / 2, ptop),
           (ay - ww / 2, ptop), (ay - ww / 2, zw), (ay - nw / 2, zn)]
    face = profile(pts)
    face = face.fillet_2d(rr, [v for v in face.vertices() if v.Y > fh])   # sketch (u, v) = (y, z)
    cut = extrude(Plane(origin=(xf - pdp, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)) * face, amount=pdp + 1.0)
    body -= cut & Pos(xf - pdp / 2, ay, fh) * Box(pdp + 0.01, ww + 2, ptop, align=(Align.CENTER, Align.CENTER, Align.MIN))

    feats: dict = {"bed": plane((0, 0, 0), (0, 0, -1)), "foot_top": plane((ax, ay, fh), (0, 0, 1)),
                   "drum_top": plane((ax, ay, dz1), (0, 0, 1)), "top": plane((ax, ay, z), (0, 0, 1)),
                   "axis": {"type": "axis", "p": [ax, ay, z], "d": [0.0, 0.0, -1.0], "r": (d1 + fit) / 2},
                   "cable_pocket": plane((xf - pdp, ay, (fh + ptop) / 2), (1, 0, 0))}
    hole_features(feats, "bore", (ax, ay, 0), (0, 0, 1), (d0 + fit) / 2, depth=zs)
    hole_features(feats, "bore_top", (ax, ay, z), (0, 0, -1), (d1 + fit) / 2, depth=z - zs)
    for i, (g0, g1) in enumerate(grooves):
        feats[f"groove{i + 1}"] = plane((ax, ay, (g0 + g1) / 2), (0, 0, 1))

    ins = P["insert"]
    ins_for = {g: (ins or ("M4" if g == "side" else "M3")) if P["inserts"] else ins for g in DESIGNED}
    bolt, key, xs, sz, pd, (dp_pos, dp_neg), rd, rdep = P["side"]
    for i, x in enumerate(xs):
        for sgn, face_y, dp, tag in ((1, 0.0, dp_pos, "p"), (-1, -fy, dp_neg, "n")):
            body = insert_pocket(body, feats, f"side_{tag}{i + 1}", (x, face_y, sz), (0, -sgn, 0), pd=pd, pdep=dp, rd=rd,
                                 rdep=dp + rdep, bolt=bolt, insert_key=key, hole_type=types["side"],
                                 insert=ins_for["side"], fit=fit)
    bolt, key, sq, pd, dp, rd, tot = P["base"]
    k = 0
    for dx in (-sq / 2, sq / 2):
        for dy in (-sq / 2, sq / 2):
            k += 1
            body = insert_pocket(body, feats, f"base{k}", (ax + dx, ay + dy, 0), (0, 0, 1), pd=pd, pdep=dp, rd=rd,
                                 rdep=tot, bolt=bolt, insert_key=key, hole_type=types["base"], insert=ins_for["base"],
                                 fit=fit)
    bolt, key, off, pd, dp, lead = P["top"]
    for i, dy in enumerate((-off, off)):
        body = insert_pocket(body, feats, f"top{i + 1}", (ax, ay + dy, dz1), (0, 0, -1), pd=pd, pdep=dp + lead, rd=pd,
                             rdep=dp + lead, bolt=bolt, insert_key=key, hole_type=types["top"], insert=ins_for["top"],
                             fit=fit, lead_in=lead)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("foot down (z = 0 on the bed)", "bed", True,
                                     "the drum overhangs the column by 11 mm all round: support under it"))
