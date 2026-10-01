# Robot profile: mech model vs `robot.json`

Generated 2026-10-01T01:01:48+00:00 by `mech/.venv/bin/python -m rigsync` from `out/r3x_droid/manifest.json` (built 2026-10-01T01:01:39+00:00; assemblies r3x_droid, base, base_panels_closed, r3x_neck_drive, hunter_head, morton_frame, lower_ring, middle_ring, top_ring; not fitted: base_panels_open, base_panels_port, head_r3x, randall_frame).

`robot.generated.json` is NOT live. To try it: runtime `R3X_PROFILE=profiles/r3x/robot.generated.json`, sim `?profile=generated`. To adopt it: `mech/.venv/bin/python -m rigsync --apply` (backs up robot.json).

**49 changes; 19 change how existing clips play (marked ⚠).**

## Changes

| | joint | field | robot.json | generated | note |
|---|---|---|---|---|---|
|  | torso_lower | hard | -35 .. 35 | -35.5 .. 35.5 |  |
|  | torso_lower | soft | -32 .. 32 | -32.66 .. 32.66 |  |
|  | torso_lower | animation | -32 .. 32 | -32.66 .. 32.66 |  |
|  | torso_lower | v_max | 40 | 79.4 |  |
| ⚠ | torso_middle | parent | torso_lower | - |  |
| ⚠ | torso_top | hard | -30 .. 30 | -25.5 .. 25.5 |  |
| ⚠ | torso_top | soft | -27 .. 27 | -23.46 .. 23.46 |  |
| ⚠ | torso_top | animation | -27 .. 27 | -23.46 .. 23.46 |  |
|  | torso_top | v_max | 40 | 71 |  |
| ⚠ | torso_top | actuator heroarm.gear | 4.4 | 4.25 | servo pulse per joint degree changes (hardware only; the sim is unaffected) |
|  | hero_shoulder | soft | -30 .. 40 | -31.8 .. 41.8 |  |
|  | hero_shoulder | animation | -30 .. 40 | -31.8 .. 41.8 |  |
|  | hero_shoulder | v_max | 120 | 282.1 |  |
| ⚠ | hero_wrist | soft | -86 .. 85.64 | -82.8 .. 82.8 |  |
| ⚠ | hero_wrist | animation | -86 .. 85.64 | -82.8 .. 82.8 |  |
|  | hero_wrist | v_max | 180 | 420 |  |
| ⚠ | head_lift | parent | torso_top | head_pan |  |
|  | head_lift | hard | -20 .. 20 | -37 .. 37 |  |
|  | head_lift | soft | -19 .. 19 | -34.04 .. 34.04 |  |
|  | head_lift | animation | -19 .. 19 | -34.04 .. 34.04 |  |
|  | head_lift | v_max | 40 | 99.8 |  |
| ⚠ | head_lift | actuator headlift.mm_per_deg | 0.21 | 0.475 | servo pulse per mm changes: the same clip moves the lift a different distance on hardware |
| ⚠ | head_pan | parent | head_lift | - |  |
| ⚠ | head_pan | hard | -70 .. 70 | -33.8 .. 33.8 |  |
| ⚠ | head_pan | soft | -66 .. 66 | -31.1 .. 31.1 |  |
| ⚠ | head_pan | animation | -66 .. 66 | -31.1 .. 31.1 |  |
| ⚠ | head_pan | v_max | 150 | 75.4 |  |
| ⚠ | head_pan | actuator neck.gear | 1.5 | 4 | servo pulse per joint degree changes (hardware only; the sim is unaffected) |
| ⚠ | head_tilt | parent | head_pan | head_lift |  |
|  | head_tilt | soft | -16 .. 21 | -18.2 .. 23.2 |  |
|  | head_tilt | animation | -16 .. 21 | -18.2 .. 23.2 |  |
|  | head_tilt | v_max | 100 | 225.1 |  |
| ⚠ | head_tilt | actuator headtilt.gear | 1 | 0.9331 | push-rod PAIR: tilt and roll both move servo_l and servo_r; the profile still models one channel per joint, so this is the rest-pose magnitude only (see mech.drives) |
|  | head_tilt | actuator headtilt.servo | SERVO_35KG_270 | GOBILDA_2000_25_2 |  |
|  | head_roll | soft | -10 .. 10 | -10.5 .. 10.5 |  |
|  | head_roll | animation | -10 .. 10 | -10.5 .. 10.5 |  |
|  | head_roll | v_max | 100 | 136.7 |  |
| ⚠ | head_roll | actuator headroll.gear | 2.4 | 1.5367 | push-rod PAIR: tilt and roll both move servo_l and servo_r; the profile still models one channel per joint, so this is the rest-pose magnitude only (see mech.drives) |
|  | visor | soft | -12 .. 27 | -13.2 .. 28.2 |  |
|  | visor | animation | -12 .. 27 | -13.2 .. 28.2 |  |
|  | visor | v_max | 150 | 335.1 |  |
| ⚠ | visor | actuator visor.gear | 1 | 0.9007 | servo pulse per joint degree changes (hardware only; the sim is unaffected) |

