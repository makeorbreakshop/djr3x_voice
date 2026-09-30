# DJ R3X motion control: research and design

*2026-09-28. Research brief behind `sim/web/src/actuation/` and `behavior.ts`. Numbers marked
**(measure)** are estimates until bench-tested (see "Validating the twin against hardware").*

## TL;DR

- **The sim is a digital twin, not an animation.** Behaviour produces targets; a trajectory
  generator enforces velocity/acceleration/jerk and soft limits; an output stage turns joint
  values into the exact pulse frames the hardware gets (1 µs resolution at 50 Hz by
  default); a servo plant model (deadband, lag, load-derated speed, backlash, frame latency)
  moves the 3D joints. Nothing on screen is faster or smoother than the frames allow.
- **Mechanics are the R-3X Animation build** (Drive `MOB/Projects/DJ-R3X/R-3X Animation`):
  eight servos, channel map taken from its sample show script. That build used a Pololu
  Maestro; **we will build our own motion controller**, and the sim's pipeline is its
  reference implementation (section 8). The printable kit's own guide specifies no
  actuators at all - it is a static, poseable build.
- **Trapezoidal motion is what makes robots look robotic.** Jerk-limited motion, layered
  "alive" micro-motion, head-leads-body timing, anticipation and speech accents are what
  make R3X read as a character.
- **One bug found by testing, not by reading:** the research brief's single-stage
  jerk-limited follower overshoots a 60° move by 5°. Replaced with a provably bounded
  two-stage design (below).

## 1. The hardware we are simulating

Sources: the kit guide (78 pp., static build), `07 - r3x hardware.txt`,
`08 - r3x maestro sample script.txt`, the ring/visor/wrist mechanism STLs and CAD
screenshots in the R-3X Animation folder.

| Channel | Script name | Joint | Servo (parts list) | Mechanism |
|---|---|---|---|---|
| 0 | `neck` | head pan | 35 kg | neck tube rotates (neck-center-gear) |
| 1 | `headlift` | head lift (prismatic) | 60 kg | tube slides on 2x MGN12 rails, rack + pinion in the base |
| 2 | `headtilt` | head tilt | 35 kg | gear at the neck top |
| 3 | `visor` | visor (brow + arms) | 35 kg | push-rod to a flipper on the ear axle, F6001ZZ bearing |
| 4 | `elbow` | hero-arm shoulder disc | 20 kg dual shaft | direct |
| 5 | `hand` | hero wrist roll | 7 kg | direct ("new wrist" parts) |
| 6 | `lowarm` | lower ring (poker arm) | 35 kg | internal gear sector, ~98°, **~3.8:1** (measured) |
| 7 | `heroarm` | top ring (hero arm) | 35 kg | internal gear sector, ~61°, **~4.4:1** (measured) |
| 17 | `headroll` | head roll | goBILDA 2000-0025-0002 (25 kg) | Hunter's head mech (`mech/assemblies/hunter_head`): push-rod gimbal, **~2.4:1** (inferred) |

- Ring ratios come from the gear STLs: inner-sector pitch radius ≈118 mm against ~31 mm and
  ~27 mm servo gears. They cap the rings at about ±35° and ±30°, so the rig's joint limits
  now match the mechanism.
- The middle ring, the throttle arm and the poker arm are **not** motorised in this build.
  The sim treats them as posable. The `extended` profile shows what nine more servos would
  add.
- **Head roll** comes from Hunter Smoke's head mech, Brandon's chosen head: a two-servo
  push-rod gimbal whose cross nests the roll (pillow-block bearings, axis +Z) inside the
  tilt (U-joint bearings, axis +X), both through the gimbal centre ~738 mm up. Chain:
  lift → pan → tilt → roll → visor. Range ±12° to first contact (inferred in the mech
  build), soft ±10°. `+head_roll` is right-handed about +Z: the crown leans to the droid's
  right. Channel 17 is the controller's spare PIO output, so the base build is 9 servos.
  **Gap:** on the real mech both servos carry tilt *and* roll (horns turn together for tilt,
  opposite for roll; from the manifest's linkage geometry ~1.6 servo° per tilt° and ~2.4 per
  roll° each). The pipeline maps one joint per servo, so the profile models an independent
  roll channel; driving Hunter's mech needs a two-joint mix on channels 2 and 17.
