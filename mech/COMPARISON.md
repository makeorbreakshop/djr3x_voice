# Geometry vs profile - what the sim and `robot.json` assume, and what the parts say

*Phase A, 2026-09-30. "Assumed" = `profiles/r3x/robot.json`, `sim/web/src/actuation/servo_map.json`
and the sim rig (`sim/model/build_r3x.py` JOINTS, `rig_limits.json`). "Derived" = measured on
the R-3X Animation parts and the kit in `mech/` (reproduce: `assembly.py`, `r3xmech.stepfeat`,
the gear measurements in `assemblies/r3x_animation/assembly.py` GEAR). Body frame, mm, deg.
Confidence: **high** = counted/measured on the part; **medium** = measured, placement or one
input inferred; **low** = inferred.*

## 1. Structural differences (read these first)

| # | assumed | derived | confidence |
|---|---|---|---|
| S1 | The head chain hangs off the top ring: `head_lift` parent = `torso_top` ("the rings stack", SPEC.md) | The neck tube runs from a **turntable in the base** through all three rings to the head. `head_pan` and `head_lift` are children of the **base**; ring motion does not move the head. A 6 in lazy susan at the top ring decouples the tube. | high (PNG 01/03, base-rotation parts) |
| S2 | Rings stack: lower -> middle -> top | The lower **and** middle rings both turn on lazy susans whose inner race bolts to **LS_IC_1**, a static core on the pedestal (guide p20, p33). Middle does not ride the lower ring; top rides the middle (guide p52). | high (guide) |
| S3 | `head_tilt` pivot at y 740 (head frame), in the head | R-3X: tilt axis on the yoke **below the head floor**, y **695** (45 mm lower). Hunter's gimbal: y 738.3. | medium (baseplate under H_Main_1 floor at y 721; bore 26 mm below its top face) |
| S4 | `torso_middle` driven (extended, ch 16) | Not motorised in the build. | high |

S1 changes gaze solving: head yaw in the world = `head_pan` alone, not `head_pan` + rings.
`Rig.aimAt` and SPEC.md "Frame and zeros" assume the opposite.

## 2. Per joint

Pulses: 270 deg servos map 500-2500 us -> 0.135 deg/us; 180 deg servos 0.09 deg/us.
Script values are Maestro quarter-microseconds.

