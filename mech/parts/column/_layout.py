"""The central column's layout: every number the column parts and assemblies/column share.

Body frame (show/SPEC.md "Frame and zeros"): mm, +Y up, +Z front, +X the droid's left, rest pose
(head_lift 0, head_pan 0). Every column part is modelled in this frame (its make() places it), so
the assembly's transform for a part is the identity and a parameter change moves the part.

What each group is, and where its numbers come from (`inferred` = not stated by any source; the
evidence follows):

* Gil plate: David Ferreira's Gil drive base plate, measured from Morton's
  "Gil-Drive-with-Base-Plate_Electronics stack v8.stl" (2026-10-01): a 169.7 x 169.7 x 8.0 mm plate,
  corners R 17.25, eight 6.0 mm holes at (+-80, +-64) and (+-64, +-80); the file also carries the
  electronics stack's four 50 x 7 x 44.7 uprights (at +-62.85..69.85 on the plate's edges), which a
  column at the centre replaces (the electronics move out onto the skid plate round it). Its sibling
  "SkidPlate Outer.stl" (5 mm, ~237 x 233) has a 171.7 mm square window - the Gil plate's outline +
  1 mm a side - with eight 3.5 mm holes at (+-112, +-64) / (+-64, +-112). inferred: only the height.
  The Gil file is in its own frame (y -275.7); the skid plate is in the kit frame at y -53..-48 and
  Morton's 2020 posts stand at y -48, so the Gil plate's top is put at -48.0, in the skid's window.
* Column: four V-slot 2020 posts on a 100 x 100 square (the sled's V-wheels run in their inner slots). The square is set by the kit: the pedestal's
  back grille pocket comes in to r 59.7 at y 165-265 (P_M_1/P_M_2/P_G_1, measured) and the rest of
  the pedestal wall is at r 86-88, so a 100 mm square (faces at 50, corners at r 70.7) clears both
  with >= 9.7 mm. Hunter's tower (photos 2025-08-06/11) is the same idea; his square is inferred
  larger, ours is set by the kit's pedestal.
* Ring heights: Anderson's sectors on the rings stay where his files put them (lower sector y
  345-390, top sector y 462-476, both at the back, measured from his parametric remodels); the
  column's ring drives put the same pinions at the same places (centre (-9.8, *, -83)).
* Sled (lift + pan): Jason Charlton's build (his replies 2026-10-01, mech/vendor/refs/column_internals):
  an inner assembly on V-wheels, the lift servo behind a grid plate on it on a rack at the rear, the
  pan servo at its base turning the neck through a coupler. Plate thicknesses, the wheels' depth in
  the slot and the stack heights are ours (inferred).
* Head: Hunter's gimbal centre at body y 738.3 (assemblies/hunter_head), its coupler's bore stop
  (the tube's top) at 696.3: the short neck ends there, so the head sits where it does today.
"""

from __future__ import annotations

import math

# ------------------------------------------------------------------ base
GIL = dict(top_y=-48.0, t=8.0, half=169.7 / 2, corner_r=17.25,     # measured (Morton's Gil STL); top_y inferred
           holes=((80.0, 64.0), (64.0, 80.0)), hole_d=6.0,          # x8 by symmetry (measured)
           foot_bolt="M5")
SKID = dict(top_y=-48.0, window=171.7 / 2, holes_at=((112.0, 64.0), (64.0, 112.0)), hole_d=3.5, t=5.0)   # Morton's (not modelled)
FOOT = dict(t=6.35, half=75.0, bolt_at=62.0)              # 1/4 in 6061 foot plate, 150 x 150 (ours)
Y_GIL_TOP = GIL["top_y"]
Y_FOOT_TOP = Y_GIL_TOP + FOOT["t"]                        # -41.65

# ------------------------------------------------------------------ column
POST_C = 40.0          # post centres at (+-40, +-40): 2020 on a 100 mm square
HALF = POST_C + 10.0   # the column's outline half-width (50)
Y_POST_TOP = 584.0     # under the top cap: TR_N_3 at y 570 is an annulus r >= 89 (corners at 70.7), TR_N_2 at 598.7
TOP = dict(t=6.0, tube_hole_r=16.0)                       # 6061 top plate, 100 x 100
POST_LEN = Y_POST_TOP - Y_FOOT_TOP

# V-slot 2020 (OpenBuilds; the V-wheels ride its slots): the slot in the post's own frame (face at 10 from
# its centre): the V's mouth and floor, then the T cavity (as the 5-series slot)
SLOT = dict(open_half=3.1, lip_in=8.2, cav_half=5.5, cav_floor=6.2, taper_half=3.2, taper_floor=4.8)