Not in the mech model (values kept): poker_claw_upper, poker_claw_lower, throttle_claw_a, throttle_claw_b, hero_claw_l, hero_claw_r, hero_claw_t.

## Joint tree

| joint | parent (robot.json) | parent (mech) | pivot mm | axis | drive | servo | first contact |
|---|---|---|---|---|---|---|---|
| torso_lower | - | - | 0, 0, 0 | 0, 1, 0 | gear - gear 3.8:1 | SERVO_35KG_270 | clear |
| poker_shoulder | torso_lower | torso_lower | 200.64, 372, 0.35 | -0.0192, 0, -1 | none | MG996R | clear |
| poker_wrist | poker_shoulder | poker_shoulder | 328.09, 465.7, 1.7 | -0.0192, 0, -1 | none | MG90S | clear |
| torso_middle | torso_lower | - | 0, 0, 0 | 0, 1, 0 | none | SERVO_35KG_270 | clear |
| throttle_shoulder | torso_middle | torso_middle | -211.29, 448.6, 1.51 | 1, 0, 0.0227 | none | DS3218 | clear |
| throttle_elbow | throttle_shoulder | throttle_shoulder | -211.33, 242.1, 3.26 | 1, 0, 0.0227 | none | MG996R | clear |
| throttle_wrist | throttle_elbow | throttle_elbow | -210, 321.6, 182.58 | 1, 0, 0.0227 | none | MG90S | clear |
| torso_top | torso_middle | torso_middle | 0, 0, 0 | 0, 1, 0 | gear - gear 4.25:1 | SERVO_35KG_270 | clear |
| hero_shoulder | torso_top | torso_top | 158.1, 528.4, 168.53 | 0.746, 0, -0.666 | direct - direct | DS3218_DUAL | clear |
| hero_wrist | hero_shoulder | hero_shoulder | 271.29, 685.42, 291.11 | 0.498, 0.664, 0.558 | direct - direct | SERVO_7KG | clear |
| head_lift | torso_top | head_pan | 0, 0, 0 | 0, 1, 0 | gear - rack: 0.475 mm per servo deg | SERVO_60KG_270 | clear |
| head_pan | head_lift | - | 0, 0, 0 | 0, 1, 0 | gear - gear 4:1 | SERVO_35KG_270 | clear |
| head_tilt | head_pan | head_lift | 0, 738.3, 0 | 1, 0, 0 | push_rod_pair - push_rod_pair at rest: servo_l +0.932, servo_r -0.933 servo deg per joint deg | GOBILDA_2000_25_2 | max +31.5 |
| head_roll | head_tilt | head_tilt | 0, 738.3, 0 | 0, 0, 1 | push_rod_pair - push_rod_pair at rest: servo_l +1.537, servo_r +1.536 servo deg per joint deg | GOBILDA_2000_25_2 | max +21 |
| visor | head_roll | head_roll | 0, 770.59, 0.881 | 1, 0, 0 | push_rod - push_rod at rest: visor_servo +0.901 servo deg per joint deg | SERVO_35KG_270 | max +33 |

## Mass properties (per link, from the CAD)

Total 12.91 kg. Printed parts: volume x density x fill; purchased parts: catalogue mass; inertia from each part's mesh as a uniform solid (a lower bound for printed parts).

