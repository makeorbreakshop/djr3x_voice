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

Requires Blender 4.2+ (tested on 5.2), Node with `sim/web` installed, and basisu for the
texture compression (`brew install basis_universal`):

```bash
sim/model/build.sh
```

The script reads three inputs:

- the kit zip, default `~/Desktop/DJ-R3X/3D Models/DJ R3X - v2.zip`;
- the Mic-Mouth-Split and R-3X Animation folders on Google Drive
  (`MOB/Projects/DJ-R3X/`), when they are synced;
- optional flags: `--mouth <dir>`, `--budget <tris>` (default 700k), `--preview`,
  `--no-bake`, `--keep-passes`.

It runs in two steps, about 7.5 minutes on an M1 Max:

1. **Blender** (`model/build_r3x.py`) builds the geometry and `rig.json`, and bakes the
   textures. Intermediates go to `model/.work/`, which is gitignored.
2. **Node** (`web/scripts/pack-model.mjs`) puts the textures into the glTF material slots,
   compresses them to KTX2, compresses the meshes with Draco and writes
   `web/public/model/r3x.glb`. The file is about 13.8 MB: 9.5 MB of textures and 4.3 MB
   of mesh.

`rig.json` holds the joints, the LED anchors and the mass/torque/inertia model. Its
pivots and anchors are measured on copies of the parts decimated as the original
600k-triangle build did, so changing the visual triangle budget never moves the rig.

**Geometry.** Collapse decimation spends the triangle budget where it is seen. Hero parts
get 2.5 times the density: the head, visor, headphone cups, face plate, mouth, the RX-24
plate and the arms. Parts buried inside the rings get less. Visibility is ray-cast from
all directions. Every mesh then gets Smooth by Angle plus Weighted Normal (face area,
keep sharp), which removes the blotchy shading of long CAD triangles on large panels.

**Textures.** There are two UV atlases, one for the head and one for the body. Each has
three maps:

- **baseColor:** the paint and its weathering, 4K. The KTX2 codec is ETC1S.
- **ORM:** occlusion, roughness and metalness. The body's is 2K ETC1S. The head's is 1K
  UASTC, because close-ups look at the head and ETC1S blocks show in its highlights. The
  pack script's `--uastc-orm` flag makes both UASTC, for about 3 MB more.
- **Normal map:** tangent space, 2K, UASTC. It is baked from the undecimated STLs onto
  the decimated meshes. Each part is baked against its own high-poly twin, with a cage as
  deep as that part's measured surface gap, so a ray never picks up a neighbouring part.

xatlas packs the UV charts. It is pip-installed into `model/.work/pydeps` on first use.
It fills about 75% of each atlas, where Blender's packer managed 20% with these ~13k
CAD charts.

Pass `--no-bake` to skip the textures. The GLB then carries flat palette colours.

## The look

The paint is in the GLB. The build bakes it into standard glTF PBR textures: baseColor,
ORM and normal maps, plus clearcoat through `KHR_materials_clearcoat`. GLTFLoader turns
these into plain `MeshStandardMaterial` and `MeshPhysicalMaterial`. Anything that renders
glTF sees the same droid with no custom shader, including Blender, a path tracer and a
future WebGPU port.

The target is the Oga's Cantina animatronic, with colours taken from neutral-light
references of the Hasbro figure:

- weathered orange rings and base;
- charcoal middle ring, top cap and head shell;
- an orange visor with cream bars;
- blue headphone cups;
- blue RX-24 letters on a dark plate;
- light-grey arms with orange wrist cuffs.

- **Palette:** `web/src/palette.json` holds every class's colour and weathering amounts. It
  is the one place to edit them. The build reads it, so rebuild after a change.
  `sim/model/build.sh --keep-passes` keeps the raw bake passes, so the paint can be
  re-tuned without baking again.
- **Weathering (baked):** the same model the old runtime shader used, computed per texel
  from baked masks: ambient occlusion, exposed edges, position and normal, plus 3D noise.
  - Colour drifts and fades in patches.
  - Grime packs into crevices and runs down from them in streaks.
  - Paint chips on exposed edges, in clusters, down to a primer rim and bare metal. The
    chips are recessed in the normal map.
  - Scuffs, and dust on up-facing surfaces.

  The clearcoat takes its strength and roughness from the ORM map, so dust, grime and
  primer are not glossy.
- **Runtime:** `web/src/look.ts` only tunes texture sampling. It also adds a soft knee
  below 1.0 on lit surfaces, so only the LEDs cross the bloom threshold. `leds.ts` clones
  the eye-lens and light-pipe materials and adds its diffuser glow to them.
- **Debugging:** `?look=albedo|occ|rough|metal|normal` shows one baked channel.

## The booth

`web/src/booth.ts` builds the set and lights it. It recreates Oga's Cantina's DJ booth from
the reference photos, scaled to the droid, as procedural geometry (nothing downloaded):

- **The set:** a rock alcove with a low dome, seen through an arch in a rock facade. It
  holds the machinery panel behind the droid, four hanging six-driver speakers, cable
  bundles, flexible ducting, stepped cassette racks with a lit monitor, the bar counter
  that hides the pedestal, and the glowing ring under the base.