# ------------------------------------------------------------------ servos (goBILDA 2000-0025-0002, vendor STEP)
SPLINE_ABOVE_BOSS = 4.1      # spline top above the case boss (vendor STEP)
HUB_H = 5.5                  # 1906 hub: servo face to arm face (vendor STEP)
HUB_TAP_R = 11.43            # 1906 hub: the four tapped M4 holes, on its own axes (measured, vendor STEP)
FLANGE_Y = (-16.9, -13.39)   # servo flange, below the spline top (measured, vendor STEP)
FLANGE_HOLES = [(10.0 + sx * 24.0, sz * 4.89) for sx in (-1, 1) for sz in (-1, 1)]   # (long axis, across)
CASE_BOTTOM = -44.1
CASE_TOP = -4.3              # the case's top face below the spline top (parts/models.py "standard")

# ------------------------------------------------------------------ the sled (after Jason Charlton's build)
# Charlton (2026-10-01, mech/vendor/refs/column_internals): an inner assembly round the neck "glides up
# and down the outer rails with V-wheels"; the lift servo sits behind a grid plate on it, its brass
# servo gear on a long vertical rack on the rear of the structure; the rotation servo is attached to the
# base of that inner assembly and turns the neck through a round coupler (his photo: the servo at the
# sled's bottom, spline up, a hub/coupler up into the neck). So: a box of four plates between the posts,
# eight V-wheels in the posts' inner slots (V-slot 2020), the pan servo hung under its bottom plate on the
# axis, the neck in two bearings in its top plate.
#
# V-wheels: axle along Z (the wheel's plane is X-Y, square to the post's inner X face), on the slot
# centre (z = +-40, the post's), the V tip `tip_in` into the slot: wheel centre at |x| = 30 + tip_in - r.
VWHEEL = dict(od=24.39, w=10.23, bore=5.0, hub_r=7.0, tip_in=1.5, tip_hw=3.1)   # OpenBuilds solid V-wheel (od, w)
WHEEL_X = POST_C - 10.0 + VWHEEL["tip_in"] - VWHEEL["od"] / 2       # 19.305
ECC = dict(l=6.0, od=10.0)                                        # eccentric spacer, 6 mm (OpenBuilds)
SLED = dict(t=3.0, half_x=33.5,                                   # goBILDA-pattern grid plates (inferred t)
            z_in=POST_C - VWHEEL["w"] / 2 - ECC["l"] - 3.0,       # front/back plates' inner face (25.885)
            side_x=30.5,                                          # the right side plate's inner face (the rack: x -35)
            side_x_l=31.5,                                        # the left one's: 1.5 mm past the pan servo's case
            grid=8.0, grid_r=2.0)                                 # 4 mm holes on an 8 mm grid (goBILDA pattern)
SLED["z_out"] = SLED["z_in"] + SLED["t"]                          # 28.885: the wheels' spacers start here

# ------------------------------------------------------------------ pan: direct drive at the sled's base
# The pan servo hangs spline-up on the axis under the sled's bottom plate (its flange under the plate, the
# case's top through a cut-out); a goBILDA 1906 hub on its spline and a turned coupler on the hub clamp the
# neck tube's bottom. The neck runs up through two 6806 in the top plate's housing (the head's weight on the
# upper inner race through the hub's flange). Direct, 1:1: the servo's own travel is the pan's.
PAN_SPLINE_Y = 385.0                                             # spline top (sets the stack below)
PAN_SERVO_SPLINE = (0.0, PAN_SPLINE_Y, 0.0)
BOTTOM = dict(t=4.0, y0=PAN_SPLINE_Y + FLANGE_Y[1])               # 371.61: the flange's top against its underside
BOTTOM["y1"] = BOTTOM["y0"] + BOTTOM["t"]                         # 375.61: the box's plates stand on it
TAB = dict(half_x=9.0, z1=48.0)                                   # the bottom plate's tab for the service cable
COUPLER = dict(r=19.0, flange_y0=PAN_SPLINE_Y - SPLINE_ABOVE_BOSS + HUB_H, flange_t=8.0, socket=25.0, bore_r=13.05,
               cb_r=3.6, cb_depth=4.5, slit=1.0)
COUPLER["stop"] = COUPLER["flange_y0"] + COUPLER["flange_t"]      # 394.4: the tube's bottom on the flange
COUPLER["top"] = COUPLER["stop"] + COUPLER["socket"]              # 419.4
PAN = (-135.0, 135.0)  # design range: the suite's +-135 servo rule (goBILDA 2000 standard mode is 300 deg:
                       # +-150 is the mechanical end); the clock spring allows +-200. Past ~+120 relative to
                       # the top ring the low head's right ear meets the raised hero arm: a coupled limit
