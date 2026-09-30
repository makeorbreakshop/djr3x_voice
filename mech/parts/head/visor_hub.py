"""Visor hub (ours, new): joins a stub axle to the kit visor's side arm (H_V_2 left, H_V_3 right).

It replaces Anderson's flipper adapter here. His adapter is 13 mm long and fits the 8.3 mm round
end of his one-piece axle; on Hunter's head the bearing bracket stands where that length would
go, so this hub is shorter (9 mm) and takes the stub axle's 12 mm D profile directly. It bolts to
the arm's own four holes (Ø3.9 through the arm, on the kit's pattern, ~11.4 mm from the axis):
M3 heat-set inserts in the hub, M3 screws from the arm's outboard face, whose heads sit in the
axle end's (H_V_4/5) Ø5.8 pockets once that end is glued on. An M3 set screw on the D flat holds
the axle axially.

Design frame = the head frame; the hub runs along X from |x0| to |x1| on the visor axis.
`holes` are the arm holes' (y, z) centres, measured from the kit mesh by the assembly.
"""

from __future__ import annotations

from ._common import HOLES, Print, axis, finish, hole_features, plane

REFERENCE = None
LABEL = "Visor hub (parametric)"

DEFAULTS = dict(side=1, axis_y=32.3, axis_z=0.9, x=(73.3, 82.3), od=35.0, r=5.85, flat=4.1,
                flat_dir=(-1.0, 0.0), fit=0.2, holes=(), bolt="M3", set_screw=True,
                pocket=7.5)  # insert pocket depth: the insert (5.7) plus room for the screw's tip


def make(params: dict | None = None, **kw):
    import math

    from build123d import Box, Cylinder, Pos, Rot

    P = dict(DEFAULTS)
    P.update(params or {})
    P.update(kw)
    s = 1 if P["side"] >= 0 else -1
    x0, x1 = P["x"]
    L = x1 - x0
    xc = s * (x0 + x1) / 2
    ay, az = P["axis_y"], P["axis_z"]
    part = Pos(xc, ay, az) * Rot(0, 90, 0) * Cylinder(P["od"] / 2, L)
    # the D bore: a round bore with the flat left standing
    rb = P["r"] + P["fit"] / 2
    bore = Pos(xc, ay, az) * Rot(0, 90, 0) * Cylinder(rb, L + 1)
    fy, fz = P["flat_dir"]
    ang = math.degrees(math.atan2(fz, fy))  # the flat's outward normal, from +Y toward +Z
    keep = Pos(xc, ay, az) * Rot(ang, 0, 0) * Pos(0, P["flat"] + P["fit"] / 2 + rb, 0) * Box(L + 2, 2 * rb, 2 * rb + 2)
    part -= bore - keep
    feats: dict = {}
    d_ins, dep = HOLES[P["bolt"]]["heatset"], P["pocket"]
    for i, (y, z) in enumerate(P["holes"]):
        part -= Pos(s * (x1 - dep / 2), y, z) * Rot(0, 90, 0) * Cylinder(d_ins / 2, dep + 0.01)
        hole_features(feats, f"ins{i + 1}", (s * x1, y, z), (-s, 0, 0), d_ins / 2, depth=dep,
                      bolt=P["bolt"], kind="heat_set")
    if P["set_screw"]:
        # radial, tapped M3 from the rim, midway between two insert pockets (it only holds the axle
        # axially: the D bore carries the torque), so each pocket keeps its wall
        tap = HOLES[P["bolt"]]["tap"]
        hole_angs = [math.degrees(math.atan2(z - az, y - ay)) for y, z in P["holes"]]
        ss = max(range(0, 360, 5), key=lambda a: min([abs((a - h + 180) % 360 - 180) for h in hole_angs] or [180]))
        part -= Pos(xc, ay, az) * Rot(ss, 0, 0) * Pos(0, (P["od"] / 2 + rb) / 2, 0) * Rot(90, 0, 0) * Cylinder(
            tap / 2, P["od"] / 2 - rb + 1)
    feats["bore"] = axis((s * x0, ay, az), (s, 0, 0), P["r"])
    feats["face_out"] = plane((s * x1, ay, az), (s, 0, 0))
    feats["face_in"] = plane((s * x0, ay, az), (-s, 0, 0))
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("inboard face on the bed", "face_in", False,
                                     "insert pockets face up; print 100% infill round the D bore"))