- Supply is 12 V into buck converters; 6 V at the servos is assumed.
- **Still assumed and needing the bench:** each channel's centre pulse (the rest pose, every joint 0: `show/SPEC.md` "Frame and zeros"),
  the direction, the neck gear ratio (1.5 assumed), and the lift pinion (0.21 mm/° ⇒ ±20 mm
  over 800-2200 µs).

### Faces and lights

- Eyes: WS2812 7-LED jewels (centre + ring) behind the kit's "Eye Diffusion Bulb"
  (`H_*Eye_4`, guide p.67/69).
- Mouth: 8 WS2812 in a V behind the **Mic-Mouth-Split** light pipe. Its bars sit in the
  holed grille's slots.
- Both are driven by `rex_face_v3_clean.ino`, which the sim emulates line for line.
- Speakers are in the four base pods (JBL), not the mouth.

## 2. Hobby servo physics

- **Signal.** ~20 ms frame, 1500 µs neutral. There is no standard pulse-to-angle mapping,
  so calibrate every servo.
- **Internal controller.** A saturating P loop with a deadband. Big errors run the motor
  at full voltage; inside the deadband the motor is off.
  - You command **position only**. Speed exists only because the setpoint moves.
  - A large step gives a max-effort slew, then overshoot under inertia.
  - Keep the commanded trajectory inside the servo's capability, and the servo behaves like
    a small first-order lag. That behaviour is predictable, which is what makes a twin possible.
- **Loaded speed.** `ω ≈ ω₀·(1 − τ_load/τ_stall)`. Plan at **≤70% of loaded speed**. Keep
  worst-case static load at **30-50% of stall**.
- **Resolution.**
  - Maestro targets are in 0.25 µs.
  - A PCA9685 at 50 Hz steps 4.88 µs, about 0.44° on a 180° servo. Its internal oscillator
    is ±8%, so calibrate per board.
  - Micro-motion must be at least ~2× (deadband + one step), about 2°, or it vanishes
    or stair-steps.
- **Backlash.** 0.5-2° of lost motion on reversal **(measure)**.
- **Power-on.** Servos slew at full speed to the first pulse they receive.
  - Hold outputs off, write the park pose, enable, then power joints about 200 ms apart.
  - Always shut down in the park pose.
- **Datasheet values** used by the sim are in `servos.ts`. The 35/60/7 kg classes are
  typical for their listings and should be swapped for the fitted servo's datasheet.

## 3. Limits and end stops

- **Three nested ranges.** From outside in: hard mechanical stop, then the soft limit
  (3-5° inside), then the animation range. Driving into a hard stop stalls a servo
  (MG996R: 2.5 A) and burns it out.
- **Never clamp position alone.** That is an infinite deceleration. Brake toward a limit at
  a speed you can still stop from: `v ≤ √(2·a·d)`. The follower does this for both soft
  limits on every tick, then clamps as a last line.
- **Per-channel calibration record** (in `servo_map.json`): centre pulse, trim, invert,
  gear or pinion, margin, v/a/j limits, and Maestro speed/acceleration.
- **Rotating stages.** Track the unwrapped angle and give each a cable-wrap budget. Not an
  issue with the current sector gears.

## 4. Smooth motion

| Method | Verdict |
|---|---|
| Trapezoid (v, a limits) | Infinite jerk at corners: the visible "clunk", and it excites wobble in printed arms |
| S-curve / Ruckig | Time-optimal and exact. No official TypeScript port |
| Minimum-jerk quintic (Flash & Hogan) | Natural for authored moves. Duration `T = max(1.875D/v, √(5.77D/a), ∛(60D/j))` |
| Critically damped spring (Holden, exact update) | Frame-rate independent, never overshoots. Alone it has infinite jerk on a step |

**What the sim uses** (`trajectory.ts`):

1. An exact trapezoid, which brakes for the target and for both soft limits.
2. A critically damped filter on its output, with **ω = e·jMax / (2·aMax)**.

- A critically damped filter is monotone, so there is no overshoot and v and a stay within
  the trapezoid's.
