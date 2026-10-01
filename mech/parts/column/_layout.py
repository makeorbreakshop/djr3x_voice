"""The central column's layout: every number the column parts and assemblies/column share.

Body frame (show/SPEC.md "Frame and zeros"): mm, +Y up, +Z front, +X the droid's left, rest pose
(head_lift 0, head_pan 0). Every column part is modelled in this frame (its make() places it), so
the assembly's transform for a part is the identity and a parameter change moves the part.

What each group is, and where its numbers come from (`inferred` = not stated by any source; the
evidence follows):

* Gil plate: David Ferreira's aluminium base plate. Its outline is not in any file we hold (the
  Morton "Gil-Drive-with-Base-Plate_Electronics stack v8.stl" is a 170 mm electronics stack, not
  the plate). inferred: a 1/4 in (6.35) disc whose edge is flush with the kit skirt's foot (B_B
  r 227.4 at y -54.3), its top at the skirt's underside (the skirt sits on it, Morton photo
  20250810_165424), three Gil-drive wheel slots as in Morton's Fusion screenshot (2025-08-10).
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
GIL = dict(top_y=-54.3, t=6.35, r=227.5,                 # inferred: see the module doc
           slots=((0.0, 120.0, 240.0), 140.0, 205.0, 64.0),   # wheel slots: angles, r from/to, width (inferred)
           foot_bolt="M5")
FOOT = dict(t=6.35, half=75.0, bolt_at=62.0)              # 1/4 in 6061 foot plate, 150 x 150 (ours)
Y_GIL_TOP = GIL["top_y"]
Y_FOOT_TOP = Y_GIL_TOP + FOOT["t"]                        # -47.95

# ------------------------------------------------------------------ column
POST_C = 40.0          # post centres at (+-40, +-40): 2020 on a 100 mm square
HALF = POST_C + 10.0   # the column's outline half-width (50)
Y_POST_TOP = 584.0     # under the top cap: TR_N_3 at y 570 is an annulus r >= 89 (corners at 70.7), TR_N_2 at 598.7
TOP = dict(t=6.0, tube_hole_r=16.0)                       # 6061 top plate, 100 x 100
POST_LEN = Y_POST_TOP - Y_FOOT_TOP

# 2020 (5 series) slot, in the post's own frame (face at 10 from its centre): opening, lips, T cavity
SLOT = dict(open_half=3.1, lip_in=8.2, cav_half=5.5, cav_floor=6.2, taper_half=3.2, taper_floor=4.8)

# ------------------------------------------------------------------ lift (GT2 belt from the column foot)
BELT = dict(pitch=2.0, width=6.0, thick=1.38, tooth_pd_off=0.254, z=-40.0)   # GT2 6 mm (2GT) belt plane z = -40
DRIVE_T, IDLER_T = 60, 20
R_DRIVE = DRIVE_T * BELT["pitch"] / (2 * math.pi)        # 19.10 pitch radius
R_IDLER = IDLER_T * BELT["pitch"] / (2 * math.pi)        # 6.37
DRIVE_C = (0.0, -15.0)                                    # drive pulley centre (x, y) in the belt plane
IDLER_C = (-R_DRIVE + R_IDLER, 562.0)                     # tangent to the same vertical line x = -R_DRIVE (the clamped run)
LIFT_MM_PER_DEG = R_DRIVE * math.pi / 180.0               # 0.3333 mm per servo degree
LIFT = (-37.0, 45.0)    # travel: down as today's (the mouth meets the top cap's collars below that), up +45 (free)

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
PAN = (-90.0, 90.0)    # the mechanism turns +-135 (1:1, the servo's +-135 rule); the droid allows +-90: with the head
                       # low and the hero arm raised (+45), the head's right ear meets the arm's HA_PS_1 past pan +120
                       # relative to the top ring (measured 2026-10-01, workbench suite scan), and the top ring turns +-25.5
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
CLAMP = dict(y0=436.0, y1=452.0, x0=-24.0, x1=-8.0, z0=-46.0, z1=-34.0)
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
