# DJ R3X physics simulation: research and design

*2026-09-28. How the twin moves from kinematic to a real rigid-body simulation that validates and
authors shows, and the "Lab" app that holds it. Companion to
[motion-control.md](motion-control.md). Figures marked **(measure)** are placeholders until
bench data replaces them (P4).*

## TL;DR

- **Engine: MuJoCo, one MJCF for both runtimes.** Python (`mujoco`) handles batch linting, CI
  and CantinaOS pre-flight. The official WebAssembly build (`@mujoco/mujoco`, Apache-2.0,
  3.14.0 on npm, 2026-09-22) runs the live Lab app.
  - MuJoCo simulates in reduced coordinates, so joints have no drift.
  - It has armature (reflected rotor inertia), damping and frictionloss built in.
  - Since 3.7 it has a `dcmotor` actuator. Its `input="pos"` controller with a `Vmax` clamp
    behaves like a hobby servo: a saturating P loop with back-EMF, i.e. a linear torque-speed
    curve, plus an optional thermal state.
  - `mj_inverse`, `mj_geomDistance` and the `jointlimitfrc`/`actuatorfrc`/`contact` sensors are
    all built in, and the WASM build exports them too.
- **Validate frames, not scripts.** Every show source becomes the same `{frame, t, targets[]}`
  JSONL by running through the existing TS pipeline, so the controller reference stays in one
  place. The physics validator replays those frames through servo actuators in MuJoCo and
  reports:
  - collisions and limit hits;
  - torque and speed saturation, and tracking error;
  - current per supply rail and brown-out;
  - thermal proxy, gear-tooth load, wobble, cable twist and the power-on jump.
- **Two apps, one core.**
  - **Show** (today's `sim/web`) stays the beautiful booth.
  - **Lab** (`lab.html`, same Vite project) is the engineering app: MuJoCo, plots, lint,
    timeline, IK.
  - The servo table, Maestro runner and joint sliders move to Lab.
- **The sample show would already fail lint** (worked example, section 8). Nothing in it breaks
  a servo on torque. What it does:
  - it starts up to 5 servos in the same 20 ms frame, every 500 ms, drawing an estimated
    8-17 A of inrush;
  - it bypasses every authored speed limit by 1.6-2.8×;
  - it parks the visor 0.5° from its soft limit, on an unmeasured linkage;
  - it slams 8 servos to position at power-on.

## 1. Where we are

- **Today the twin is kinematic.** `actuation/pipeline.ts` runs behaviour through a
  jerk-limited follower, then to 50 Hz pulse frames, then into a per-channel plant. That
  plant is a saturating P loop with deadband, load-derated speed, an accel cap from
  (stall − worst gravity)/inertia, and backlash.
- **Loads are static and there is no coupling between joints.** Each channel sees one
  worst-case gravity number from `rig.json dynamics`.
  - Inertia there is **point-mass per part at its COM**, from STL volume at 45 % PLA density
    (`build_r3x.py joint_dynamics`). It is fine for sizing but low for bulky parts, and zero
    for a ring-shaped part centred on its axis.
- **The model does not yet catch:**
  - self-collision;
  - servos, hardware and wiring mass (printed parts only);
  - the rotor inertia inside the servo;
  - supply current;
  - compliance of the 8×6 mm aluminium-tube arms.

## 2. Engine choice

| Engine | Articulation | Servo-relevant features | Browser | Python/CI parity | Licence | Verdict |
|---|---|---|---|---|---|---|
| **MuJoCo 3.14** | Reduced (generalised) coords | `armature`, `damping`, `frictionloss`, `gear`, `dcmotor` (K, R, PID, Vmax, thermal, LuGre, cogging), actuator `delay`, `forcerange`; fixed tendons with limits; `equality` connect/joint; `mj_inverse`; `mj_geomDistance`; limit/actuator/contact sensors | Official WASM + TS types, single- and multi-threaded builds; "still WIP" | Same engine and XML (`pip install mujoco`) | Apache-2.0 | **Primary** |
| Rapier (JS/WASM) | Impulse joints + reduced-coordinate multibody joints | PD motors with max impulse; cross-platform deterministic | Excellent, small | Rust only; no inverse dynamics | Apache-2.0 | Runner-up if MuJoCo WASM disappoints |
| Jolt (JoltPhysics.js) | Maximal coords + constraints, skeleton ragdolls | Position/velocity motors with force/torque limits | Good | C++ only | MIT | Game-oriented: no reflected inertia or ID |
| PhysX 5 (`physx-js-webidl`) | Reduced-coord articulations with drives | Drive stiffness/damping/maxForce, joint friction, armature | Community bindings, large | Isaac only | BSD-3 | Capable, but a heavy single-maintainer port |
| Bullet / ammo.js | btMultiBody exists | Basic motors | Old emscripten port, stale | PyBullet inactive | zlib | Skip |
| Drake | Multibody tree | `JointActuator` rotor inertia + gear ratio (reflected inertia), effort limits, trajectory optimisation | None (Meshcat viewer) | Python/C++ | BSD-3 | Only if we get into trajectory optimisation |
| Isaac Lab / Sim | PhysX | GPU RL, domain randomisation | No | Linux/Windows + NVIDIA GPU | Mixed | Skip: no macOS, built for RL |

**Why MuJoCo.**

1. **The mechanism class fits.** R3X is a tree of servo-driven hinges plus one slide and one
   four-bar. Reduced coordinates give exact joint limits and stable stiff gear ratios, which
   maximal-coordinate game engines approximate with constraint springs.
2. **Our actuator is a first-class object.** Rotor inertia × gear² is `armature`. The servo
   is a `dcmotor` with `nominal="V τ_stall ω0"`, a position controller, and `Vmax` equal to
   rail volts. `saturation` sets a continuous-torque cap. `thermal` adds a winding temperature.
3. **Analysis primitives come built in:**
   - `mj_inverse` gives the torque needed for a trajectory;
   - `mj_geomDistance` gives near-miss distance;
   - the `jointlimitfrc` sensor gives end-stop hits;
   - a tendon limit force gives the gear-tooth load.
4. **One XML runs in both places.** Batch runs in Python and live runs in the browser. We check
   parity with a test instead of maintaining two models.
5. **Its community shares our problem.** MuJoCo Menagerie and the SO-100 hobby-servo arm
   models, and Rhoban's BAM servo friction identification (ICRA 2025, with a MuJoCo
   integration), all model servo robots in it.

