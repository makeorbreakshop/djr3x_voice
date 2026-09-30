# R3X Show Format v1

A single, file-based format for R3X's performance content. It is read by **both** runtimes:

- **The r3x performer (Rust, `r3x-runtime --bridge`)** is the live conductor since Phase 3. **CantinaOS (Python)** validates and catalogues the files (Claude's prompt, CLI, plan steps) and sends the requests over its bus tap; the bridge puts the performer's events back on the CantinaOS bus.
- **The sim (TypeScript)** renders the body, and plays everything offline when CantinaOS is not connected.

There are three kinds of file, from small to large:

| Kind | What it is | Lives in |
|---|---|---|
| **clip** | Motion only: joint keyframes (a nod, a point, a visor flip) | `show/clips/*.json` |
| **cue** | One moment across departments: clip + eyes + chest + lights + sfx | `show/cues/*.json` |
| **sequence** | A show element: a timeline of cues/clips/actions on a clock | `show/sequences/*.json` |

`show/idle.json` holds the weighted idle policy. `show/tests/fixtures/` + `show/tests/golden/`
are the cross-language parity tests.

The file name must equal the `id`. Ids are `snake_case`, unique across all three kinds.

## Common fields

```jsonc
{
  "id": "nod",
  "kind": "clip" | "cue" | "sequence",
  "title": "Nod",                 // human label (UI, logs)
  "description": "Quick yes-nod", // ONE line; this is what Claude and Jev see in their catalogue
  "tags": ["yes", "agree"],       // for search / fuzzy resolve
  "tier": "free" | "cheap" | "show"
}
```

**Tiers** decide who may trigger an item, mirroring the Jev router's risk tiers:

| Tier | Who may trigger it | Examples |
|---|---|---|
| `free` | anyone: reflexes, idle policy, Jev (loose gate), Claude tags | nod, glance, eye flash |
| `cheap` | Jev (strict gate), Claude tags or tool, timeline, UI | dance groove, big arm throw, light flash |
| `show` | Claude tool, timeline/BrainService, CLI/UI only; never Jev or a reflex | dj_intro, malfunction |

## Clip

```jsonc
{
  "id": "nod", "kind": "clip", "tier": "free",
  "duration": 0.9,                       // seconds at speed 1
  "interruptible_after": 0.3,            // before this, a same-or-lower-priority request queues
  "tracks": {
    "head_tilt": {
      "mode": "additive",                // "additive" (adds to what lower layers do) | "override"
      "keys": [[0, 0], [0.25, -8], [0.5, 4], [0.9, 0]],   // [t_seconds, value]
      "ease": "minjerk"                  // "minjerk" (default) | "linear" | "step"
    }
  }
}
```

- **Units:** degrees for revolute joints; millimetres for `head_lift` (prismatic).
- **Joint names are the rig's:** `head_pan`, `head_tilt`, `head_roll`, `head_lift`, `visor`, `hero_shoulder`, `hero_wrist`, `torso_lower`, `torso_top`. These nine are driven by the base (`r3x_animation`) profile: the 8-servo R-3X Animation mechanics plus the head roll of Hunter's head mech (±12° hard, ±10° soft).
  - The extended joints are `torso_middle`, `hero_claw_*`, `throttle_*` and `poker_*`. They are allowed, but the clip must say `"requires": "extended"`.
  - The linter flags extended joints used without `requires`, because the base build cannot perform them.
- **Keys:** the first key must be at t=0. Additive tracks must start and end at 0. Override tracks blend in and out over `blend` seconds.
  - The default `blend` depends on the joint class, as Disney's BD-X/Olaf engine does: light "show function" parts blend faster than the body.
    - `visor`: 0.1 s
    - `head_pan`, `head_tilt`, `head_roll`, `head_lift`: 0.2 s
    - `hero_*`, `torso_*`, and the extended arm joints: 0.35 s
  - Department actions (eyes, chest, lights) switch within 0.1 s.
  - A track may set its own `blend`.
- **Params at trigger time:**
  - `intensity` (0–1.5, default 1) scales additive values and the offsets of override values from the current pose;
  - `speed` (0.5–2, default 1) scales time.
- **Linted against `sim/web/src/actuation/servo_map.json`:** joint limits (rig min/max, with margin) plus `vMax`/`aMax` after the gear ratio. A clip that the channel cannot physically follow fails the test.

## Frame and zeros

Every value in a clip is relative to one body frame and one rest pose, the same for the sim,
the performer and the servos:

- **Forward (+Z) is the base's front**: the speaker pod between the `[ ]` brackets. **Up is +Y.**
  The droid's left is +X.
- **Every joint at 0 is the rest pose of the park droid:** the head faces forward and is level,
  the visor is at the bottom of its flap; the lower ring has a vent at the front and the poker arm out to the left;
  the middle ring has its three logic panels at the front and the throttle arm out to the
  right; the top ring has the RX-24 plate at the front and the hero arm up at the front-left.
- **Signs:** `+head_pan` and `+torso_*` turn toward the droid's left (+X). `+head_tilt` and
  `+visor` tip down. `+head_roll` is right-handed about +Z (forward) through the gimbal
  centre: the crown leans to the droid's right (−X), right ear down. The roll nests inside the
  tilt at the same pivot, so it turns everything above the tilt (shell, visor, eyes).
- The rings stack. A ring's value turns everything above it, so `head_pan` 0 faces forward
  only while the rings are at 0. Gaze targets are solved from the current pose (`Rig.aimAt`),
  and `gaze off` faces the base's front.
- A servo at its `center_us` puts its joint at `center_value` (0 = the rest pose).

The printable kit is exported in a display pose (rings turned up to 56° from this rest). Until
the model build bakes the rest in, `sim/web/src/show/rig_limits.json` carries the correction
(`body_yaw`, per-joint `zero_offset`), and the sim applies it once in the Rig. Only the 3D
geometry uses these numbers. Values, ranges and pulses are already about the rest.

## Cue

A cue is one moment: every department fires together, optionally with small offsets.

```jsonc
{
  "id": "hype_drop", "kind": "cue", "tier": "cheap",
  "actions": [
    { "at": 0.00, "do": "clip",   "id": "arm_throw", "intensity": 1.0 },
    { "at": 0.00, "do": "eyes",   "pattern": "excited", "color": "#ffb000", "duration": 1.5 },
    { "at": 0.00, "do": "chest",  "command": "X2", "hold": 1.5 },
    { "at": 0.00, "do": "lights", "cue": "red_flash", "fade": 0.1, "hold": 2.0 },
    { "at": 0.05, "do": "sfx",    "id": "airhorn" }
  ]
}
```

**Department actions (`do`).** This one table is the whole contract between file, runtime and hardware.

| `do` | Fields | Live topic (CantinaOS bus, emitted by the r3x bridge) | Sim renders |
|---|---|---|---|
| `clip` | `id`, `intensity?`, `speed?` | none since Phase 3 (the performer drives motion) | body compositor (gesture/show layer) |
| `eyes` | `pattern`, `color?`, `intensity?`, `duration?` | `EYE_COMMAND` (existing `EyeCommandPayload`) | face firmware via host |
| `chest` | `command` (chest serial word, e.g. `X2`, `SF`, `M200`), `hold?` s | `chest.override` (new; the chest service applies it, then returns to status after `hold`) | chest firmware |
| `lights` | `cue` and/or `mode`, `fade?`, `hold?`, `rig?` | `stage.lights` (new) | `stagelights.ts` desk |
| `sfx` | `id` (file stem under the sim's sfx folder) | `show.sfx` (new) | browser audio |
| `speak` | `text` | the existing path that makes ElevenLabs speak a line | offline: caption plus fake amplitude |
| `duck` / `unduck` | none | existing `AUDIO_DUCKING_START` / `STOP` | none |
| `wait` | `for`: `"speech_end"` | not emitted; the performer holds its run | same |

A `hold` of 0 or missing means the department keeps the state until something else changes it.
`lights` with `hold` returns to whatever the desk's mode program was running.

## Sequence

A show element is a timeline of cues, clips and actions on one clock.

```jsonc
{
  "id": "dj_intro", "kind": "sequence", "tier": "show",
  "clock": "time" | "beat",        // beat: `at` is in beats; seconds = beat * 60 / bpm
  "bpm": 120,                      // default when clock=beat and there is no live music tempo
  "layer": "show",                 // "show" (default) | "gesture"
  "owns": ["head_pan", "visor"],   // joints the sequence overrides; others stay live (gaze, breathing)
  "loop": false, "length": 16,     // loop length in the clock's units (needed when loop=true)
  "track": [
    { "at": 0,  "cue": "visor_reveal" },
    { "at": 4,  "clip": "ring_sweep", "speed": 1.2 },
    { "at": 8,  "do": "speak", "text": "Time to boogie!" },
    { "at": 8,  "do": "wait", "for": "speech_end" },
    { "at": 12, "sequence": "crowd_hype" }
  ]
}
```

- A track item is `{at, cue}`, `{at, clip, intensity?, speed?}`, `{at, sequence}` (nesting depth ≤ 3) or `{at, do: ...}` (any department action).
- `wait` pauses the clock. Every later item slides by the wait's real duration.
- **Clock rules.** The player schedules each item against a **monotonic clock anchored at the start**; it never accumulates sleeps. On `clock: beat` it chases the live tempo when one is known.
- **Interruption.**
  - A new sequence on the same layer ends the current one; its clips blend out, never cut.
  - `show.stop {id|layer|all}` does the same.
  - Safety (freeze) beats everything.

## Idle policy (`show/idle.json`)

```jsonc
{ "after_s": 45,
  "choices": [ {"id": "idle_still", "weight": 0.5}, {"id": "tinker_decks", "weight": 0.2}, {"id": "look_around", "weight": 0.2}, {"id": "glitch_small", "weight": 0.1} ],
  "while_music": [ {"id": "bop", "weight": 0.7}, {"id": "look_around", "weight": 0.3} ] }
```

## Live bus contract (CantinaOS ⇄ sim)

| Topic | Direction | Payload |
|---|---|---|
| `show.perform` | anyone → performer | `{id, params?: {intensity, speed}, source: "jev" \| "claude" \| "timeline" \| "idle" \| "ui" \| "cli", conversation_id?}` |
| `show.stop` | anyone → performer | `{id?, layer?, all?}` |
| `show.started` / `show.ended` | performer → all | `{id, kind, source, run_id, reason?: "done" \| "interrupted" \| "rejected"}` |
| `show.motion` | Python player only (retired in Phase 3) | `{run_id, clip, intensity, speed, start_at (epoch s), layer, owns?}` |
| `stage.lights` | performer → lights | `{cue?, mode?, fade, hold, rig?}` |
| `chest.override` | performer → chest svc (only while CantinaOS owns the board) | `{command, hold}` |
| `show.sfx` | performer → audio | `{id}` |
| `motion.freeze` | anyone → body | `{on: bool}`. This is the "motion stop" (BD-X's Menu button). **on:** stop every show and gesture, stop idle, and hold the current setpoints. **off:** blend back to procedural motion over 0.5 s. The body obeys this above every other layer. |

Every payload is a Pydantic model emitted via `model_dump()`. SimBridge forwards all of them.
A tier violation (for example Jev asking for a `show` item) emits `show.ended` with `reason: "rejected"` and does nothing else.

## Cross-language parity

**Expansion** turns a cue or sequence (with `bpm`, no loops, and waits resolved as 0 s) into a flat, time-sorted list:

```json
[{"t": 0.0, "do": "clip", "id": "arm_throw", "intensity": 1.0, "speed": 1.0}, {"t": 0.05, "do": "sfx", "id": "airhorn"}]
```

- Normalisation:
  - `t` is rounded to 3 decimals;
  - defaults are filled in for `clip` (`intensity` 1, `speed` 1), `lights` (`fade` 0, `hold` 0) and `chest` (`hold` 0), and nowhere else;
  - `wait` is dropped (resolved as 0 s);
  - the fixture's `bpm` stands in for the live tempo and overrides the sequence's own `bpm`;
  - nested cues and sequences are flattened, with their `at` added;
  - ties keep file order.
- `show/tests/fixtures/*.json` are the inputs; `show/tests/golden/<fixture>.json` are the expected expansions.
- The Python and TypeScript test suites must both reproduce the golden files. Compare parsed JSON, so `2` equals `2.0`.
- The golden files are hand-written. If an implementation disagrees, fix the implementation, or raise the disagreement; never regenerate the golden files to match.
