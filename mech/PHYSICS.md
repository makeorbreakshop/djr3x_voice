# Physics: what is computed, and what comes next

One rig, from the mechanical model: `mech/rigsync` reads the built droid manifest
(`out/r3x_droid/manifest.json`, Hunter's head by ChildRef) and writes
`profiles/r3x/robot.generated.json` (joint tree, limits, speeds, ratios, plus a `mech` block with
pivots, axes, drives, per-link mass properties and the push-rod linkages in the body frame) and
`robot.generated.diff.md`. The sim reads the `mech` block (`sim/web/src/mechrig/`).

```
mech/.venv/bin/python -m rigsync            # regenerate (never touches robot.json)
mech/.venv/bin/python -m rigsync --apply    # adopt it (robot.json.bak kept)
R3X_PROFILE=profiles/r3x/robot.generated.json   # runtime, without adopting
http://localhost:<port>/?profile=generated      # sim, without adopting
```

## Now: required servo torque (kinematic inverse dynamics)

`sim/web/src/mechrig/torque.ts`, live in Bench (meter), Studio (overlay + lint) and Build (under
the pad jog).

| Term | How |
|---|---|
| Mass, COM, inertia per link | Every part's display mesh as a uniform solid (volume, centroid, inertia tensor), scaled to its mass: catalogue `mass_g` for purchased parts, else volume x density x fill (PLA 1.24 x 0.45, PETG 1.27 x 0.65, metals solid). Summed per link with the parallel-axis theorem (`rigsync/inertia.py`). Total 13.0 kg vs r3xmech phase A's 12.4 kg (different head). |
| Joint effort | Virtual work over every link a joint carries: `J_v . m (a - g) + J_w . (I alpha + w x I w)`. Gravity exact per pose. `a`, `w`, `alpha` are second differences of each link's COM and orientation over the trajectory's last three samples, so coupling and velocity terms between joints are in, not only the joint's own acceleration. Live: a 50 ms low-pass on the acceleration part; lint: exact at 50 Hz. |
| Through the drive | Gears/direct: effort / ratio / efficiency (0.85 printed gear, 1.0 direct). Head lift rack: force x 0.475 mm per servo deg. Push rods (head tilt+roll pair, visor): `J^-T` of the closed-form rod solve's servo-angle Jacobian **at the current pose**, efficiency 0.90, so leverage loss near the ends shows up as load. |
| Turntables | Lazy-susan rolling friction while turning: 0.02 x weight x 110 mm (as phase A). |
| Rating | Stall torque at the 6 V supply from `r3x-performer-core/src/actuation/servos.rs` (copied to `rigsync/servos.py`). Load = torque / stall; the motion-control **70 % rule** flags anything above 0.7 (meter goes red; Studio lint marks the clip at the peak). |

Checked by `sim/web/test/mechrig.test.ts`: rest load of the lift = m g x pinion lever / eta;
a pure pan acceleration = I_yy alpha (+ friction) through 4:1; the gimbal Jacobian's signs
(tilt: horns opposite; roll: together); a 10x-speed `beat_bop` trips the 70 % lint. Committed
clips peak at 43 % (hero shoulder in `arm_throw`); the head servos stay under 10 %.

**Not modelled** (so not claimed): friction in gears, rods and ball links beyond the efficiency
factor; backlash; the servo's speed-torque curve and its own position loop (the servo plant in
`r3x-servo-ctl` owns that); cable drag; compliance; contact and collisions; non-uniform density of
printed parts (inertia is a lower bound for infilled parts). This is "required torque to follow
the commanded trajectory", not a simulation of what the robot would actually do.

## Next: full dynamics and contact (design)

### MJCF for MuJoCo (offline, Python)

`rigsync` already has everything an MJCF needs. `python -m rigsync --mjcf` would write
`out/r3x_mjcf/r3x.xml` + meshes:

- **Bodies**: one per link, nested along the joint tree (a link's `joint` = the profile joint that
  moves it), `pos` = the joint pivot, `<inertial>` from the `mech.links` block
  (`mass`, `pos` = COM, `fullinertia`).
- **Joints**: `hinge`/`slide` with `axis`, `range` = hard limits, `armature` = rotor inertia x
  ratio^2 (from the servo class; estimate ~1e-6 kg m^2 per servo until measured), `damping`
  small and explicit.
- **Actuators**: `<position>` per servo with `gear` = ratio, `forcerange` = +/- stall, `kp` from
  the servo class's stiffness (tune against a bench step response); the push-rod pair as two
  actuated horn hinges with the rods as `<equality connect>` constraints (ball ends), so the
  gimbal's closed loop is solved by the engine, not by our closed form.
- **Geometry**: visual = the display GLBs; collision = convex decompositions (CoACD, a few
  hulls per part) for the parts that can touch (the interference pairs in the checks), primitives
  elsewhere; `contype`/`conaffinity` excluding bolted neighbours.
- **Validation**: `mj_inverse` on each committed clip must match `torque.ts` within a few % (same
  masses, same trajectory); then `mj_step` with the servo model to see tracking error and
  contact. Only then call anything "physics".
- Cost: `mujoco` is a pip wheel (not installed in `mech/.venv` today); ~300 lines in rigsync.

### Rapier in the browser (live)

`@dimforge/rapier3d-compat` (WASM) for live contact in the sim: multibody joints for the tree,
impulse joints for the rod loops, convex hulls from the same decomposition. Worth it only for
interactive contact (a hand hitting the decks); the torque model above already covers loads, and
MuJoCo is the better tool for validation. Decide after the MJCF comparison.

## Direction (Brandon, 2026-09-30): physics built in, not exported

Not scheduled yet; this is how it should be built when it is.

- **MuJoCo in-process, no files.** Build the model with MuJoCo's `MjSpec` API straight from the
  workbench assembly on every rebuild (links, masses/inertias from materials, joints + limits,
  rod/rack loops as equality constraints, servos as position actuators with torque/speed limits,
  friction/damping). MJCF is a debug dump only. The browser can run the same model via MuJoCo's
  WebAssembly build for live physics in the 3D view.
- **FEA (CalculiX) on load-bearing parts**, loads taken from the MuJoCo run of a clip (worst
  instant per mount), plus modal analysis of the neck/lift stack at full extension; the
  resulting stiffness goes back into MuJoCo as a compliant joint, so the sim wobbles like the
  real head.
- **"Simulate this clip"** in Build/Studio: servo torque, bearing/pin loads, tracking error and
  overshoot over time; pass/warn/fail against safety factors.
- **Materials drive mass and stiffness**: per-part material + print settings (walls, infill,
  orientation), derated for printed plastic; calibrated by weighing parts and a phone
  accelerometer tap test.
- Brandon never edits CAD: intent -> parameters/designs -> rebuild -> checks + physics -> 3D view.
