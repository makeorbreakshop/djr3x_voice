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
* Column: four 2020 posts on a 100 x 100 square. The square is set by the kit: the pedestal's
  back grille pocket comes in to r 59.7 at y 165-265 (P_M_1/P_M_2/P_G_1, measured) and the rest of
  the pedestal wall is at r 86-88, so a 100 mm square (faces at 50, corners at r 70.7) clears both
  with >= 9.7 mm. Hunter's tower (photos 2025-08-06/11) is the same idea; his square is inferred
  larger, ours is set by the kit's pedestal.
* Ring heights: Anderson's sectors on the rings stay where his files put them (lower sector y
  345-390, top sector y 462-476, both at the back, measured from his parametric remodels); the
  column's ring drives put the same pinions at the same places (centre (-9.8, *, -83)).
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

# 2020 (5 series) slot, in the post's own frame (face at 10 from its centre): opening, lips, T cavity
SLOT = dict(open_half=3.1, lip_in=8.2, cav_half=5.5, cav_floor=6.2, taper_half=3.2, taper_floor=4.8)

# ------------------------------------------------------------------ lift (a rack climber, after Jason Charlton)
# The lift servo rides the carriage (beside the pan servo); its brass Mod 0.8 servo gear climbs a
# fixed vertical rack on the back-right post's inner face (x -40, the droid's right) (Charlton's build: "the lifting servo is
# attached behind the grid plate; the brass servo gear meshes with a long vertical gear rack attached
# to the rear of the structure", 2026-10-01). 48 teeth: pitch radius 19.2, 0.335 mm per servo degree,
# so the servo's +-135 deg is +-45.2 mm.
RACK = dict(module=0.8, x=-40.0, face=10.0, z_base=-30.0, pitch_z=-22.0, y0=350.0, y1=482.0, teeth_y=(362.0, 470.0),
            screws_y=(356.0, 476.0), cb_floor=4.0)
LIFT_PINION = dict(teeth=48, thick=6.0)
R_LIFT = RACK["module"] * LIFT_PINION["teeth"] / 2                     # 19.2
LIFT_MM_PER_DEG = R_LIFT * math.pi / 180.0                             # 0.3351 mm per servo degree
LIFT_PINION_C = (RACK["x"], 412.0, RACK["pitch_z"] + R_LIFT)          # (x mid-plane, y, z) at rest
LIFT = (-37.0, 45.0)    # travel: down as today's (the mouth meets the top cap's collars below that), up +45 (free)
# the lift servo: spline along +X into the pinion (its boss on the pinion's -X face), long side down
LIFT_SPLINE = (RACK["x"] - LIFT_PINION["thick"] / 2 + 4.1, LIFT_PINION_C[1], LIFT_PINION_C[2])
HANGER = dict(t=8.0, y_top=446.0, arm_y=(438.0, 446.0), arm_x1=-23.0, half_z=15.0)

# ------------------------------------------------------------------ servos (goBILDA 2000-0025-0002, vendor STEP)
SPLINE_ABOVE_BOSS = 4.1      # spline top above the case boss (vendor STEP)
HUB_H = 5.5                  # 1906 hub: servo face to arm face (vendor STEP)
HUB_TAP_R = 11.43            # 1906 hub: the four tapped M4 holes, on its own axes (measured, vendor STEP)
FLANGE_Y = (-16.9, -13.39)   # servo flange, below the spline top (measured, vendor STEP)
FLANGE_HOLES = [(10.0 + sx * 24.0, sz * 4.89) for sx in (-1, 1) for sz in (-1, 1)]   # (long axis, across)
CASE_BOTTOM = -44.1

# ------------------------------------------------------------------ carriage + pan
G0 = 480.0             # the pan gears' plane (bottom); everything on the carriage hangs off it
PAN_GEAR = dict(module=2.0, teeth=25, thick=8.0)          # 1:1, centre distance 50
PAN_CD = PAN_GEAR["module"] * PAN_GEAR["teeth"]          # 50
PAN = (-135.0, 135.0)  # the full mechanism: 1:1 on a goBILDA 2000 (+-150 standard mode), the suite's +-135 servo
                       # rule; the clock spring allows +-200. Past ~+120 relative to the top ring the low head's
                       # right ear meets the raised hero arm: a coupled limit to add (pan vs the arm and the top ring)
