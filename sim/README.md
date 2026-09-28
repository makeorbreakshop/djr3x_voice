# DJ R3X sim

A 3D digital twin of DJ R3X in the browser (Three.js). It uses the real parts, the real
firmware logic and the real servo layout, and it can mirror a running CantinaOS live.

- **Model**: built by Blender from the printable kit's assembled-position STLs, plus the
  holed mouth grille and light pipe (Mic-Mouth-Split). Joints sit at pivots measured from
  the hinge and gear geometry.
- **Face**: a line-for-line emulation of `cantina_os/arduino/rex_face_v3_clean`. Eye
  jewels light the diffusion bulbs; the mouth V lights the light pipe behind the slots.
- **Chest lights**: the build finds the logic panels' openings itself: 3 panels × (8 LED
  holes + 3 windows). `web/src/chest.ts` is the reference for the planned Nano +
  addressable-LED controller. It speaks the face's serial words (`SI…SS`, `Mnnn`) plus
  `Bnnn` for tempo, so one command stream can drive both boards.
- **Servos**: the R-3X Animation build's mechanics (8 servos) on our own custom
  controller. Behaviour becomes jerk-limited trajectories, then 1 µs pulse frames, then a
  servo physics model, then the 3D joints. That pipeline is the reference implementation
  for the controller firmware. It also replays the sample show's Maestro-format script.
  Design and research: [docs/motion-control.md](docs/motion-control.md).
- **Live**: with CantinaOS running, `SimBridgeService` streams voice, speech amplitude,
  mode and music events to `ws://127.0.0.1:8765`.

## Licensing: the model is not in this repo

The kit ("DJ R3X - v2", Patrick Gray & David Ferreira) is CC BY-NC and group-only, and this
repository is public. `web/public/{model,sfx,shows}/` are gitignored. Build them locally.

## Build the model

Requires Blender 4.2+ (tested on 5.2):

```bash
sim/model/build.sh
```

The script reads three inputs:

- the kit zip, default `~/Desktop/DJ-R3X/3D Models/DJ R3X - v2.zip`;
- the Mic-Mouth-Split and R-3X Animation folders on Google Drive
  (`MOB/Projects/DJ-R3X/`), when they are synced;
- optional flags: `--mouth <dir>`, `--budget <tris>`, `--preview`.

It writes `web/public/model/r3x.glb` (Draco, about 3 MB) and `rig.json`. The rig file holds
the joints, the LED anchors and the mass/torque/inertia model. It also bakes two
weathering masks into a shared UV atlas, `r3x_occlusion.jpg` and `r3x_edges.jpg`, in about
20 s. Pass `--no-bake` to skip them; the sim then uses procedural wear only.

## The look

`web/src/look.ts` owns the materials, the lights and the environment. The target is the
Oga's Cantina animatronic: semi-gloss burnt-orange body, grey head and arms, blue cups and
RX-24 plate. The paint is worn to silver on exposed edges and dirty in the seams. It sits
in a dim booth under a warm tungsten spot, with teal and magenta practicals behind it.

- **Weathering:** comes from the baked masks plus 3D noise in each part's own space, so the
  wear moves with the part. It covers chips (a primer rim around bare metal), crevice
  grime, streaks, scuffs, dust on the tops and patchy fading. There are no texture
  downloads; the cantina environment is procedural.
- **LEDs:** lit surfaces pass through a soft knee below 1.0, so only the LEDs cross the bloom
  threshold.
- **Debugging:** `?look=occ|edge|wear|grime` shows one mask.

## Run

```bash
cd sim/web && npm install && npm run dev
```

Open the printed URL.

- **Offline:** use the demo conversation, DJ mode, mic, dropped audio files, the show
  script runner, raw serial (`SS`, `M128`…), and manual joints.
- **Live:** start CantinaOS (`python -m cantina_os.main`); the pill turns **LIVE**. Add
  `?offline` to a tab to keep it on the demo.
- **Disable the bridge:** set `SIM_BRIDGE_ENABLED=false`.

## Tests

```bash
cd sim/web && npm test
```

The tests cover:

- **Firmware parity:** the serial protocol, timing, mouth staging, flash→ENGAGED, and the
  THINKING overrun.
- **Host emulation:** the dropped `M000`.
- **Trajectory limits:** velocity, acceleration, jerk, soft limits and overshoot.
- **Pipeline:** channel map, frame rate and resolution, prismatic lift, script
  override, and servo lag.
- **Show-script (Maestro format) interpreter.**

## Things the sim surfaced about the real system

- **Firmware THINKING overrun.** The right eye gets one dot, not two, and step 0 writes
  `eyeLeds[14]`, one past the end of the array. It probably lands on mouth LED 0 (shown
  cyan in the sim). Check on the hardware.
- **Dropped end-of-speech `M000`.** The adapter's 10 Hz throttle can swallow it when an
  amplitude update went out less than 100 ms earlier. The mouth then stays lit in ENGAGED.
  It is timing-dependent; in the live run the reset got through.

## Calibrate before trusting pulse numbers

`web/src/actuation/servo_map.json` marks what is assumed. For each channel:

1. Jog the servo to the model's rest pose and record `centerUs`.
2. Confirm the direction (`invert`).
3. Measure the neck gear and lift pinion.

The frame log (**Export frame log**) is the record to compare against high-speed video.