- Its peak jerk for an acceleration step S is S·ω/e. The worst step is 2·aMax (accelerating
  straight into braking), so jerk ≤ jMax always.
- Cost: about 2/ω of lag (~0.2 s at default limits).
- The brief's single-stage "brake-speed" follower overshot a 60° move by 5.4°: its
  braking distance ignores acceleration already in progress. A first fix of ω = e·jMax/aMax
  let jerk reach 1.7× the limit. Both failures are pinned by `test/actuation.test.ts`.

**Starting limits** (≈70% of capability, then re-fit from measurements):

| Joint | vMax | aMax | jMax |
|---|---|---|---|
| Rings (geared, inertial) | 40°/s | 120°/s² | 800°/s³ |
| Head pan | 150 | 600 | 4000 |
| Head tilt | 100 | 400 | 3000 |
| Head lift | 40 mm/s | 300 mm/s² | 4000 mm/s³ |
| Visor | 150 | 900 | 8000 |
| Shoulder | 120 | 500 | 3000 |
| Wrist | 180 | 900 | 6000 |

## 5. Reading as alive

`behavior.ts` is a layered stack, after Disney's gaze-controller layers and van Breemen's
iCat engine:

- **L1 Alive.**
  - Breathing on lift and tilt at 0.25 Hz.
  - Micro-motion at 0.1-0.3 Hz, ≥2° so it survives the hardware.
- **L2 Gaze.**
  - Saccades with ±20% timing jitter.
  - **The head leads and the rings follow 200-350 ms later** (overlapping action).
  - Anticipation: an 8% counter-move over 150 ms before turns larger than 25°.
- **L3 Activity.** Listening (brow up, head raised), thinking (brow down, looks away, claw
  taps), speaking, DJ.
- **L4 Speech.**
  - Servos can't track syllables (4-7 Hz with real mass). The envelope is low-passed to
    about 3 Hz for bob.
  - Accents fire when a fast envelope (15 ms attack / 110 ms release) rises 0.22 above a
    1.5 s average: a nod, a visor flick and a lift pop.
  - The mouth is a light and needs no smoothing: it takes the envelope straight through the
    firmware's `Mnnn` staging.
- **DJ.** The Maestro show's choreography, generalised to any tempo. Its `delay 500` steps
  are exactly 120 BPM:
  - lift and visor on every beat;
  - hand every 2 beats;
  - elbow every 4;
  - rings every 8.
- **Latency.** Advance servo commands by frame + rise + audio latency, roughly 60-120 ms
  **(measure)**, or delay the audio by the same amount.

## 6. Robotics tooling: adopt / later / skip

- **Adopt now**
  1. One servo command stream (`JointFrame` topic) with one safety layer, fanned out to a
     Maestro sink and the sim sink. The sim's frame log is already that format.
  2. The mass/torque table. It is in `rig.json` `dynamics`, from STL volume at 45% of solid
     PLA (~8.7 kg printed). Weigh a part and correct the density.
  3. Capsule self-collision checks in three.js.
  4. A keyframe show format plus a player.
- **Later**
  - Generate URDF from the JSON rig.
  - Batch-check shows in MuJoCo (Python) with `mj_inverse` for peak torque.
  - Bottango or Blender Servo Animation as the authoring tool.
- **Skip:** ROS 2 / ros2_control (copy its mock-hardware idea instead), Gazebo, Isaac Sim,
  Webots, PyBullet (inactive), and MuJoCo WASM for now.

## 7. Validating the twin against hardware

1. Log `{frame, t, targets[]}` on both sides. The sim's **Export frame log** writes this
   as JSONL.
2. Measure the real angle: a servo potentiometer tapped to an ADC, an IMU on each ring, or
   240 fps video with ArUco markers.
3. Per joint, with the real load attached:
   - 5/20/60° steps both ways → fit deadtime, τ, loaded speed and overshoot;
   - approach the same target from both sides → backlash;
   - a 0.2-5 Hz sine sweep → bandwidth;
   - a tap test on the arms → natural frequency. Keep motion content below f_n/3.
4. Replay the log through the plant and compare. Accept at RMS error < 2°, timing within one
   frame, and no soft-limit overshoot.

## 8. Our custom motion controller: what the sim says it must do