PAN_MECH = 150.0       # the servo's own end (standard mode, 300 deg): nothing else stops the neck before it
BRG = dict(id=30.0, od=42.0, w=7.0)                       # 6806-2RS (30 x 42 x 7), two
G0 = 480.0             # the hub flange's top (the bearings' stack hangs off it)
HUB = dict(r=15.0, flange_r=21.5, flange_t=4.0, bore_r=13.05, y0=423.0, top=500.0, screw_r=19.0)
Y_FLANGE = (G0 - HUB["flange_t"], G0)                     # 476..480
Y_BRG_UP = (Y_FLANGE[0] - BRG["w"], Y_FLANGE[0])          # 469..476
Y_BRG_LO = (436.0, 436.0 + BRG["w"])                      # 436..443
HOUSING = dict(r=25.0, y0=437.5, y1=474.5, seat_r=21.05, mid_r=18.0)   # bearings stand 1.5 proud of each end
COLLAR = dict(r=22.5, y0=423.0, y1=436.0)                 # 30 mm one-piece clamp collar, 45 OD x 13
CROSS_Y = 494.0        # the M4 cross bolt through hub and tube (above the flange)
TUBE = dict(od=26.0, wall=1.5, y0=COUPLER["stop"], top=696.3)   # 26 x 1.5 6061: Hunter's coupler bore; top = its bore stop
TOP_PLATE_SLED = dict(t=8.0, y1=HOUSING["y1"])            # the sled's top plate, flush with the housing's top
TOP_PLATE_SLED["y0"] = TOP_PLATE_SLED["y1"] - TOP_PLATE_SLED["t"]
SLED["y0"], SLED["y1"] = BOTTOM["y1"], HOUSING["y1"]      # the four plates: 375.61..474.5
WHEEL_Y = (BOTTOM["y1"] + VWHEEL["od"] / 2 + 1.0, HOUSING["y1"] - VWHEEL["od"] / 2 - 0.3)   # 388.8, 462.0
STANDOFF = dict(r=33.0, angles=(45.0, 135.0, 225.0, 315.0), length=24.0)   # McMaster 92871A317 (6 OD, M4, 24)
EAR = dict(t=6.0, r=38.0, w=12.0)
CASE = dict(y0=HOUSING["y1"] + STANDOFF["length"], floor=3.0, h=14.0, r=28.0, wall=2.0, hole_r=18.0, top_hole_r=14.5,
            inner_r=15.0)
COIL = dict(x=0.0, z=40.0, r=5.0, wire_r=2.0)             # retractile service cable (the column's front opening)

# ------------------------------------------------------------------ lift (a rack climber, after Jason Charlton)
# The lift servo rides the sled, behind a plate on its right (-X) side; its brass Mod 0.8 servo gear climbs
# a fixed vertical rack on the back-right post's inner face (x -40, the droid's right) (Charlton's build: "the
# lifting servo is attached behind the grid plate; the brass servo gear meshes with a long vertical gear
# rack attached to the rear of the structure", 2026-10-01). 48 teeth: pitch radius 19.2, 0.335 mm per
# servo degree, so the servo's +-135 deg is +-45.2 mm.
RACK = dict(module=0.8, x=-40.0, face=10.0, z_base=-30.0, pitch_z=-22.0, y0=350.0, y1=482.0, teeth_y=(362.0, 470.0),
            screws_y=(356.0, 476.0), cb_floor=4.0)
LIFT_PINION = dict(teeth=48, thick=6.0)
R_LIFT = RACK["module"] * LIFT_PINION["teeth"] / 2                     # 19.2
LIFT_MM_PER_DEG = R_LIFT * math.pi / 180.0                             # 0.3351 mm per servo degree
LIFT_PINION_C = (RACK["x"], 412.0, RACK["pitch_z"] + R_LIFT)          # (x mid-plane, y, z) at rest
LIFT = (-37.0, 45.0)    # travel: down as today's (the mouth meets the top cap's collars below that), up +45 (free)
# the lift servo: spline along +X into the pinion (its boss on the pinion's -X face), long side down
LIFT_SPLINE = (RACK["x"] - LIFT_PINION["thick"] / 2 + 4.1, LIFT_PINION_C[1], LIFT_PINION_C[2])
# its hanger (on the sled): a plate behind the flange, arms to the sled's right side plate (top) and under
# its bottom plate (bottom), round the pinion
HANGER = dict(t=8.0, y_top=446.0, arm_top=(438.0, 446.0), arm_bot=(BOTTOM["y0"] - 8.0, BOTTOM["y0"]), arm_bot_x1=-25.0,
              half_z=15.0)