| joint (ch) | quantity | assumed | derived | delta | conf. |
|---|---|---|---|---|---|
| **head_pan** (0 `neck`) | axis / pivot | vertical, (0,0,0) | vertical through the sector centre = base-center disc centre = body axis | 0 | high |
| | gear | **1.5** (`assumed`) | **4.00 : 1** - 15T pinion (24.0 deg pitch) walking a 60T-pitch internal sector (6.00 deg) | x2.67 | high |
| | range | hard +-70, soft +-66 | **+-33.8** (servo 270/4); sector allows +-42.9 | -52 % | high |
| | script sweep 800/1490/2208 us | +-63 deg head | **-23.3 / +24.2 deg** | | high |
| | v_max 150 deg/s | 600 deg/s at the servo | servo loaded speed ~420 deg/s: **joint max ~74 deg/s** at the 70 % rule | infeasible | high |
| | direction | invert false | internal gear with the servo on the moving turntable: turntable turns **opposite** to the servo spin -> invert relative to the rings | sign | medium |
| | centre 1490 us | assumed | script centre; mechanical zero = pinion mid-sector, set at assembly | bench | - |
| **head_lift** (1) | axis | +Y | +Y (MGN12 rail / 2020 direction) | 0 | high |
| | mm per servo deg | **0.21** (`assumed`, ~12 mm pinion) | **0.475** - 19T pinion on a 9.0 mm-pitch rack, pitch radius 27.2 mm | x2.26 | high |
| | range | +-20 | **+-37** (87 mm rack, one tooth kept engaged); servo would allow 128 mm | wider | high |
| | pulses for +-20 mm | 800-2200 us | **1189-1811 us** (+-42 deg) about 1500 | | high |
| | script 1600 <-> 2000 us bob | 11.3 mm | **25.7 mm** | x2.26 | high |
| | parent | torso_top | base turntable (S1) | | high |
| | servo | SERVO_60KG_270 | DS5160SSG 60 kg 270 (listing) | = | high |
| **head_tilt** (2) | axis | ear axis (1,0,0) at rest | X, parallel to the visor axle (PNG 04) | 0 | high |
| | pivot y | 740 | **695** (R-3X yoke) / 738.3 (Hunter) | -45 | medium |
| | gear | 1.0 (`assumed`) | **1.034 : 1** (19T pinion, centre gear teeth every 18.33 deg vs 18.95) | +3 % | high |
| | script 1092.5 <-> 1156 us | +-4.3 deg | +-4.1 deg | ~ | high |
| | centre 1124 us | assumed | 376 us off the servo centre in the builder's assembly; re-clock the horn at head level to get ~1500 | bench | - |
| | range | -20..+25 | no hard stop in the CAD; the centre gear's teeth span 270 deg | - | medium |
| **visor** (3) | axis | ear axis, y 770.6 | F6001ZZ bore axis; on the ear axis if the frame is shimmed 2.6 mm (768.0 standing on the floor) | 2.6 mm | medium |
| | ratio | **1 : 1** ("push-rod treated as 1:1") | **~1.39 visor deg per servo deg** at the parallel-crank rest (horn 25 mm / lever 18 mm, rod 58 mm); non-linear away from rest | +39 % | medium (servo position inferred) |
| | script flap 1239 <-> 1324 us | 11.5 deg | **~16 deg** | +39 % | medium |
| | invert true | assumed | depends on the servo side; not determinable from the files | - | - |
| **hero_shoulder** (4 `elbow`) | axis / pivot | (0.988,0,-0.152) kit, (40.8,528.4,227.5) | identical: through the HA_LE_1 / HA_RE_1 disc centres, disc normals agree; canonical (0.746,0,-0.666) at (158.1,528.4,168.5) | 0 | high |
| | servo | DS3218_DUAL 180 | 20 kg 180 deg listing ("double shaft" in the list), direct | = | high |
| | centre 1840 us | script "up" 7361 = rest | 340 us (31 deg) off the servo centre | bench | - |
| **hero_wrist** (5 `hand`) | axis | (0.114,0.674,0.727) kit | forearm axis (HA_SP_2), = | 0 | high |
| | servo | SERVO_7KG 180 | INJORA INJS2065 **micro** 7 kg | = (size: micro) | high |
| **torso_lower** (6 `lowarm`) | gear | 3.8 | **3.80 : 1** (25T / 95T-pitch sector, 14.4 / 3.790 deg) | 0 | high |
| | range | +-35 | +-35.5 (servo) / +-36.0 (sector) | ~ | high |
| | direction | invert false | internal sector turned by a static pinion: ring turns the **same** way as the servo | = | medium |
| **torso_top** (7 `heroarm`) | gear | **4.4** | **4.25 : 1** (20T / 85T-pitch sector, 18.0 / 4.238 deg) | -3.4 % | high |
| | range | hard +-30, soft +-27 | **+-25.5** (sector: 59.4 deg of teeth) - beyond it the pinion runs off the teeth | profile exceeds the hardware | high |
| | script 800/1511/2208 us | -21.8/+21.4 | -22.6 / +22.1 | +4 % | high |

Every `status: "assumed"` calibration (centre pulse, invert, gear/pinion) remains a bench
item: the files fix the ratios and the ranges, not how the horns were clocked. Recommended:
assemble every geared joint with the servo at 1500 us and the pinion centred in its sector /
the head level, so `center_us` = 1500 by construction.

## 3. Things the sim/profile has no slot for

