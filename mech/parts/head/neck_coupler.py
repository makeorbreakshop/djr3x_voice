"""RX Neck Coupler V1 - the cup that sits on the neck tube and carries the bottom sonic hub.

Reference: `RX Neck Coupler V1 v1.step` (Hunter's B-rep; `RXNeckCouplerV1.stl` is the same solid
in the same frame, and is what the workbench regresses against). Frame = the
reference's: axis +Y, the open (tube) end at y = 0, the hub face at y = height.

Design: a round boss (od x height), bored from below for the neck tube to a stop `top_t` under
the hub face; a centre relief hole for the hub's boss; four M4 tap holes on the goBILDA sonic
hub's 16 mm square pattern (the hub's screws self-thread into the plastic); one M4 tap hole
through the +X wall, `pin_y` up, to pin the cup to the tube.

Variants:
    make()                      Hunter's: 32 mm neck tube
    make(tube_od=26.0)          the R-3X kit's 26 mm neck tube (wall 7 mm instead of 4)
    make(fit=0.2)               every hole and the bore opened 0.2 mm
"""

from __future__ import annotations

from ._common import (INSERTS, Print, axis, finish, hole_d, hole_features, hole_group_params, plane,
                      resolve_hole_types, rot_x, transform)

REFERENCE = "RXNeckCouplerV1.stl"          # the mesh the workbench regresses against (same solid, same frame)
REFERENCE_CAD = "RX Neck Coupler V1 v1.step"  # Hunter's B-rep: what the defaults are read from
LABEL = "RX Neck Coupler V1 (parametric)"

VARIANTS = {"hunter_32": {}, "r3x_26": {"tube_od": 26.0}}

DESIGNED = {"hub": "tapped", "pin": "tapped"}   # the hub's screws and the side pin self-thread (Hunter)
INSERT_CANDIDATES = ()   # a 5 mm hub plate and a 4 mm wall are too thin for inserts: kept tapped

DEFAULTS = dict(
    fit=0.0,              # print fit, mm added to every hole and to the bore diameter
    tube_od=32.0,         # neck tube OD the bore takes (Hunter: 32; R-3X kit: 26)
    od=40.0,              # outside diameter
    height=35.0,
    top_t=5.0,            # hub plate thickness (the tube stops under it)
    centre_hole_d=15.0,   # relief for the sonic hub's centre boss
    hub_pattern=16.0,     # goBILDA sonic hub: 4 holes on a 16 mm square
    hub_bolt="M4",
    pin_bolt="M4",
    pin_y=15.0,           # side pin hole height above the open end
    insert="M4-kit",      # the head's inserts: the BOM's 6 x 6 mm M4
    **hole_group_params(DESIGNED),   # hub_hole / pin_hole: clearance | heat_set | tapped (as designed: tapped)
)


def make(params: dict | None = None, **kw):
    from build123d import Axis, BuildPart, BuildSketch, Circle, Hole, Locations, Mode, Plane, extrude

    P = dict(DEFAULTS)
    given = dict(params or {}, **kw)
    if "bore_d" in given:                          # the workbench's name for it (PARAMS["neck_tube_bore"])
        given["tube_od"] = given.pop("bore_d")
    P.update(given)
    unknown = set(P) - set(DEFAULTS)
    if unknown:
        raise TypeError(f"unknown neck_coupler parameters: {sorted(unknown)}")
    fit = P["fit"]
    od, h, tt = P["od"], P["height"], P["top_t"]
    bore = P["tube_od"] + fit
    if bore >= od - 2.0:
        raise ValueError(f"tube_od {P['tube_od']} leaves less than a 1 mm wall in a {od} mm coupler")
    types = resolve_hole_types(P, DESIGNED, INSERT_CANDIDATES)
    if "nut_trap" in types.values():
        raise ValueError("no room for nut traps in the coupler: clearance | heat_set | tapped")

    def dia(bolt, t):
        return INSERTS[P["insert"]]["d"] + fit if t == "heat_set" else hole_d(bolt, "tap" if t == "tapped" else t, fit)

    d_hub = dia(P["hub_bolt"], types["hub"])
    d_pin = dia(P["pin_bolt"], types["pin"])
    hp = P["hub_pattern"] / 2

    # design frame: axis +Z, open end at z = 0 (turned to the reference's +Y at the end)
    with BuildPart() as bp:
        with BuildSketch():
            Circle(od / 2)
        extrude(amount=h)
        # the tube bore, from the open end up to the hub plate
        with BuildSketch():
            Circle(bore / 2)
        extrude(amount=h - tt, mode=Mode.SUBTRACT)
        top = bp.faces().sort_by(Axis.Z)[-1]
        with Locations(Plane(top.center(), z_dir=(0, 0, 1))):
            Hole((P["centre_hole_d"] + fit) / 2, tt)
            with Locations(*[(x, y) for x in (-hp, hp) for y in (-hp, hp)]):
                Hole(d_hub / 2, tt)
        # side pin hole through the +X wall only
        wall = (od - bore) / 2
        with Locations(Plane((od / 2, 0, P["pin_y"]), x_dir=(0, 1, 0), z_dir=(1, 0, 0))):
            Hole(d_pin / 2, wall + 1.0)                 # through the wall, into the bore
    part = transform(bp.part, rot_x(-90))          # design +Z -> the reference's +Y: (x, y, z) -> (x, z, -y)

    # features, in the reference frame (x, y, z) = design (x, z, -y)
    feats: dict = {
        "top": plane((0, h, 0), (0, 1, 0)),
        "bottom": plane((0, 0, 0), (0, -1, 0)),
        "bore": axis((0, 0, 0), (0, 1, 0), bore / 2, depth=h - tt),
        "bore_stop": plane((0, h - tt, 0), (0, -1, 0)),
    }
    hole_features(feats, "centre", (0, h, 0), (0, -1, 0), (P["centre_hole_d"] + fit) / 2, depth=tt)
    hub = sorted([(x, z) for x in (-hp, hp) for z in (-hp, hp)])
    for i, (x, z) in enumerate(hub):
        hole_features(feats, f"cp{i + 1}", (x, h, z), (0, -1, 0), d_hub / 2, depth=tt, bolt=P["hub_bolt"],
                      kind=types["hub"])
    hole_features(feats, "side_f", (od / 2, P["pin_y"], 0), (-1, 0, 0), d_pin / 2, depth=wall,
                  bolt=P["pin_bolt"], kind=types["pin"])
    return finish(part, label=LABEL, params=P, features=feats, reference=REFERENCE,
                  printability=Print("open end down (y = 0 on the bed)", "bottom", False,
                                     "the 5 mm hub plate bridges the 32 mm bore: a clean bridge at 0.2 mm layers; "
                                     "no supports. The side pin hole prints as a horizontal 3.3 mm hole (fine)."))