- **The light:** a warm tungsten lamp hangs in front of the droid. It is the key and the
  only shadow caster. Saturated red uplight washes the rock, a blue-violet fill comes
  through the arch, and small practicals add colour. The rock is plain tan stucco, so all
  of the red is light, as in the park.
- **Bounce:** a `LightProbeGrid` (three r186) is baked once at startup from the lit booth,
  so the moving droid picks up the red bounce wherever it is. The environment map is the
  booth itself, captured from the droid's head. While the grid is present, the map adds
  only specular light.
- **Haze:** a screen-blended cone in the key beam. It cannot push a surface over 1.0, so
  it never blooms.
- **Camera:** OrbitControls stay inside the open front. A move that would put the camera in
  the rock, the counter or behind the facade dollies in along the sight line instead.
- **`?booth=0`:** a clean turntable stage (dark floor, studio-style cantina lights) for
  neutral views of the model.

## Rendering

`web/src/post.ts` owns everything after the scene is lit. The passes run in this order:

1. **Ambient occlusion:** N8AO at half resolution with depth-aware upsampling. It fades
   out where a pixel is already past 1.0, because emitted light is not occluded.
2. **Bloom:** threshold 1.0, so only the LEDs glow.
3. **Tone mapping:** Neutral. AgX whitens LED cores more readily, but it also turns the
   burnt-orange paint salmon.
4. **SMAA:** edges. The canvas has no MSAA, since it only ever receives full-screen passes.
5. **Grade:** a built-in 3D LUT, generated in code. It adds a gentle S-curve, cool
   shadows and warm highlights.
6. **Film:** vignette, chromatic aberration at the frame edges only, and grain. The grain
   also dithers the dark booth against banding.

LED channel values are PWM duty cycles, which is linear light. The LEDs are decoded that
way, so their cores run hot enough for the tone mapper to whiten them the way a camera
does.

Resolution is dynamic. The render scale starts at 1.5x on a 2x display. It drops in
0.125 steps when frames run over about 17.8 ms and climbs back when there is headroom.

The **Render quality** control in the Camera section switches between two levels. Your
browser remembers the choice.

- **High:** AO on, scale up to 1.5x.
- **Low:** no AO, scale up to 1.25x.

URL flags:

| Flag | Effect |
|---|---|
| `?quality=low\|high` | Set the quality level. |
| `?tonemap=agx` | Compare against AgX. |
| `?dpr=1` | Fix the scale. |
| `?grain=0` | Turn off grain. |
| `?lut=/my.cube` | Grade from a `.cube` file instead. |
| `?post=hot` | Magenta marks every pixel above 1.0 before bloom, i.e. everything that blooms. |
| `?post=ao` | Show the AO term alone. |
| `?rest=kit` | Pose the model as the kit exports it (its display pose), without the canonical rest from `rig_limits.json`. For before/after comparisons. |

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

## Show system

R3X's performance content lives in the repo-root `show/` folder and follows `show/SPEC.md`.
CantinaOS reads the same files. There are three kinds:

- **Clips** (`show/clips/`): motion only, as joint keyframes.
- **Cues** (`show/cues/`): one moment across every department: clip, eyes, chest, lights,
  sfx.
- **Sequences** (`show/sequences/`): show elements on a time or beat clock.

`show/idle.json` holds the weighted idle policy. The sim code is in `web/src/show/`.

- **Offline:** `ShowPlayer` conducts, standing in for TimelineExecutor.
  - It schedules every item against a monotonic clock anchored at the start.
  - `wait for speech_end` pauses that clock.
  - `beat` clocks chase the BPM slider while music or DJ mode is on.
  - Each layer runs one item. A gesture inside its `interruptible_after` window queues
    the next request.
  - Departments dispatch locally:
    - clips go to the body;
    - `eyes` goes through the host emulator's EYE_COMMAND path;
    - `chest` goes to the chest firmware, which holds and then resyncs;
    - `lights` goes to the light desk, which holds and then resumes its program;
    - `sfx` plays the kit sounds in `public/sfx`, matched by name ignoring case and spaces;
    - `speak` shows a caption and runs the fake-amplitude mouth.
- **Live:** CantinaOS conducts and the sim's player stays idle. The sim renders
  `show.motion`, `stage.lights`, `show.sfx` and `motion.freeze`. It uses `show.started` and
  `show.ended` for ownership and the UI. Eyes and chest arrive on their existing topics.
- **Body compositor** (`show/body.ts`). The layers, bottom to top:
  1. procedural (`behavior.ts`);
  2. background: an authored loop per activity;
  3. gesture;
  4. show;
  5. puppeteer;
  6. freeze.

  Tracks are `additive` or `override`. Override weights ramp by joint class: visor 0.1 s,
  head 0.2 s, body 0.35 s. `intensity` scales an override's offset from the pose it
  started over. An interrupt freezes the clip and fades it out; it never cuts.

  A sequence's `owns` holds those joints and masks its overrides to them. Every other joint
  stays live (gaze, breathing). Keys use min-jerk easing, and the output still goes through
  the jerk-limited follower and the servo plant.