- **head_roll** (Hunter's head, +-12 deg, two servos mixing tilt/roll).
- The pan stage's cable path: the tube turns +-34 deg; the lift slide moves +-37 mm.

## 4. Checks (`assembly.py --checks`, out/checks.json)

Assumptions: printed parts at 1.24 g/cc x 0.45 (kit shells, PLA) and 1.27 x 0.65 (mechanism,
PETG); catalogue masses for servos, rails, carriages, bearings, tube (26 x 1.5 mm aluminium),
2020; gear efficiency 0.85, linkage 0.90; lazy-susan rolling friction 0.02 x weight x 110 mm;
dynamic term = inertia x the profile's a_max. Model = kit shells + R-3X head + Morton frame
(Hunter's head is 949 g by its own module, so the R-3X head, 1.58 kg on the lift, is the
heavier case). Total 12.4 kg.

### Torque (worst pose over the joint and its descendants)

Status per motion-control.md 2: static demand <= 50 % of stall = pass. "70 % margin" =
0.7 x stall / demand.

| joint | servo (6 V stall) | load moved | servo demand | % stall | 70 % margin | servo speed needed / loaded available | verdict |
|---|---|---|---|---|---|---|---|
| head_pan | 35 kg 270 (29.2 kg.cm) | 3.0 kg | 0.58 kg.cm | 2 % | 35x | **600 / 423 deg/s** | torque fine, **speed fails** at the profile's 150 deg/s: cap ~74 deg/s, or a stepper |
| head_lift | 60 kg 270 (50) | 2.0 kg | 7.9 | 16 % | 4.4x | 84 / 253 | pass |
| head_tilt | 35 kg 270 | 1.55 kg | 7.4 | 25 % | 2.8x | 103 / 323 | pass |
| visor | 35 kg 270 | 0.15 kg | 1.0 | 4 % | 20x | 108 / 416 | pass (oversized; a 10-15 kg servo would do) |
| torso_lower | 35 kg 270 | 1.1 kg | 0.3 | 1 % | 72x | 152 / 427 | pass |
| torso_top | 35 kg 270 | 1.8 kg | 0.5 | 2 % | 41x | 170 / 424 | pass |
| hero_shoulder | 20 kg 180 (20.4) | 0.36 kg | 5.4 | 27 % | 2.6x | 120 / 296 | pass |
| hero_wrist | 7 kg micro (7.0) | 0.06 kg | 0.07 | 1 % | 72x | 180 / 594 | pass |

### Servo or stepper

| joint | recommendation | why |
|---|---|---|
| head_pan | **stepper** (NEMA 17 on the same 15T pinion or a belt, hall/endstop homing) | torque is 2 % of the servo; the limits are the 270 deg servo (+-34 deg head) and speed (74 deg/s max at 4:1). A stepper gives the full sector (+-43 deg, or +-70 with a full ring gear), 3-10x the speed, quiet holding. |
| torso_lower / torso_top | servo is adequate; stepper optional | the sectors, not the motor, limit travel (+-36 / +-25.5); loads are ~1 % of stall. A stepper only pays off if the sectors become full ring gears (then ring ranges are unlimited, needs a cable plan). |
| head_lift | keep the 60 kg servo | 16 % of stall, fast enough; a lead-screw stepper would be quieter but slower and heavier |
| tilt, visor, arm, wrist | servos | small, direct, well inside the rule |

### Interference (1 deg / 1 mm sweeps over the union of the derived and profile hard ranges)

| joint | swept | first contact | verdict |
|---|---|---|---|
| head_pan | -70..+70 | none | clear (with the upper neck guide excluded: its geometry is inferred) |
| head_lift | -37..+37 mm | none | clear |
| head_tilt | -20..+25 | none | clear |
| visor | -15..+30 | **-7 deg** push rod x head shell (H_Main_3); **+19 deg** push rod x visor servo | from the inferred servo position - relocate the servo in Phase B; kit visor arms graze the ear cups (<1 mm) from +13 deg |
| torso_lower | -35.5..+35.5 | none | lower/middle ring shells within 1 mm (kit nesting) |
| torso_top | -30..+30 | none | middle ring within 1 mm of TR-MR_SC / TR_SR (Morton's "more clearance" seal fixes this) and of the top sector from -21 deg |
| hero_shoulder | -35..+45 | none | clear |
| hero_wrist | -90..+90 | none | clear |

Already touching at rest (fit issues, not motion): the forearm (mainarm) with the kit elbow
block HA_EB_1..4 and disc HA_LE_1 (the elbow block must be reworked round the servo); the
lower-ring servo and mount with the pedestal top (Henley B_M_3 lip) and LS_M_Full's floor; the
tilt pinion with the yoke (2.5 mm - servo angle round the tilt axis is inferred) and with the
upper guide rail (inferred); the visor push rod with the visor frame (inferred linkage);
Morton's upper frame / Henley ring against the pedestal shells (snug fits, by design).