| link | moved by | kg | COM mm | Ixx Iyy Izz kg m² |
|---|---|---|---|---|
| base/base | ground | 3.187 | -0, 96, -1 | 9.63e-02 1.43e-01 9.58e-02 |
| base_panels_closed/side_panels | ground | 0.314 | -0, 125, -0 | 8.69e-03 1.70e-02 8.63e-03 |
| r3x_neck_drive/neck_stage | ground | 0.616 | -2, -49, 14 | 5.20e-03 9.61e-03 4.66e-03 |
| r3x_neck_drive/turntable | head_pan | 1.081 | -16, 76, -20 | 6.25e-02 4.60e-03 6.15e-02 |
| r3x_neck_drive/slide | head_lift | 0.409 | -2, 279, -10 | 2.72e-02 1.56e-04 2.71e-02 |
| hunter_head/neck | head_lift | 0.062 | 0, 730, -0 | 8.67e-05 1.12e-05 8.92e-05 |
| hunter_head/cross | head_tilt | 0.003 | 0, 738, 0 | 8.76e-07 1.04e-06 1.96e-07 |
| hunter_head/head | head_roll | 1.403 | 2, 783, 1 | 9.50e-03 1.48e-02 1.23e-02 |
| hunter_head/visor | visor | 0.158 | 1, 802, 23 | 5.79e-04 1.75e-03 1.91e-03 |
| morton_frame/frame | ground | 1.234 | 0, 120, 0 | 2.14e-02 1.61e-02 2.14e-02 |
| lower_ring/lower_ring_mount | ground | 0.255 | -1, 366, -36 | 1.38e-03 2.10e-03 9.40e-04 |
| lower_ring/lower_ring | torso_lower | 0.896 | 29, 356, -6 | 7.16e-03 1.69e-02 1.06e-02 |
| lower_ring/poker_upper | poker_shoulder | 0.051 | 269, 421, 0 | 7.16e-05 1.04e-04 1.41e-04 |
| lower_ring/poker_hand | poker_wrist | 0.147 | 417, 466, 0 | 6.74e-05 3.47e-04 3.74e-04 |
| middle_ring/middle_ring | torso_middle | 0.658 | -26, 445, 5 | 6.09e-03 1.15e-02 6.16e-03 |
| middle_ring/throttle_upper | throttle_shoulder | 0.186 | -211, 410, -2 | 1.03e-03 1.31e-04 9.78e-04 |
| middle_ring/throttle_fore | throttle_elbow | 0.127 | -211, 281, 92 | 5.21e-04 4.85e-04 1.57e-04 |
| middle_ring/throttle_hand | throttle_wrist | 0.130 | -210, 396, 225 | 2.83e-04 8.83e-05 2.34e-04 |
| top_ring/top_ring | torso_top | 1.422 | 37, 530, 37 | 1.47e-02 2.76e-02 1.54e-02 |
| top_ring/hero_arm | hero_shoulder | 0.486 | 211, 610, 223 | 1.94e-03 1.52e-03 1.82e-03 |
| top_ring/hero_hand | hero_wrist | 0.084 | 282, 712, 300 | 9.69e-05 3.94e-05 9.74e-05 |

## Servos

| servo | model | drives | ratio | rated (stall) N m | no-load deg/s |
|---|---|---|---|---|---|
| lower_servo | SERVO_35KG_270 | torso_lower | 3.8:1 | 2.8603 | 431.1 |
| top_servo | SERVO_35KG_270 | torso_top | 4.25:1 | 2.8603 | 431.1 |
| hero_shoulder_servo | DS3218_DUAL | hero_shoulder | 1.0:1 | 1.9995 | 403.0 |
| hero_wrist_servo | SERVO_7KG | hero_wrist | 1.0:1 | 0.6865 | 600.0 |
| lift_servo | SERVO_60KG_270 | head_lift | 0.475 mm/servo deg | 4.9033 | 300.0 |
| pan_servo | SERVO_35KG_270 | head_pan | 4.0:1 | 2.8603 | 431.1 |
| servo_l | GOBILDA_2000_25_2 | head_roll, head_tilt | linkage (live Jacobian) | 2.4713 | 300.0 |
| servo_r | GOBILDA_2000_25_2 | head_roll, head_tilt | linkage (live Jacobian) | 2.4713 | 300.0 |
| visor_servo | SERVO_35KG_270 | visor | 0.9007:1 | 2.8603 | 431.1 |

## Clips that change

Authored keys outside the NEW animation range (inside the old one): these clamp once the generated profile is live. Additive tracks are checked about the rest pose.

| clip | joint | mode | keys span | old range | new range |
|---|---|---|---|---|---|
| look_around | head_pan | additive | -35 .. 30 | -66 .. 66 | -31.1 .. 31.1 |

## Behaviour notes

- Parent changes (⚠) move a joint to another branch. The mech model stands the head column on the base (the pan turntable is fixed to the base) and each ring on the static core, so turning a ring no longer turns the head or the ring above it. Body-yaw gestures lose their head motion, and the puppeteer's gaze spill (`show::puppeteer`: yaw past the neck limit goes to the rings) no longer extends the gaze.
- Narrower ranges clamp at the performer's follower; `look_around` above is the one committed clip that reaches past the new pan range.
- Actuator `gear`/`mm_per_deg` changes alter pulses on hardware only; the sim renders joint values.
- Torque per clip: Studio's lint and the Bench meter (sim/web/src/mechrig/torque.ts, mech/PHYSICS.md).