The sim's `actuation/` code is written to be ported, not just watched. Put trajectory
generation **on the controller**, as Bottango and ServoEasing do, so host jitter (USB,
Python GC, Wi-Fi) never reaches a joint.

- **Host → controller: goals, not pulses.** Send `{joint, target, [vMax, aMax, jMax]}` at
  event time, e.g. a gaze target or an accent. Send `{channel, us}` direct targets only for
  legacy shows. Sequence-number every message, and send a heartbeat.
- **On the controller, per channel at 200 Hz or more:**
  1. Clamp to soft limits.
  2. Run the two-stage follower from section 4 (a trapezoid, then a critically damped
     filter with ω = e·jMax/2aMax).
  3. Apply calibration: centre µs, trim, invert, gear or pinion.
  4. Output a pulse at 1 µs resolution.

  Port `trajectory.ts` and `Channel.valueToUs` directly.
- **Pulse output.**
  - Hardware PWM, all channels phase-aligned.
  - 50 Hz for analog servos. The digital 35/60 kg class usually accepts 100-333 Hz, which
    cuts the 0-20 ms output latency (verify per servo).
  - An RP2040 or ESP32 does this comfortably for 8-17 channels.
- **Safety.**
  - Park pose on boot: hold outputs off, write park, enable, then power up in staggered
    groups.
  - If the heartbeat stops for more than 250 ms, ramp to hold and stop accepting motion.
  - Per-channel current sense (an INA219 on the rail) catches stalls, since hobby servos
    report no position.
- **Controller → host: telemetry.** Commanded pulse per channel, follower state, and fault
  flags, at 20-50 Hz. The sim bridge can forward it, so the twin shows what the real
  controller is doing.
- **Frame log parity.** Log the same `{frame, t, targets[]}` records as the sim's
  **Export frame log**. Replaying a hardware log through the sim plant is the validation
  loop in section 7.

## Sources

- Pololu, servo control in detail: https://www.pololu.com/blog/17/servo-control-interface-in-detail
- Pololu Maestro user's guide (the sample show's script language): https://www.pololu.com/docs/0J40
- MG996R datasheet: https://www.electronicoscaldas.com/datasheet/MG996R_Tower-Pro.pdf
- DS3218: https://servodatabase.com/servo/miuzei/ds3218
- SG90 datasheet: http://www.ee.ic.ac.uk/pcheung/teaching/DE1_EE/stores/sg90_datasheet.pdf
- NXP PCA9685 datasheet: https://www.nxp.com/docs/en/data-sheet/PCA9685.pdf
- Adafruit PCA9685 guide (power-on /OE): https://cdn-learn.adafruit.com/downloads/pdf/16-channel-pwm-servo-driver.pdf
- ServoEasing: https://github.com/ArminJo/ServoEasing
- Bottango protocol: https://docs.bottango.com/apis-and-comms/bottango-protocol/commands/
- Ruckig (Berscheid & Kröger 2021): https://arxiv.org/abs/2105.04830
- Flash & Hogan 1985, minimum jerk: https://www.semanticscholar.org/paper/The-coordination-of-arm-movements:-an-confirmed-Flash-Hogan/7d8ac1ed3dc3fc96538372206da015e7dd4b251e
- Holden, Spring-It-On: https://theorangeduck.com/page/spring-roll-call
- Disney Research, realistic and interactive robot gaze (IROS 2020): https://la.disneyresearch.com/wp-content/uploads/root.pdf
- Disney Research, vibration-minimizing motion retargeting: https://la.disneyresearch.com/publication/publication-process-vibration-minimizing-motion-retargeting-for-robotic-characters/
- Disney BD-X: https://arxiv.org/html/2501.05204v1
- Takayama et al. 2011, animation principles for robots: https://dl.acm.org/doi/10.1145/1957656.1957674
- van Breemen 2004, iCat animation engine: https://www.researchgate.net/publication/4122040_Animation_engine_for_believable_interactive_user-interface_robots
- MuJoCo: https://mujoco.readthedocs.io ; urdf-loaders: https://github.com/gkjohnson/urdf-loaders
- Blender Servo Animation: https://github.com/timhendriks93/blender-servo-animation
- ChatterPi (speech-driven jaw): https://www.mcgurrin.info/robots/690/
