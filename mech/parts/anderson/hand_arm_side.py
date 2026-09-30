"""Hero-arm hand, arm side (Anderson, `hand-arm-side`) - the disc the wrist servo turns.

A 60 mm disc 7.8 thick (0.75 chamfer on its lower edge) with a 1.5 mm collar ring (r19..20.5) 5 mm
proud underneath. Inside the ring the servo's six-arm horn lets into a 3 mm star pocket, its six
horn screws self-threading 3 mm further; a 6 mm hole takes the horn's centre screw. Four 2 mm
holes on r25.25 join it to the finger side (`hand_finger_side`), which sits on its top face.

Frame = his STEP's: axis +Z, the disc z = 0 .. 7.8, the collar below.
"""

from __future__ import annotations

import math

from parts.head._common import Print, cut_hole, finish, hole_features, hole_group_params, plane, resolve_hole_types

from ._horn import horn_star_tapered

REFERENCE = "r3x-internal - hand-arm-side.step"
LABEL = "Hero-arm hand, arm side (parametric)"
DESIGNED = {"horn": "tapped", "join": "clearance"}   # the horn's M2 self-tap in; M2 pass through to the finger side
INSERT_CANDIDATES = ()                               # 3 mm of plastic under the horn: too thin for an insert

DEFAULTS = dict(
    fit=0.0,
    horn_bolt="M2", join_bolt="M2",
    radius=30.0, thickness=7.8, chamfer=0.75,
    collar=(19.0, 20.5, 5.0),            # inner r, outer r, height below z = 0
    horn=(6, 13.5, 3.0, 4.75, 3.0),      # arms, tip-centre radius, tip radius, flank half-width at the centre, depth
    horn_screws=(1.5, 3.0),              # diameter, depth beyond the pocket floor
    centre_d=6.0,
    join=(4, 25.25, 2.0, 0.0),           # count, radius, diameter, first angle (deg)
    **hole_group_params(DESIGNED),
)


def make(params: dict | None = None, **kw):
    from build123d import Align, Cylinder, Cone, Pos, extrude

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown hand_arm_side parameters: {sorted(unknown)}")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    fit, Rd, T, c = P["fit"], P["radius"], P["thickness"], P["chamfer"]
    body = (Pos(0, 0, c) * Cylinder(Rd, T - c, align=(Align.CENTER, Align.CENTER, Align.MIN))
            + Cone(Rd - c, Rd, c, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    ri, ro, ch = P["collar"]
    body += Pos(0, 0, -ch) * (Cylinder(ro, ch, align=(Align.CENTER, Align.CENTER, Align.MIN))
                              - Cylinder(ri, ch, align=(Align.CENTER, Align.CENTER, Align.MIN)))
    arms, R, r, hub, depth = P["horn"]
    body -= extrude(horn_star_tapered(arms, R, r + fit / 2, hub + fit / 2), amount=depth)
    feats: dict = {"top": plane((0, 0, T), (0, 0, 1)), "horn_seat": plane((0, 0, depth), (0, 0, -1)),
                   "collar": plane((0, 0, -ch), (0, 0, -1)),
                   "axis": {"type": "axis", "p": [0.0, 0.0, 0.0], "d": [0.0, 0.0, 1.0], "r": ri}}
    hole_features(feats, "collar_bore", (0, 0, -ch), (0, 0, 1), ri, depth=ch)   # the wrist's spigot
    cd = P["centre_d"] + fit
    body -= Pos(0, 0, depth - 0.01) * Cylinder(cd / 2, T - depth + 0.02, align=(Align.CENTER, Align.CENTER, Align.MIN))
    hole_features(feats, "centre", (0, 0, depth), (0, 0, 1), cd / 2, depth=T - depth)
    sd, sdep = P["horn_screws"]
    for k in range(arms):
        a = 2 * math.pi * k / arms
        x, y = R * math.cos(a), R * math.sin(a)
        if types["horn"] == DESIGNED["horn"]:
            body -= Pos(x, y, depth - 0.01) * Cylinder((sd + fit) / 2, sdep + 0.01,
                                                       align=(Align.CENTER, Align.CENTER, Align.MIN))
            hole_features(feats, f"horn_screw{k + 1}", (x, y, depth), (0, 0, 1), (sd + fit) / 2, depth=sdep,
                          bolt=P["horn_bolt"], kind="tapped")
        else:
            body = cut_hole(body, feats, f"horn_screw{k + 1}", (x, y, depth), (0, 0, 1), P["horn_bolt"],
                            types["horn"], sdep, fit)
    n, jr, jd, a0 = P["join"]
    for k in range(n):
        a = math.radians(a0) + 2 * math.pi * k / n
        x, y = jr * math.cos(a), jr * math.sin(a)
        if types["join"] == DESIGNED["join"]:
            body -= Pos(x, y, T / 2) * Cylinder((jd + fit) / 2, T + 0.02)
            hole_features(feats, f"join{k + 1}", (x, y, 0), (0, 0, 1), (jd + fit) / 2, depth=T, bolt=P["join_bolt"],
                          kind="clearance")
        else:
            body = cut_hole(body, feats, f"join{k + 1}", (x, y, 0), (0, 0, 1), P["join_bolt"], types["join"], T, fit)
    return finish(body, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("top face down (z = 7.8 on the bed)", "top", False,
                                     "the collar and the horn pocket face up; no supports"))