- **From Reachy Mini:**
  - **Listening freeze:** orient once, hold still, then ease back.
  - **Speaking handoff:** the gaze stays anchored on the listener while he talks.
  - **Speech wobble:** small head micro-motion from the mouth-LED amplitude.
  - **Weighted idle policy** (`show/idle.json`), with a separate `while_music` list.
- **Freeze** (`motion.freeze`) stops shows, gestures and idle, holds the servos where they
  are, and blends back over 0.5 s.
- **Puppeteer** (`show/puppeteer.ts`): a gamepad, or the sliders.
  - **Why the command space is fixed:** it is 8 named continuous intents in [-1, 1]
    (`gaze_yaw`, `gaze_pitch`, `lift`, `body_yaw`, `lean`, `visor`, `arm_raise`,
    `energy`), plus 8 cue slots and a mode. Disney trained an operator-imitation policy for
    BD-X on under an hour of exactly this kind of stream (arXiv 2504.02724). It transferred
    to another robot with the same interface, so this space is our future action space.
    Renaming or remapping a signal breaks every recorded take; add new ones at the end.
  - **Record take** logs the stream at 50 Hz as JSONL. The log includes the show layers,
    `show.*` events and any live `vision.*` events.
- **Servo whine** (optional, off by default): a quiet hum that follows the summed servo
  speed.
- **UI:** the Show system section has the lists, Play and Stop, intensity and speed, the
  layer readout, the idle toggle and the background loops.
- **Debug handle:** `__r3x.show.play(id, {intensity, speed, source})`, `.stop()`,
  `.running()`, `.freeze(on)` and `.expand(id, bpm)`, plus `__r3x.puppet.*`.

`npm test` lints every show file against `servo_map.json`:

- the channel soft limits and the joint-side `vMax`/`aMax`;
- `requires: "extended"` for extended joints;
- that every id resolves;
- nesting depth of at most 3;
- that tiers never escalate through nesting;
- that idle items are free tier.

It also checks that `expand()` and the clock-driven player both reproduce
`show/tests/golden/`. `web/src/show/rig_limits.json` and `kit_sfx.json` are committed copies
of the gitignored `rig.json` and sfx list, and the tests assert that they still match
whenever the originals exist.

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
- **Show system:** golden parity, the show linter, player timing (waits, beat chase,
  loops, interruption, tiers, freeze) and the body compositor.

## Render regression check

```bash
cd sim/web
npm run render:check     # render every shot and diff it against the baselines
npm run render:update    # accept the current renders as the new baselines
npm run render:perf      # fps at 1440x900 @2x with vsync off, high and low quality
```

`scripts/render-harness.mjs` drives headless Chrome over the DevTools protocol. It uses
the dev server on :5391 if one is running; otherwise it starts its own. The page runs in
still mode (`?still`, `web/src/still.ts`), which makes every shot deterministic:

- a virtual clock that only advances when the harness asks;
- a seeded `Math.random`;
- a fixed render scale and no grain;
- a fixed pose and LED state: idle, or speaking with the mouth held steady.

The named cameras are `full`, `face`, `face-speaking`, `chest`, `arms` and
`photo-oga-front-low`, plus an 8-angle turntable (`turn-000` to `turn-315`).
`photo-oga-front-low` is matched to the WDWNT reference photo.

Each shot is compared with pixelmatch and SSIM. The check fails when more than 0.5% of
pixels differ or SSIM drops below 0.98. Adjust these with `--max-diff` and `--min-ssim`.

Other options:

- `--only face,turn`: run only some shots.
- `--quality low`: render at low quality.
- `--extra tonemap=agx`: add any URL flags.

The contact sheet, `web/.render-out/index.html`, shows each render next to its baseline,
the diff and the reference photo.

Renders show the kit-derived model, so `web/.render-out/` and `web/.render-baselines/`
are gitignored. Keep your baselines locally. The reference photos are linked from
`~/Desktop/DJ-R3X/Reference Photos` (`--refs <dir>`) and never copied.

## Things the sim surfaced about the real system

- **Firmware THINKING overrun.** The right eye gets one dot, not two, and step 0 writes
  `eyeLeds[14]`, one past the end of the array. It probably lands on mouth LED 0 (shown
  cyan in the sim). Check on the hardware.
- **Dropped end-of-speech `M000`.** The adapter's 10 Hz throttle can swallow it when an
  amplitude update went out less than 100 ms earlier. The mouth then stays lit in ENGAGED.
  It is timing-dependent; in the live run the reset got through.

## Calibrate before trusting pulse numbers

`web/src/actuation/servo_map.json` marks what is assumed. For each channel:

1. Jog the servo to the rest pose (every joint 0, `show/SPEC.md` "Frame and zeros"; not the kit's display pose) and record `centerUs`.
2. Confirm the direction (`invert`).
3. Measure the neck gear and lift pinion.

The frame log (**Export frame log**) is the record to compare against high-speed video.