**Determinism.**

- MuJoCo is deterministic for the same build and inputs. Python and WASM may differ in the last
  bits, so **Python is the source of truth** for pass/fail.
- The browser is for feel. The parity test allows small tolerances (joint RMS < 0.1°, same
  collision events ±1 frame).

## 3. Modelling R3X in MJCF

### 3.1 Generator

**`build_r3x.py --sim-export`** (runs in Blender, which already has the undecimated meshes and
the regex part→joint map) writes:

- one OBJ per part in world coordinates;
- `parts.json` (part → joint, material, density);
- the joint table it already writes to `rig.json`.

**`sim/physics/mjcf_gen.py`** (plain Python) turns that into `r3x.xml`:

- **Bodies.** One body per rig joint, following `parent`, with `pos` at the pivot.
- **Inertia.**
  - Computed per part with trimesh (full tensor, not point mass).
  - Printed parts are modelled as 3 perimeters (shell) plus 15-20 % infill. Alternatively, set
    `inertia="exact"` on watertight meshes at the effective density, then correct from a
    weighed part.
  - Add explicit point masses for hardware the STLs lack:
    - servos: 60-80 g each for the 35 kg class, ~150 g for the 60 kg;
    - bearings, rails and carriages (MGN12H ~40 g each);
    - the aluminium tubes and cat6.
- **Joints.**
  - `range` comes from `rig.json`, `limited="true"`.
  - Each joint gets a `jointlimitfrc` sensor.
- **Passive terms** (starting values, then fitted in P4):