BRG = dict(id=30.0, od=42.0, w=7.0)                       # 6806-2RS (30 x 42 x 7), two
HUB = dict(r=15.0, flange_r=21.5, flange_t=4.0, bore_r=13.05, y0=423.0, top=500.0, screw_r=19.0)
Y_FLANGE = (G0 - HUB["flange_t"], G0)                     # 476..480
Y_BRG_UP = (Y_FLANGE[0] - BRG["w"], Y_FLANGE[0])          # 469..476
Y_BRG_LO = (436.0, 436.0 + BRG["w"])                      # 436..443
HOUSING = dict(r=25.0, y0=437.5, y1=474.5, seat_r=21.05, mid_r=18.0)   # bearings stand 1.5 proud of each end
COLLAR = dict(r=22.5, y0=423.0, y1=436.0)                 # 30 mm one-piece clamp collar, 45 OD x 13
CROSS_Y = 494.0        # the M4 cross bolt through hub and tube (above the gear)
TUBE = dict(od=26.0, wall=1.5, y0=430.0, top=696.3)       # 26 x 1.5 6061: Hunter's coupler bore (26); top = its bore stop
STANDOFF = dict(r=33.0, angles=(-90.0, 145.0, 180.0), length=24.0)   # McMaster 92871A317 (6 OD, M4, 24)
EAR = dict(t=6.0, r=38.0, w=12.0)
CASE = dict(y0=HOUSING["y1"] + STANDOFF["length"], floor=3.0, h=14.0, r=28.0, wall=2.0, hole_r=18.0, top_hole_r=14.5,
            inner_r=15.0)
YC = 456.0             # MGN12H blocks' centre (y) at rest
RAIL = dict(y0=365.0, length=180.0, first=10.0, pitch=25.0, x=POST_C, z0=HALF)   # MGN12 on the front posts' front faces
BLOCK = dict(w=27.0, l=45.4, h=13.0, h1=3.0, holes=20.0)  # MGN12H (Hiwin): body 27 x 45.4, H 13, 20 x 20 M3
FRONT = dict(z0=RAIL["z0"] + BLOCK["h"], t=8.0, half=53.5, y0=YC - 25.0, y1=YC + 25.0)
WEB = dict(half=22.0, z0=20.0, y0=438.0, y1=462.0)
PAN_SERVO_SPLINE = (PAN_CD, G0 - (HUB_H - SPLINE_ABOVE_BOSS), 0.0)   # spline top: (50, 478.6, 0)
CRADLE = dict(t=8.0, x0=20.0, x1=92.0, half_z=12.0)
COIL = dict(x=0.0, z=36.0, r=6.0, wire_r=2.0)             # retractile service cable (inside the column, front)

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

# ------------------------------------------------------------------ shell support (Hunter/Morton ring round the pedestal's foot)
BASE_RING = dict(y1=160.15, t=15.0, r=160.0, tab_t=5.8, tab_h=22.0)   # under the kit's base top B_T (160.25)

# ------------------------------------------------------------------ inserts / hardware
INSERT_M4 = {"type": "insert", "thread": "M4", "length_mm": 8.1, "od_mm": 6.3,
             "note": "Ruthex RX-M4x8.1 (hole 5.6; OD 6.3 from its datasheet, not on file)"}
TNUT_THREAD = 4.95     # our drop-in T-nut's thread length (it fills the slot's T cavity up through the lips)
MGN_THREAD = 4.0       # MGN12H mounting holes: M3, 4 deep (inferred: Hiwin lists M3 x 3.5..4)
STANDOFF_THREAD = 8.0  # female M4 standoff thread depth each end (inferred)
HUB_THREAD = 5.0       # 1906 hub tapped holes (as hunter_head/hardware.py)
POST_THREAD = 12.0     # M5 tapped into the 2020's 4.2 mm centre bore (as tapped)