# ------------------------------------------------------------------ ring drives (Anderson's sectors, our servos)
RING_PINION_C = (-9.8, -83.0)                             # (x, z): Anderson's pinion centre, both rings
RING = {
    # pinion: Anderson's rounded teeth (parts/anderson/ring_servo_gear presets); y0 = its underside
    "lower": dict(y0=373.3, teeth=25, tip=(1.4629, 31.5408), fillet=(1.6342, 29.2397), phase=3.6,
                  post_y=(418.0, 428.0)),
    "top": dict(y0=462.0, teeth=20, tip=(1.4666, 27.0372), fillet=(1.8833, 24.9144), phase=0.0,
                post_y=(506.0, 516.0)),
}
RING_PINION_T = 10.0
for _k, _v in RING.items():
    _v["hub_top"] = _v["y0"] + RING_PINION_T + HUB_H           # servo boss (the hub's servo face), spline down
    _v["spline"] = _v["hub_top"] - SPLINE_ABOVE_BOSS           # spline top
    _v["flange"] = (_v["spline"] - FLANGE_Y[1], _v["spline"] - FLANGE_Y[0])
    _v["plate"] = (_v["flange"][1], _v["flange"][1] + 8.0)
DRIVE_PLATE = dict(t=8.0, r_max=96.0, back=5.8)

# ------------------------------------------------------------------ ring plates (the body on the column)
# The kit carries every ring down its printed stack: the lower lazy susan's inner race is clamped between
# LS_IC_1 and the pedestal cap P_M_3 (guide p20), the middle ring's race sits on LS_IC_1's pillars (p33-34),
# the top ring's on TR-MR_SC, a carrier hung in the middle ring (p52). The plates put those races on the
# column (Charlton's aluminium ring plates, Hunter's ring plate, Morton's frame rings): measured from the kit -
# the lower race's underside = P_M_3's top (y 342.1, r 97-110.3; LS_SR_1 from r 111.8), LS_IC_1's four screw
# holes (4.8) at r 104.4, 1.1 deg + 90 k; TR-MR_SC's flange underside y 476.8 (r 98.5-127.8), its race-screw
# holes (4.8) at r 104.4 and 348/78/168/258 deg; MS_Main's inner wall r >= 121.
SUPPORT = dict(
    t=6.0, hole_half=HALF + 0.5, screw_r=104.4,
    notch=(-88.0, -20.0, 14.0),     # (x0, z0, z1) on -X: the lift servo (to r 83.4) and its hanger over the travel
    core=dict(y_top=342.1, r=110.3, screws=(1.1, 91.1, 181.1, 271.1)),
    # open at the back: the top sector (r >= 84, swept +-25.7 deg) and the top drive (pinion, hub, servo)
    top=dict(y_top=476.8, r=116.0, screws=(348.0, 78.0, 258.0), open=((124.0, 248.0, 84.0), (146.0, 220.0, 0.0))),
)
LS_IC_RING_TOP = 361.1   # LS_IC_1's screw recesses' top (the 9.8 mm countersinks at r 104.4, read from its mesh)
TOP_RACE_TOP = 488.0     # the top lazy susan's inner race on TR-MR_SC's flange, y 480-488 (assemblies/kit/fastening.py)
BRACKET = dict(leg=20.0, w=20.0, t=5.0)   # 2020 corner bracket (cast aluminium, 20 x 20 x 20 x 5; inferred dims)
# the clamp: one M4 into a drop-in T-nut per bracket (inferred: what a drop-in nut in 6063 holds before its
# lips yield, and anodised aluminium on aluminium)
CLAMP = dict(preload_n=1200.0, mu=0.2)   # M4 (8.8) at ~2.5 N m

# ------------------------------------------------------------------ shell support (Hunter/Morton ring round the pedestal's foot)
BASE_RING = dict(y1=160.15, t=15.0, r=160.0, tab_t=5.8, tab_h=22.0,   # under the kit's base top B_T (160.25)
                 bt=(120.0, (60.0, 150.0, 240.0, 330.0)))   # B_T screwed down into it: r, angles (B_T solid there: 3.4 mm)

# ------------------------------------------------------------------ inserts / hardware
INSERT_M4 = {"type": "insert", "thread": "M4", "length_mm": 8.1, "od_mm": 6.3,
             "note": "Ruthex RX-M4x8.1 (hole 5.6; OD 6.3 from its datasheet, not on file)"}
TNUT_THREAD = 4.95     # our drop-in T-nut's thread length (it fills the slot's T cavity up through the lips)
STANDOFF_THREAD = 8.0  # female M4 standoff thread depth each end (inferred)
HUB_THREAD = 5.0       # 1906 hub tapped holes (as hunter_head/hardware.py)
POST_THREAD = 12.0     # M5 tapped into the 2020's 4.2 mm centre bore (as tapped)