| Joint | frictionloss | damping | Note |
|---|---|---|---|
| Rings (10" lazy susan) | 0.1-0.3 N·m **(measure)** | 0.05 N·m·s/rad | Spring-scale pull at the rim |
| Head lift (2× MGN12, rack) | 2-4 N **(measure)** | 5 N·s/m | Preload + seals dominate |
| PLA hinge on bolt | 0.02-0.1 N·m | 0.005 | |
| Visor on F6001ZZ | 0.01 N·m | 0.002 | |

- **Armature: the term the current plant misses entirely.**
  - A hobby servo's output inertia is about J_rotor·N², with J_rotor 0.3-2 g·cm² and N ≈
    200-400 **(measure)**. That gives roughly 0.002-0.03 kg·m² at the horn.
  - The visor (7.5e-4 kg·m²) and the lift's head load reflected through a 12 mm pinion
    (m·r² ≈ 3e-4 kg·m²) are both **smaller than the servo's own rotor**.
  - So on the head the servo mostly accelerates itself. Step response is set by the servo,
    not the load, and the load matters mainly through gravity, impacts and compliance.
  - The rings are different. The lower ring carries the whole upper body (≥0.116 kg·m²,
    ÷3.8² = 0.008 kg·m² at the servo), which is comparable to the armature.

### 3.2 Mechanisms

- **Rack-and-pinion lift.**
  - A `slide` joint driven directly by the actuator, with `gear = 1/r_pinion` (83 rad/m at
    r = 12.0 mm, from `mmPerDeg` 0.21).
  - The actuator's armature and damping are then reflected correctly (× gear²).
  - Rail friction goes in the slide's `frictionloss`.
- **Ring gear sectors, with backlash.** Put a "servo gear" body on the ring's parent, with its
  own hinge and the servo's armature. Couple it to the ring with a **fixed tendon**:
  `L = q_servo − N·q_ring`, with `limited="true"` and `range = ±backlash/2`.
  - The tendon limit is the dead zone.
  - Its `solref` is the tooth stiffness.
  - `tendonlimitfrc` ÷ pitch radius is the **tooth force on the PLA sector**.
  - Use the same pattern for the neck gear (1.5:1 assumed). Other joints keep backlash in the
    actuator model.
- **Visor push-rod (four-bar).**
  - Horn body on the servo hinge in `head_tilt`, and a rod body hinged to the horn.
  - An `equality connect` from the rod tip to a site on the visor flipper.
  - The ratio and its non-linearity then come from geometry. That needs the horn radius, rod
    length and flipper arm from the "r3x - visor animation" STLs.
  - `efc_force` of that equality is the **push-rod load**.
- **Coupled claws.** `equality joint` with `polycoef` when one SG90 drives 2-3 fingers
  (extended profile).
- **Compliance and wobble.**
  - A two-axis passive hinge at each arm root and at the neck-tube base, with `stiffness` and
    `damping`.
  - Start from the servo's position stiffness (τ_stall / ~5° ≈ 30 N·m/rad for a 35 kg) in
    series with the 8×6 mm tube (EI ≈ 9.5 N·m²).
  - Tune so the natural frequency matches a tap test (target ~8-15 Hz **(measure)**).
  - Report tip acceleration and spectrum.

### 3.3 The servo actuator

```xml
<!-- 35 kg class at 6 V: 2.86 N·m stall, 7.52 rad/s no-load (servos.ts) -->
<dcmotor name="heroarm" joint="heroarm_servo" input="pos"
         nominal="6 2.86 7.52"          controller="40 0 0 0 0 6"
         armature="0.006" damping="0" delay="0.01" nsample="20"
         ctrlrange="-2.36 2.36"/>
```

- **`nominal` gives K and R.** K = V/ω0 = 0.80 N·m/A and R = 1.68 Ω, lumped at the horn. So
  `actuatorfrc/K` is the modelled current: 3.6 A at stall. Datasheet stall currents for the
  class are 2.5-4 A; gearbox losses push the real figure up **(measure)**.
- **`controller` kp is the P gain in torque space.** Full torque at ~4° of error: 2.86/0.07 ≈
  40 N·m/rad. `Vmax` = 6 V gives the saturating P loop with the back-EMF torque-speed line.
- **Deadband** is not in `dcmotor`. The stepping loop holds `ctrl` at the measured position
  while |error| < deadband (3-5 µs ≈ 0.4-0.7°). The same code runs in Python and TS.
- **Frame latency** is `delay` plus writing `ctrl` only on frame boundaries (20 ms at 50 Hz).
- **Rail sag** feeds back by rescaling `Vmax` each step from the rail model (section 4).

### 3.4 Collision geometry

- **Visual meshes stay in the GLB.** Collision uses **CoACD** convex parts per STL (threshold
  ~0.05, max ~16 hulls per part). MuJoCo collides each mesh by its convex hull, so every hull
  must be its own geom (`obj2mjcf` automates this).
- **Swap long members for capsules** (arm tubes, fingers, neck tube). They are faster and give
  clean distances.
- **Exclusions:**
  - parent↔child are filtered automatically (`filterparent`);
  - add `<exclude>` for the concentric rings (bearing faces touch by design), the neck tube
    against the top-ring bore, and sibling claw fingers.
  - Keep everything else, especially arms/claws against the head, visor and rings, and the
    head against the top ring.
- **Two contact modes:**
  - *physical*: contacts push, so a jam shows up as a stall;
  - *report*: `margin = gap = 5 mm`, so contacts are reported with depth or clearance but apply
    no force, and the motion stays as commanded.
- **Licensing.** Hulls derived from the CC BY-NC kit stay gitignored like the GLB.

### 3.5 Sensors

Per joint:

- `jointpos` and `jointvel`;
- `actuatorfrc`;
- `jointlimitfrc`.

Per ring:

- `tendonlimitfrc` (tooth load).

Per watched part pair:

- `distance`.

Plus:

- a `contact` sensor on the arm and head groups;
- `framelinacc` at the claw tips and the brow (wobble).

The electrical model sits outside the engine. It sums currents, applies per-rail limits and
feeds back V.

## 4. Show linting (validation)

**Inputs.** Everything becomes a frame log first, through the TS pipeline run headless in
Node:

- Maestro scripts;
- behaviour recordings;
- timeline shows (P3);
- CantinaOS/SimBridge logs;
- hardware controller logs.

**Two passes.**

1. **Intent pass.** Kinematic playback of the *commanded* trajectory plus `mj_inverse` gives
   required torque and speed against each servo's torque-speed envelope. It runs about 1000×
   real time.
2. **Physics pass.** Forward simulation at 1 kHz (implicitfast) with `dcmotor` servos, backlash
   tendons and flex joints gives what the robot *actually* does: lag, overshoot, jams and
   currents. Expect tens of × real time in Python for ~25 DoF **(measure)**.

| Check | Rule (default, per channel in `servo_map.json`) | Source |
|---|---|---|
| Collision | error: any active contact between non-excluded groups; warn: clearance < 5 mm | contacts, `mj_geomDistance` |
| Limit | error: `jointlimitfrc` > 0; warn: inside soft margin | sensor + rig limits |
| Torque | warn: peak > 50 % stall; error: > 80 % or saturation > 100 ms | `actuatorfrc`, ID pass |
| Speed / tracking | warn: ω > 90 % loaded speed, or \|cmd − actual\| > 2° for > 100 ms; info: exceeds authored vMax/aMax | pipeline vs sim |
| Current / brown-out | error: rail sum > buck rating for > 20 ms, or modelled rail < servo minimum | K, R model + rail map |
| Thermal | warn: RMS torque over 60 s > 30 % stall, or `dcmotor thermal` > limit | RMS / thermal state |
| Gear teeth / push-rod | warn: tooth force > PLA budget **(measure)** | tendon limit force, equality force |
| Wobble | warn: tip acceleration RMS, or command energy above f_n/3 | `framelinacc`, FFT of command |
| Cable twist | error: \|lower + middle + top + pan\| > budget (cat6 through the neck) | joint sum |
| Power-on | error: first frame ≠ park pose; warn: > N servos enabled in one frame | frame 0 |

**Rails.** The parts list has a 12 V supply, 1× 12 A buck and 5× 2 A bucks, but no wiring
map. Add a `rail` per channel and a `rails` table to `servo_map.json` (set point, continuous
and peak amps). Until then the linter assumes one 12 A servo rail.

**Output.**

- `sim/physics/reports/<show>.json`: an event list of `{t, kind, severity, joint/parts, value,
  limit}` plus per-channel time series.
- Exit code by worst severity, so `npm run lint:shows` / `python -m r3x_physics.lint` can gate
  a commit.
- Public-repo CI cannot see the gitignored model. Run it locally as a pre-commit hook, or on a
  self-hosted runner.

**Where physics runs.**

- **Python** is canonical: batch runs, CI, and pre-flight next to CantinaOS (lint a show file
  before the controller accepts it).
- **Browser (Lab)** runs live while authoring: MuJoCo in a Web Worker, 1 kHz stepping, posting
  pose, sensors and events at 60 Hz.
- Both load the same `r3x.xml`. A parity test runs the sample show in both.

**Visualisation (in Lab):**

- colliding geoms flash red, and a clearance ghost shows the near part;
- a per-joint torque-utilisation heat tint;
- a lint-marker lane on the timeline;
- uPlot charts: commanded vs actual, error, torque against the τ-ω envelope, and current per
  rail against its rating.

**For log archaeology,** export MCAP or Rerun. Foxglove and Rerun both animate a URDF from
joint-state streams, so emit a URDF alongside the MJCF.

## 5. Authoring (in Lab)

- **Show format `r3x-show v1` (JSON):**
  - per-joint keyframe tracks (joint units, easing: minimum-jerk / bezier / hold);
  - gaze-target and IK tracks;
  - event tracks: face and chest serial words (`SI…`, `Mnnn`, `Bnnn`) and audio cues;
  - `bpm` and an audio reference.

  It compiles to what the custom controller plays: **goals, not pulses**
  (`{joint, target, v/a/j}` at event times, as in motion-control §8). It can also export
  pulse frames for legacy playback. The Maestro importer turns steps into keyframes.
- **Timeline:**
  - tracks grouped by body region;
  - an audio waveform with a beat grid;
  - scrubbing drives the rig through the real pipeline, so what you see is what the
    controller does.
  - Record from the live behaviour engine or CantinaOS: capture targets, then simplify to
    keyframes (Ramer-Douglas-Peucker per track).
- **IK:**
  - The head gaze is analytic (pan/tilt/lift to a point).
  - "Point the hero claw here" is damped least squares on MuJoCo's `mj_jacSite` over top
    ring, shoulder and wrist, with limits and a posture prior. The chain is under-actuated,
    so show the reachable cone.
- **Collision-aware editing:**
  - while scrubbing, run `mj_geomDistance` on the watched pairs, colour clearance, and mark
    violations on the lint lane;
  - **auto-limit** retimes a segment (stretches it) until torque, speed and current pass,
    rather than clipping position, which is an infinite deceleration. For hard geometric
    conflicts, offer the nearest collision-free keyframe (project along the offending joint).
- **Sync with the hardware.** The calibration record is `servo_map.json`. System ID (P4) fits:
  - `dcmotor` K, R, kp and `Vmax`;
  - armature, frictionloss and backlash;
  - flex stiffness.

  The data comes from step, sweep and tap tests (motion-control §7), with BAM-style friction
  models where Coulomb-viscous fits poorly. Every hardware run's frame log is replayed in
  Lab next to the measured angles and INA219 rail currents, and the residuals are tracked
  per release.

## 6. Two apps, one core: Show and Lab

**Recommendation: a Vite multi-page app inside the existing `sim/web` package, not an npm
workspace (yet).**

- It is one `node_modules`, one `public/` (the gitignored model, draco, shows) and one test
  runner.
- The seam is a directory boundary enforced by a test.
- Promote `src/core` to `packages/core` only when a second consumer appears. The obvious
  candidate is `dj-r3x-dashboard`.

```
sim/
  model/build_r3x.py          + --sim-export (parts OBJ, parts.json)
  physics/                    Python: r3x_physics/{mjcf_gen, servo, electrical, lint, sysid, adapters}
  web/
    index.html                Show  -> /src/show/main.ts
    lab.html                  Lab   -> /src/lab/main.ts
    vite.config.ts            build.rollupOptions.input = { show: index.html, lab: lab.html }
    src/core/                 NO three.js materials/post; pure logic + model loading
      rig.ts  model.ts (GLB+Draco load, bind nodes to joints; extracted from show main.ts)
      actuation/ (pipeline, trajectory, servos -> servos.json shared with Python, servo_map.json, maestro.ts)
      firmware.ts  host.ts  link.ts  behavior.ts  audio.ts
      chest-firmware.ts (ChestFirmware, ChestHost, healthMask - split out of chest.ts)
      show-format/ (r3x-show schema, compiler, Maestro import)       <- P3
    src/show/                 main.ts booth.ts look.ts post.ts still.ts leds.ts chest-lights.ts
    src/lab/                  main.ts viewport.ts (neutral, matcap, collision/COM/axes overlays)
                              physics/{worker.ts, mujoco.ts, electrical.ts}  panels/{servos, joints, lint}
                              plots/ (uPlot)  timeline/  ik/
    public/model/             r3x.glb rig.json r3x.xml collision/*.obj   (gitignored)
    scripts/frames.ts         headless pipeline: show -> frames.jsonl (vite-node/tsx)
```

**Migration** (after the three agents working in `sim/web` land, in one commit by one agent):

1. `git mv` only, fixing import paths and nothing else. Split `chest.ts` and `leds.ts` where
   they mix emulation and rendering.
2. Prove the Show app is untouched:
   - `npm test`;
   - `npm run build`;
   - `scripts/render-harness.mjs`, with a pixel diff against pre-move stills that must be zero.
3. Add guard tests:
   - `src/core/**` may not import from `show/`, `lab/`, `postprocessing` or `n8ao`;
   - the Show bundle must not contain `@mujoco` (the multi-MB WASM loads only in Lab).
4. Add a `lab.html` skeleton on core, then move the panels.

**What moves out of Show:**

- **To Lab:**
  - the servo table and profile selector;
  - the "servo physics" checkbox;
  - frame-log export;
  - the Maestro script runner;
  - manual joint sliders and pivot axes.
- **Stays in Show,** because it is the performance view:
  - the demo, DJ and live link;
  - raw serial;
  - chest-light subsystems;
  - camera and quality.
- **Show gains:**
  - "Play show" for *compiled* shows only (read-only);
  - an "Open in Lab" link carrying the current show.

Show keeps the kinematic plant on silently, so its motion stays honest. It never runs MuJoCo.

**Lab look.** Neutral grey studio, flat or matcap shading, orthographic front/side views,
joint axes and COM markers, a timeline dock at the bottom, and an inspector with plots on the
right. It uses the same GLB and a material override, with none of the cantina look.

## 7. Prior art

- **Disney Research.**
  - *Computational design of mechanical characters* (Coros et al., SIGGRAPH 2013) simulates
    mechanisms to drive design from motion curves.
  - *Vibration-minimizing motion retargeting* (Hoshyari et al., 2019) modifies authored
    motion so compliant robots do not ring. It is the model for our "auto-limit/retime".
  - *BD-X* (2025) authors in animation tools, identifies actuator models, and trains in
    simulation.
  - *Olaf* (arXiv 2512.16705) found that actuator temperature rises with torque squared, and
    constrains it. That validates a thermal lint.
- **Bottango.** Keyframe and bezier authoring for animatronics, with open-source firmware for
  custom hardware and live drive. As far as its docs show, it has no torque, current or
  self-collision validation. That gap is what Lab fills.
- **Blender Servo Animation.** Bone → servo export with live mode, and "position jump
  handling" (it refuses big jumps on scrub). That is an ancestor of our power-on and jump
  checks.
- **ros2_control.** `mock_components`, and now the official `ros-controls/mujoco_ros2_control`,
  make sim and hardware interchangeable behind one interface. We copy the idea (the frame log
  as the interface) without ROS.
- **MuJoCo Menagerie / SO-100 models and Rhoban BAM.** The reference for identified hobby-servo
  armature and friction in MJCF.
- **Isaac Lab.** Skip: GPU/RL-centric, and no macOS.
- **Foxglove / Rerun.** URDF + joint-state log viewers. Use them for hardware logs, not
  authoring.

## 8. Worked example: the Maestro sample show

- The script has 26 blocks of `500 delay` (120 BPM) and sets no `speed`/`acceleration`. Every
  target is therefore a **step**, and each servo slews at full effort.
- Decoded with `servo_map.json` (centres, gears, 0.21 mm/° pinion) and `servos.ts` at 6 V:

| Channel | Commanded joint values | Limits (soft) | Full-effort speed vs authored vMax |
|---|---|---|---|
| headlift | +2.8 ↔ +14.2 mm, every beat | ±20 (±19) | 63 mm/s vs 40 → **1.6×** |
| visor | 0 ↔ **−11.5°**, every beat | −15..30 (**−12**) | 431°/s vs 150 → 2.9× |
| neck | −62.1 / 0 / **+64.6°** | ±70 (±66) | 287°/s vs 150 → 1.9× |
| headtilt | ±4.3° | −20..25 | small move |
| lowarm (lower ring) | +25 / 0 / −25° | ±35 (±32) | 113°/s vs 40 → **2.8×** |
| heroarm (top ring) | 0 / −21.8 / +21.4° | ±30 (±27) | 98°/s vs 40 → 2.5× |
| elbow (hero shoulder) | 0 / +14.4° | −35..45 | |
| hand (wrist) | −63.4 / 0 / +63.4° | ±90 | |

**What the linter would report:**

1. **Torque is not the problem on the lift.**
   - Printed head mass is 1.21 kg (realistically ~2 kg with servos and carriages). On a 12 mm
     pinion that is 1.5-2.4 kg·cm static against 50 kg·cm stall: 3-5 %.
   - Peak rack force at a ~0.3 g servo-limited acceleration is ~25 N. The servo could push
     408 N.
   - The lift covers its 11.3 mm in ~0.2 s and dwells ~0.3 s, so there is no tracking
     saturation.
   - Since the servo's own armature likely dominates, the head sees ~0.3 g jolts at 2 Hz. Their
     square-wave harmonics (6, 10, 14 Hz…) sit right on the estimated arm and neck modes.
     *Wobble warning:* expect visible claw shake on every beat.
2. **Visor.**
   - −11.5° is 0.5° from the soft limit and 3.5° from the hard stop, *if* the push-rod is 1:1.
     The ratio is unmeasured. A 1.3:1 linkage would drive into the stop at full stall.
   - Stall at the horn is 2.86 N·m. On a ~20 mm horn **(measure)** that puts ~140 N through
     the rod ends and the PLA flipper.
   - Normal flaps are much lighter: the visor is ~12 % of the reflected inertia, so ~15 N.
   - *Warn → error once geometry is in.*
3. **Current (the real finding).**
   - Modelled stall currents from `nominal`: 60 kg 4.3 A, 35 kg 3.6 A, 20 kg 2.3 A, 7 kg
     1.2 A.
   - Upper-bound inrush when moves start in the same frame:

     | Blocks | Inrush |
     |---|---|
     | lift + visor alone | 7.9 A |
     | 2, 6, 8 (+ hand) | 9.1 A |
     | 5, 13, 17 | 13.8 A |
     | 9 (lift, tilt, elbow, visor, neck) | **17.4 A** |

   - Against one 12 A buck this is a brown-out warning several times per loop. If the 60 kg
     sits on a 2 A buck, it is an error on every beat.
   - The fix is authoring, not hardware: stagger starts by 20-40 ms, and use minimum-jerk
     segments instead of steps.
4. **Power-on (block 1).** All 8 servos slew from wherever they rest; the lower ring alone is
   95° of servo travel. Worst case is ~26 A inrush. *Error:* play from a park pose and enable
   in staggered groups (motion-control §2).
5. **Rings.**
   - Low gravity load, but a step reverses the lower ring (≥0.116 kg·m² of upper body) at full
     servo effort.
   - Stall torque through ~31 mm and ~27 mm pinions is **92 N / 106 N on single PLA sector
     teeth**. With backlash, each reversal clunks.
   - *Warn* until the tooth budget is measured.
6. **Neck.** The +64.6° pan is 1.4° inside the soft limit. The −62→+65° swing (blocks 9→17)
   takes ~0.45 s of a 0.5 s beat, which is near saturation. Cable twist peaks at 65°: fine.
7. **Collisions.** Unknown until the MJCF exists. The pairs to watch are:
   - hero claw vs visor/brow with the neck at +65° and the top ring at −21.8°;
   - head vs top ring at lift −20 mm with tilt forward (not used by this show).

## 9. Phased plan

| Phase | Deliverables | Files | Effort | Risks |
|---|---|---|---|---|
| **P0: Show/Lab split** | Core extracted, Lab skeleton, panels moved, guard tests | `src/{core,show,lab}`, `lab.html`, `vite.config.ts` | 1-2 days | Collides with in-flight agents (do it after they land); Show visual regression (pixel diff gate) |
| **P1: MJCF + offline validator** | `--sim-export`; `mjcf_gen.py` (inertia, CoACD, excludes, dcmotor, lift gear, ring backlash tendons, visor four-bar); `servos.json` shared with TS; headless `frames.ts`; `lint` CLI + JSON report; the sample-show report above as a regression test; `rail`/`rails` in `servo_map.json` | `sim/model/`, `sim/physics/`, `sim/web/scripts/frames.ts` | 5-8 days | Unmeasured linkage/pinion/armature make numbers indicative; convex-decomposition quality on thin shells; licence (keep derived meshes ignored) |
| **P2: Lab live physics** | MuJoCo WASM in a worker; kinematic/physics toggle; collision flashes, torque tint, current and tracking plots; lint lane from Python reports; Python↔WASM parity test | `src/lab/physics/*`, `src/lab/plots/*` | 6-10 days | WASM bindings are "WIP"; mesh file loading into its VFS; worker ↔ render sync at 60 Hz |
| **P3: Timeline + IK** | `r3x-show v1` + compiler to controller goals; timeline with audio and beat grid; keyframes, easing, recording; gaze and claw IK; collision-aware scrub; auto-retime | `src/core/show-format/`, `src/lab/timeline/`, `src/lab/ik/` | 3-4 weeks | Scope creep toward Bottango; keep the format small and controller-first |
| **P4: Sim-to-real** | Bench protocol and fixtures (pot tap/IMU/ArUco, INA219 per rail); `sysid.py` fitting K, R, kp, armature, friction, backlash, flex; replay dashboard with residuals; recalibrated `servo_map.json` | `sim/physics/sysid.py`, `src/lab/panels/replay.ts` | 1-2 weeks + bench time | Hobby servos give no position feedback (needs a pot tap); unit-to-unit spread (identify per unit) |

**Order.** P0 → P1 (biggest value per day: it answers "will this show hurt the robot" before
the controller exists) → P2 → P4 (as soon as the hardware moves) → P3.

## Sources

- MuJoCo docs and XML reference (dcmotor, armature, tendons, sensors): https://mujoco.readthedocs.io/en/latest/XMLreference.html
- MuJoCo changelog (dcmotor 3.7.0, controller redesign 3.12.0, mj_geomDistance 3.1.6, 3.14.0): https://mujoco.readthedocs.io/en/latest/changelog.html
- MuJoCo JavaScript/WASM bindings: https://github.com/google-deepmind/mujoco/tree/main/wasm ; npm `@mujoco/mujoco`: https://www.npmjs.com/package/@mujoco/mujoco
- zalo/mujoco_wasm (earlier three.js port): https://github.com/zalo/mujoco_wasm
- Rapier joints and determinism: https://rapier.rs/docs/user_guides/javascript/joints/ , https://rapier.rs/docs/user_guides/javascript/determinism/
- Jolt motor settings: https://jrouwe.github.io/JoltPhysics/_motor_settings_8h_source.html ; JoltPhysics.js: https://github.com/jrouwe/JoltPhysics.js
- PhysX articulations: https://nvidia-omniverse.github.io/PhysX/physx/5.5.0/docs/Articulations.html ; physx-js-webidl: https://github.com/fabmax/physx-js-webidl
- Drake MultibodyPlant (reflected inertia, effort limits): https://drake.mit.edu/doxygen_cxx/classdrake_1_1multibody_1_1_multibody_plant.html
- CoACD (SIGGRAPH 2022): https://github.com/SarahWeiii/CoACD ; obj2mjcf: https://pypi.org/project/obj2mjcf
- Duclusaud et al., Extended friction models for servo actuators (ICRA 2025): https://arxiv.org/abs/2410.08650 ; BAM: https://github.com/Rhoban/bam
- MuJoCo Menagerie: https://github.com/google-deepmind/mujoco_menagerie ; SO-ARM100: https://github.com/TheRobotStudio/SO-ARM100
- mujoco_ros2_control: https://control.ros.org/rolling/doc/mujoco_ros2_control/doc/index.html
- Coros et al. 2013, Computational design of mechanical characters: https://dl.acm.org/doi/abs/10.1145/2461912.2461953
- Hoshyari et al. 2019, vibration-minimizing motion retargeting: https://la.disneyresearch.com/publication/publication-process-vibration-minimizing-motion-retargeting-for-robotic-characters/
- BD-X, Design and control of a bipedal robotic character: https://arxiv.org/html/2501.05204v1
- Olaf: Bringing an animated character to life in the physical world: https://arxiv.org/abs/2512.16705
- Bottango: https://docs.bottango.com/ ; Blender Servo Animation: https://github.com/timhendriks93/blender-servo-animation
- Foxglove 3D panel (URDF + joint states): https://docs.foxglove.dev/docs/visualization/panels/3d ; Rerun: https://rerun.io/examples
- uPlot: https://github.com/leeoniya/uPlot
- Local: `sim/docs/motion-control.md`, `sim/web/src/actuation/*`, `sim/web/public/model/rig.json`, `sim/model/build_r3x.py`, Drive `R-3X Animation/07 - r3x hardware.txt` and `08 - r3x maestro sample script.txt`
