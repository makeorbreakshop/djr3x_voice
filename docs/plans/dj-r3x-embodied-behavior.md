---
title: DJ R3X embodied behaviour - rig, body language, voice-driven motion and shows
status: proposed
artifact_readiness: draft
execution: design
---

*2026-09-28. Design for making R3X's body behave like a conversational character now that it
has a turning head, lift, visor, rings and a hero arm with a wrist. Companions:
[sim/docs/motion-control.md](../../sim/docs/motion-control.md) (actuation pipeline, controller
contract) and [sim/docs/physics-simulation.md](../../sim/docs/physics-simulation.md) (MuJoCo, Show/Lab,
`r3x-show v1`). Figures marked **(measure)** are estimates.*

## TL;DR

- **One body brain, two speeds of voice input, one safety gate.** Add a CantinaOS
  `BodyLanguageService` that composites layered behaviour (alive, attention, turn-taking,
  mood, speech accents, beat-locked dance, episodic gestures) into joint goals. It publishes
  one `motion.targets` stream. A `MotionControllerService` sends that stream through a
  safety governor to the custom controller, and the sim renders the same stream.
- **Jev can drive the body, in a parallel request.** Keep the tool request exactly as
  benchmarked. Send a second, concurrent Jev request with a *body* catalogue: social acts
  (greeting, farewell, praise, tease, excitement) and body requests (dance, wave, look at me,
  freeze). It has its own risk tiers and it pre-arms reactions from partial transcripts. The
  body starts responding ~200 ms after the click, which fills the ~3.5 s gap before R3X
  speaks.
- **Claude adds meaning; ElevenLabs adds timing.** Claude writes a few inline tags
  (`{nod}`, `{point:guest}`, `{mood:excited}`). They are stripped before anyone else sees the
  text. ElevenLabs' `stream/with-timestamps` endpoint gives each character's time, so a
  gesture's stroke lands on its word. The installed SDK (2.68.0) already has
  `text_to_speech.stream_with_timestamps`.
- **The rig has one consequential error.** `build_r3x.py` parents `head_lift` to
  `torso_top`, but the R-3X Animation CAD drives the neck tube (lift rack and neck gear) from
  the **base**, through the ring centres. The rings should not carry the head. Gaze, pointing
  and the "rings follow the head" logic all depend on fixing this first.
- **Three event paths in the system have no emitter.** Nothing emits `LLM_SENTIMENT_ANALYZED`
  (only the dead GPTService did), nothing emits `VISION_ENGAGEMENT_*`, and VisionService
  computes face boxes but discards them. Emotion and gaze-at-guest therefore have no live
  input today. This plan supplies all three.
- **Everything is demoed in the sim first.** The gesture library, the compositor, Jev body
  intents and the markup all run against the sim before any servo moves.

## 1. Rigging review

Sources: `sim/model/build_r3x.py` `JOINTS` (the source of truth; `rig.json` is a gitignored
build output), `servo_map.json`, the CAD screenshots and the Maestro sample show in the
R-3X Animation folder.

### 1.1 What is faithful

- **The 8-channel map matches the Maestro script.** neck 0, headlift 1, headtilt 2, visor 3,
  elbow 4, hand 5, lowarm 6, heroarm 7.
  - "elbow" is the hero-arm disc (rig `hero_shoulder`).
  - "hand" is the wrist *roll* about the forearm axis, not a claw.
- **Ring travel is right.** ±35° and ±30° are set by the measured internal gear sectors
  (~3.8:1, ~4.4:1), as are their slow limits (40°/s).
- **The lift is a prismatic joint on MGN12 rails with a rack and pinion.** Travel is ±20 mm
  (pinion assumed).
- **The visor pivots on the ear axle through a push-rod.** The four-bar is treated as 1:1
  for now; physics-doc §3.2 plans the real linkage.
- **Coupled claws** (three hero fingers on one SG90) are modelled correctly as one channel
  in the `extended` profile.

### 1.2 What is wrong or missing

| # | Finding | Why it matters | Fix |
|---|---|---|---|
| R1 | `head_lift.parent = "torso_top"`. CAD 01/02 show the lift rack, pinion and neck gear in the base bowl, with the tube passing through the ring plates' centre holes. The joint's own note says "60 kg servo in the base". | In the sim, a ring move swings the head. On hardware it will not. Every gaze and pointing solution is wrong by the ring yaw. | Verify on the CAD whether the rail is fixed to the base or to the lower ring, then set `parent=None` (or `torso_lower`) in `JOINTS` and rebuild. |
| R2 | `behavior.ts` gaze computes pan once per saccade in the pan's parent frame, then adds `0.3·pan + 0.12·pan` of ring follow. | With the current parenting, the face overshoots by ~42% until the next saccade (3.5 s at idle). | Solve gaze in the base frame every tick. After R1, ring follow is pure body language and needs no counter-rotation. If R1 goes the other way, subtract ring yaw from pan (vestibulo-ocular style). |
| R3 | Behaviour commands claws, middle ring, throttle and poker arms. The 8-servo build has none of them (the pipeline silently drops them). | The sim's "thinking claw taps" and "speaking claw open" read as expressive and do nothing on the robot. | Make behaviour profile-aware: each clip declares `requires` joints and a fallback. The Show sim should draw unpowered joints visibly frozen in the 8-servo profile. |
| R4 | No head roll. | The classic curiosity "head cock" is unavailable. | Fake it with tilt, a visor raise and eye-LED asymmetry (one jewel dimmed). A roll servo in the tilt yoke is a possible extended-profile addition, lower value than a claw. |
| R5 | The hero arm has effectively two aiming DOF, and one is slow: azimuth comes from `torso_top` (±30°, 40°/s), elevation from the elbow disc (−35…45°). The wrist roll does not change the pointing direction. | "Point at X" needs an analytic 2-DOF solver with a reachable cone. It cannot be a generic IK chain. | Solver: azimuth = top ring (plus lower ring beyond ±26°, since the top ring sits on the lower one), elevation = elbow, wrist roll = flourish. Outside the cone, the head points ("that way!") and the arm makes a token gesture. |
| R6 | The face firmware (`rex_face_v3_clean`) has states, amplitude and flash only: no pupil offset, no blink. | Eyes are the fastest, zero-wear actuator R3X has. `arduino/pupil_test` already proves the jewel works as centre pupil plus 6-LED iris. | Add `Gd` (pupil lean toward ring LED d=0-6, or 9 for centre), `K` (blink) and `Xnn` (expression) to both firmwares and the sim emulator. |
| R7 | There are no named poses, no animation envelope and no duty budget. Also, `rig.json` is gitignored and regenerated, so nothing hand-authored can live there. | Gestures need a safe inner range: hard stop, then soft limit, then *animation* range (motion-control §3). | Put `anim: [lo, hi]`, `dutyBudget` and `gestureVmaxScale` per channel in the tracked `servo_map.json`. Put named poses in a new tracked `motion/poses.json` (§2.4). |

**Where the expressive DOF are.** The 8-servo robot has four:

- a fast head (pan 150°/s, tilt, lift);
- a very fast visor, which is R3X's eyebrow;
- one fast arm DOF plus a wrist spin;
- two slow body rings.

Design around that. Accents go to the visor, lift and wrist. Attention goes to head pan and
tilt. Posture and "addressing a guest" go to the rings. Emotion goes to the eyes and chest.
The hero claw (SG90, extended ch8) is the cheapest upgrade with the most expression per
dollar: grab, snap, pinch, "count on fingers".

## 2. Embodied behaviour architecture

### 2.1 Layers and arbitration

The architecture follows three sources:

- Disney BD-X's split into *perpetual*, *periodic* and *episodic* motion, composed by an
  animation engine;
- Disney's subsumption gaze stack;
- van Breemen's iCat animation channels with merging logic.

| Pri | Layer | Kind | Owns (default) | Sources |
|---|---|---|---|---|
| 0 | **Safety** | override | all | freeze, park, fault, e-stop, heartbeat loss |
| 1 | **Show** | override on declared joints | per show | authored timelines (DJ intro, transitions) |
| 2 | **Episodic gesture** | override or additive per joint, with 150-300 ms weight ramps | per clip | Jev body intents, Claude tags, event triggers |
| 3 | **Periodic / beat** | additive, phase-driven | lift, visor, wrist, rings | music beat grid (DJ groove, headbang) |
| 4 | **Turn-taking and activity** | pose targets | tilt, lift, visor, arm | listening, thinking, speaking, end-of-turn |
| 5 | **Attention / gaze** | target solver | pan, tilt (+ rings for "address") | vision faces, speaker, look-away, saccades |
| 6 | **Mood** | additive offsets and speed and saccade-rate scale | all | Claude `{mood}`, Jev social act, music energy |
| 7 | **Alive** | additive | lift, tilt, visor | breathing, micro-motion ≥2° |

**Compositor.** Each layer writes `(value, weight, mode)` per joint. Override layers blend by
weight in priority order; additive layers are summed on top.

- The result is clamped to the channel's `anim` envelope and scaled by a global `intensity`
  (0-1).
- It is emitted as goals. The existing jerk-limited follower (motion-control §4) handles
  smoothing, so layers never need their own easing to be safe.
- Clips mark `interruptible_after` and an `exit` segment. A higher-priority entry (a new
  listening turn, a freeze) triggers the exit blend, never a cut.

### 2.2 Turn-taking choreography (push-to-talk today)

The loop is click-gated, and a click while R3X speaks is rejected, so there is no barge-in
yet. These timings come from the measured turn: action at ~200 ms, speech at ~3.7 s.

| Moment | Event | Body | Eyes/chest |
|---|---|---|---|
| Click / record start | `VOICE_LISTENING_STARTED` | Orient to the speaker (face bearing, else last speaker). Lean in: lift +6 mm, tilt −4°, visor −8°. Hero arm drops to neutral. | Listening |
| Guest mid-utterance | `TRANSCRIPTION_FINAL` segment, or an interim pause >300 ms | Backchannel: a 4° nod, at most one per 2.5 s, plus a slight visor lift | Pupil centre, brighter |
| Partial matches a greeting or excitement | speculative Jev (§3a) | *Pre-arm* only: visor up 30%, eyes widen. No arm motion. | brighten |
| Click stop | `VOICE_LISTENING_STOPPED` | Acknowledge within the Jev verdict (~200 ms): the reaction gesture, or a small "got it" nod | Thinking |
| +600 ms, no speech yet | timer | Thinking look-away up and aside (15-30°), visor down 6°, wrist idles. A glance back at ~2 s says "still with you". Gaze aversion signals cognitive effort and holds the floor (Andrist 2014). | Thinking dots |
| Speech starts | `SPEECH_GENERATION_STARTED` + alignment | Gaze returns to the guest on the first stressed word. Claude's cues play. Amplitude accents (existing L4) fill the gaps. | Speaking, `Mnnn` |
| Last ~400 ms of speech | alignment end time | End-of-turn cue: gaze locks on the guest, a small nod, visor up (the classic "your turn" signal) | green done sparkle |
| After the turn | idle timer | Decay to engaged idle; saccade rate follows mood | Engaged |

**Future barge-in.** A `VOICE_LISTENING_STARTED` during speech runs the episodic clip's exit
(≤250 ms), then the listening pose. It never freezes mid-gesture.

### 2.3 Gesture library (8-servo first)

Clips are short `r3x-show v1` documents (physics doc §5), the same format as authored shows,
plus a few extras:

- **params:** `amp`, `speed`, `side`, `count`, `target`;
- **timing marks:** `prep_ms` (anticipation), `stroke_ms` (the accent that syncs to a word or
  beat) and `exit_ms`;
- **`requires` joints**;
- **`tier`**, used by the safety governor (§2.5).

| Clip | Joints (8-servo) | Params | Dur. | Tier | Notes |
|---|---|---|---|---|---|
| `nod` | tilt, small lift dip | count, amp | 0.4 s/nod | free | backchannels and yes |
| `shake` | pan ±10-14° | count | 0.3 s/swing | free | no, disbelief |
| `wave` | elbow to +40°, wrist ±45° × count, top ring 8° toward the target | count, side | 1.5-2 s | free | greeting and farewell; the wrist carries the wave |
| `point` | top ring (+lower), elbow; head leads 150 ms | azimuth, elevation, hold | 1-2 s | cheap | solver from R5; out-of-cone fallback |
| `look_at` | gaze layer target, not a clip | target=guest, angle, name | - | free | vision bearings (§3c) |
| `lean_in` | lift +8 mm, tilt −6°, visor −8°, rings 10° toward the target | amp | hold | free | interest, secrets |
| `visor_flip` | visor to −15° (fast), lift +12 mm pop, tilt −6° | amp | 0.4 s | free | surprise, excitement |
| `shrug` | lift −10 then +6 mm, elbow +10°, wrist outward 40°, visor up | amp | 0.9 s | free | "who knows" |
| `proud` / `sheepish` | pose: lift ±, tilt ∓6°, visor ∓, pan away 15° (sheepish) | amp | hold | free | mood poses |
| `glitch` | 300 ms freeze, three visor twitches at ~6 Hz within the jerk limit, 3° pan jitter, then a "reboot" shake | severity | 1.2 s | free | pairs with the persona's `*bzzt*` malfunctions; the eyes flicker |
| `celebrate` | elbow +45°, two wrist spins ±90°, two lift pops | count | 1.5 s | cheap | |
| `dj_scratch` | wrist ±30° at 2× beat, elbow small, head bob | bpm, bars | bars | cheap | beat-locked |
| `headbang` | tilt ±10° on the beat, lift synced, visor on the offbeat | bpm, bars | bars | big | duty-budgeted |
| `beat_drop` | 1-beat hold (lift down, visor down, all still), then on the downbeat: lift pop, visor flip, rings snap ±15° | bpm, downbeat | 2 beats | big | eyes and chest flash |
| `dance` | the Maestro show generalised: lift and visor every beat, wrist every 2, elbow every 4, rings every 8 | bpm, energy | until stopped | big | already in `behavior.ts` as `dj` |

**Extended profile.** With the hero claw added: `snap`, `grab`, `count(n)`, `pinch` ("just a
little"), and a claw clack on the `dj_scratch` stroke.

### 2.4 Emotion and intent mapping

Mood is a valence-arousal state with a named label. It decays to neutral over ~20 s unless
refreshed. It sets offsets and rates, not clips:

| Mood | Lift | Tilt | Visor | Speed / saccades | Eyes | Chest |
|---|---|---|---|---|---|---|
| excited | +8 mm | chin up −4° | up −8° | 1.3× / fast | `HAPPY`, pupils wide | tempo ↑ |
| happy | +4 | −2 | −4 | 1.1× | `HAPPY` | warm chase |
| curious | +6, lean | +3 | −10 | 1.0 / tracking | pupil lean to target | slow |
| confused / glitchy | 0 | tilt wobble | twitch | jitter | flicker, `THINKING` | stutter |
| sheepish | −8 | +8 down | +6 | 0.8× / avert | dim | dim |
| annoyed (playful) | 0 | +4 | +12 scowl | 0.9 | `ANGRY` amber | red windows |
| sad | −10 | +10 | +8 | 0.6 / slow | `SAD` | blue, slow |
| proud | +10 | −6 | −6 | 1.0 | bright | sparkle |

Named poses (`rest`, `park`, `attentive`, `lean_in`, `think_away`, `proud`, `sheepish`,
`dj_ready`) go in `motion/poses.json`. Values are in joint units, validated against the
`anim` envelopes by a unit test.

### 2.5 From behaviour to servos

```
events --> BodyLanguageService (layers + compositor, 50 Hz)
             |  motion.targets {seq, t, joints{name: value}}   (goals, joint units)
             v
          MotionControllerService
             SafetyGovernor: anim envelope, per-tier speed caps, proximity scaling,
                             start staggering, duty budget, freeze/park, heartbeat
             |-> serial: custom controller (goals + v/a/j, motion-control §8), telemetry back
             |-> mock sink (no hardware): fail-open, still emits telemetry
          SimBridgeService forwards motion.targets (25-50 Hz) -> sim feeds pipeline.command()
```

The governor's rules:

- **Proximity scaling.** When the largest face box exceeds a threshold (a guest within
  ~0.6 m **(measure)**), cap ring speed at 50% and block the `big` tier.
- **Start staggering.** Never start more than 3 channels in the same 20 ms frame; stagger
  starts 20-40 ms apart. Physics-doc §8 found the Maestro show starts 5 servos per frame,
  drawing 8-17 A of inrush.
- **Duty budget.** Keep a rolling "reversals × amplitude" budget per channel. The lift and
  tilt on a beat for a whole song are the wear hotspots, so when a channel's budget is spent,
  its beat amplitude halves and the visor and eyes carry the beat.
- **Idle park.** After N minutes of IDLE, park and hold outputs off.

Collision safety comes offline, not at runtime. Every library clip is linted in CI at its
maximum parameters, over a grid of extremal gaze and mood poses:

- limits and envelope;
- velocity, acceleration and jerk;
- current;
- MuJoCo collision once physics P1 lands.

A clip that fails does not ship.

### 2.6 New topics and payloads (Pydantic, emitted via `model_dump()`)

| Topic | Payload |
|---|---|
| `body.cue` | `BodyCuePayload{conversation_id, gesture, params, source: jev\|claude\|event\|show\|rule, tier, at: {mode: now\|speech_char\|beat\|clock, value}, intensity, refractory_key}` |
| `body.gaze.target` | `GazeTargetPayload{kind: guest\|away\|point\|named, yaw_deg, pitch_deg, frame: "base", person, confidence, source}` |
| `body.mood` | `MoodPayload{mood, valence, arousal, source, ttl_s}` |
| `speech.alignment` | `SpeechAlignmentPayload{conversation_id, audio_t0 (monotonic, after device latency), chars, starts_s, ends_s, final}` |
| `llm.response` (extended) | adds `performance_cues: [{char, gesture, arg}]` and `mood` (offsets into the clean text) |
| `music.beat_grid` | `BeatGridPayload{track, bpm, beats_s, downbeats_s, energy_env, position_s, at_monotonic}` |
| `vision.faces` | `VisionFacesPayload{t, faces: [{bbox, yaw_deg, pitch_deg, size, name?}]}` (5 Hz) |
| `motion.targets` / `motion.state` / `motion.telemetry` / `motion.command` | goals; parked\|live\|frozen\|fault; controller echo; CLI `body wave`, `body freeze`, `show play dj_intro` |

**Trap.** The bus is synchronous: never `await emit` (CLAUDE.md §10). Before trusting any new
subscription, grep for its emitter; three of today's body-relevant subscriptions have none.

### 2.7 Where `behavior.ts` should live

- **Runtime authority is CantinaOS.** The robot must not depend on a browser tab.
  `BodyLanguageService` is a Python port of the layer logic.
- **The behaviour is data, shared by both engines.** A tracked, repo-level `motion/`
  directory holds `poses.json`, `moods.json`, `gestures/*.show.json` and
  `jev_body_catalogue`. Both engines read it; the sim imports it via Vite `server.fs.allow`,
  or copies it the way the model is copied.
- **The sim becomes a renderer in live mode.** It plays CantinaOS's `motion.targets` through
  its pipeline, so what you see is what the controller gets. Offline and demo modes keep the
  TS compositor. After the Show/Lab split (physics P0) it lives in `src/core/behavior/`.
- **Parity is tested, as the face firmware is.** Record a CantinaOS event log, replay it
  through both compositors and compare the target streams (RMS < 1°, same clip start frames).
- **Rejected alternative:** running the TS core headless in Node under CantinaOS. It is one
  implementation, but it adds a Node runtime dependency to the robot and splits the process
  model.

## 3. Driving it from the voice loop

### 3a. Jev: fast body intents (~190 ms, or ~0 ms speculative)

**Coexistence rule: do not touch the benchmarked tool request.** Adding questions to it would
move its latency (the code notes 184 ms for 3 questions and 228 ms for 12; it sends 11 today)
and its measured 0-false-trigger result.

- **Fire a second, concurrent Jev request** with `jev_intents_body.build_body_questions()`,
  on the same pre-warmed client. It uses the same `build_state()` (identity line plus
  utterance, no history: the same eagerness lesson applies).
- **Cost:** ~$0.00008 per turn and no added latency on the action path.

The catalogue:

```python
BODY_SOCIAL = choice("What social act is the speaker performing toward the droid?", {
  "greeting": "Saying hello or hailing the droid now.",
  "farewell": "Saying goodbye or leaving now.",
  "praise": "Complimenting the droid, its music or its moves.",
  "thanks": "Thanking the droid.",
  "tease": "Playfully insulting or teasing the droid.",
  "excitement": "An excited exclamation (this is my jam, let's go, woo).",
  "question_to_droid": "Asking the droid something about itself or the world.",
  "none": "None of these; an instruction, or talk about something else."})
BODY_REQUEST = choice("Which movement is the speaker telling the droid to make right now?", {
  "dance": "...", "wave": "...", "look_at_me": "Turn toward, look at or face the speaker.",
  "bow": "...", "point": "Point or show which way something is.", "none": "No movement requested now."})
NOULS = {
  "body_dance": noul("Is the speaker telling the droid to dance or move to the music right now?",
      true="dance, show your moves, bust a move, drop the beat and move",
      false="asks WHETHER it can dance, talks about dancing, praises past dancing, or only asks for music"),
  "body_wave": ..., "body_look_at_me": ...,
  "body_freeze": noul("Is the speaker telling the droid to stop moving or hold still?",
      true="freeze, stop moving, hold still, calm down your moving",
      false="asks to stop the music or stop talking"),
  "expects_yes_no": noul("Is the speaker asking the droid a yes-or-no question?", ...),
  "is_a_command": IS_A_COMMAND_NOUL,   # re-asked here; a question costs tokens, not latency
}
```

The decision reuses the existing `decide()` shape. Its tiers are inverted where the
asymmetry is inverted:

| Body tier | Fires | Gate | Why |
|---|---|---|---|
| **ambient** (speculative partials only) | pre-arm: visor lift, eyes widen, orient to the speaker; clip preloaded | social ≥ 0.75 on a partial | invisible if wrong; never an arm or ring move |
| **free** reaction | greeting→`wave`(small), farewell→`wave`, praise→`proud`, thanks→`nod`+bow, tease→`sheepish`/scowl, excitement→`visor_flip` | social choice ≥ 0.80. No command gate: a social act is not an instruction. | small, reversible, character-building |
| **free** request | `wave`, `look_at_me`, `bow` | request ≥ 0.75, own noul ≥ 0.5, is_a_command ≥ 0.5 | same as the eye-animation tier |
| **big** request | `dance`, `point`, `headbang` | request ≥ 0.85, own noul ≥ 0.7, is_a_command ≥ 0.5, and not blocked by governor proximity | a full-body move at a question is the body's "music blasting" failure |
| **safety** | `freeze` | `body_freeze` noul ≥ 0.6 alone | a false freeze costs nothing; a missed one can hurt |

**Never on questions about gestures.** *"Can you dance?"*, *"did you wave at me?"* and
*"my kid loves when you dance"* fail `is_a_command` and the noul's false criterion. Claude then
answers, and may choose `{dance}` itself ("Can I?! Watch this!").

**`expects_yes_no` arms the head.** It pre-arms `nod` or `shake`, and a regex on the first
clause of Claude's reply picks the polarity ("Absolutely", "Nope", "I have a bad
feeling...").

**Double-acting is handled in two ways:**

1. **Body verdicts never touch `GATE.ActionTaken`,** so Claude's tools stay exactly as today.
   A body verdict goes on `GATE.resolve_body()`. ClaudeService's existing bounded wait
   (1.2 s) also collects it, since the two requests run in parallel. When a request-type
   gesture fired, Claude gets `<body_already_doing>dance</body_already_doing>`, so its line
   can match.
2. **`BodyLanguageService` applies a per-family refractory (3 s),** so a Claude `{wave}`
   after a Jev wave is dropped.

**Existing tool intents already trigger the body, with no new Jev questions:**

- `play_music` → `beat_drop` on `MUSIC_PLAYBACK_STARTED`;
- `dj_mode_on` → the DJ intro show;
- `set_eye_animation` → a visor flick;
- `stop_music` → a settle to engaged.

**Eval before enabling.** Build ~80 labelled utterances in the router benchmark's style. Half
should be adversarial conversation about moving. Run 5 passes. **Ship gate:** 0 big-tier false
triggers and ≤1 free-tier false trigger per 100 conversation utterances. Log the full
probability map on every decline, as the tool router does.

### 3b. Claude: inline performance markup

**Syntax: curly-brace tags**, at most 2 per reply, placed immediately before the word they
accompany, plus one optional leading `{mood:x}`. Examples: `{nod}`, `{shake}`, `{wave}`,
`{shrug}`, `{point:left|right|guest}`, `{look:guest|away|up}`, `{visor_flip}`, `{lean_in}`,
`{proud}`, `{sheepish}`, `{dance}`, `{beat_drop}`, `{glitch}`, `{mood:excited}`.

Why this syntax:

- **Not brackets.** ElevenLabs v3 interprets `[laughs]`-style audio tags, and the service
  already supports `eleven_v3`.
- **Not a tool call.** Tools are suppressed (`tool_choice: none`) exactly when Jev acted.
  Tool calls are also only extracted after `get_final_message()`. Inline tags stream with the
  text and work under `tool_choice: none`.

**Parsing, in `ClaudeService` at emission:**

- Hold back an unclosed `{` across stream chunks.
- Strip the tags, and emit `LLM_RESPONSE` with the clean text, `performance_cues` (character
  offsets into the clean text) and `mood`.
- Unknown names are dropped and extras past two are ignored. The CLI, memory, dashboard and
  TTS all see clean text.
- **Keep the tagged text in `SessionMemory`,** so Claude keeps seeing its own convention and
  does not drift out of it. The 250-character spoken limit is measured on the clean text.
- `mood` is re-emitted as `LLM_SENTIMENT_ANALYZED`, which revives the eye controller's dead
  sentiment path, and as `body.mood`.

**Timing:**

- **ElevenLabs** buffers the complete reply before synthesis (`_wait_for_complete_response`),
  so every cue is known before audio starts.
- **Switch the worker** from `text_to_speech.stream(...)` to `stream_with_timestamps(...)`.
  It returns JSON chunks with `audio_base64` and character start and end times. Emit
  `speech.alignment` with `audio_t0` = the first `stream.write` plus the device output latency.
- **Schedule each clip** so that `stroke_ms` lands on its word:
  `start = audio_t0 + t(char) − stroke_ms − servo_lead`. The servo lead is ~200 ms of follower
  lag plus frame latency **(measure)**.
- **Cached DJ speech** (`CachedSpeechService`) stores the alignment next to the audio, so
  cues work for pre-rendered commentary too.
- **Fallback when alignment is missing:** estimate the time as proportional to the character
  position over the estimated duration, then snap to the nearest amplitude accent (the
  existing L4 detector).
- **Spikes to run first:** confirm that `pcm_24000` works on the with-timestamps stream, and
  measure the first-byte delta against plain `stream`.

**Rule layer (BEAT-style, no LLM).** On the alignment, add cheap co-speech beats:

- "you" → orient to the guest;
- "I" or "me" → elbow in;
- "over there" or "that way" → `point`;
- a closing "?" → tilt and visor up on the final word;
- "no" or "not" → a micro-shake;
- list items → wrist beats.

Claude's tags win on conflict.

**Persona change.** Add a `<body_language>` block of ~300 tokens to the cached system prompt:
the tag list, "0-2 per reply, only when it adds meaning", and three examples. **Cost:**

- a cache read at 0.1× on input;
- +3-10 output tokens per reply (~50 ms of generation);
- no new round trip;
- no latency on the action path.

### 3c. Continuous signals

- **TTS amplitude** (exists). Keep the ~3 Hz bob and accent detector.
  `SPEECH_SYNTHESIS_AMPLITUDE` is emitted when a chunk is *written*, ahead of the sound by
  the device buffer. Align it using the same `audio_t0` correction.
- **Music beat** (new).
  - **Offline beat grid.** On library load, run `librosa.beat.beat_track(units='time')` per
    local file. librosa is already a dependency for CLAP. Also compute the onset-strength and
    RMS envelopes to find drops and energy sections, and cache them next to the file as
    `.beats.json`.
  - **Phase.** At playback, emit `music.beat_grid` and re-sync the phase to VLC's `get_time()`
    once a second.
  - **Knock-on wins.** The chest service already reads `track.bpm`, so its beat chase becomes
    real for free. `beat_drop` fires on a detected downbeat or energy jump.
  - **Spotify has no beat source.** Spotify's audio-features and audio-analysis endpoints are
    closed to new apps (Nov 2024), so Spotify tracks fall back to the chest's default 120 BPM
    and a free-running groove, never "locked" moves.
  - **Latency.** Advance servo commands by the servo lead and the VLC output latency
    **(measure)**.
- **Vision → gaze** (new).
  - **Emit `vision.faces`.** VisionService already runs HOG face detection at 5 fps and throws
    away `face_locations`. Emit them with bearings: yaw = atan((cx − W/2)/f), from the camera
    FOV.
  - **Mount a fixed camera** in the booth or base, not on the moving head, to avoid ego-motion.
    Record the camera-to-base extrinsics in config.
  - **Attention engine** after Disney's gaze paper. Keep a per-face curiosity score with
    habituation, and move between read, glance, engage and acknowledge states. The name from
    face recognition feeds `look_at(name)`.
  - **Implement the unemitted `VISION_ENGAGEMENT_*` events** from face size and dwell time.
- **Mode and machine status.**
  - IDLE is calm: low saccade rate and park after a timeout.
  - INTERACTIVE is engaged.
  - DJ mode is the periodic layer.
  - A service fault (the chest's `X3` path) plays `glitch` once, then a subdued pose until
    recovery.
  - Controller faults or heartbeat loss go to the safety layer.

## 4. Show animations

Authored shows and procedural behaviour are **one format with two authors**:

- **Format.** A show is a longer `r3x-show v1`, with joint, gaze and IK tracks, face and chest
  serial tracks, audio cues, and a `bpm` and audio reference. It compiles to controller goals
  (physics doc §5). Gestures are shows with parameters.
- **Playback.** The TimelineExecutor gains two step types:
  - `perform{clip, params, at}`;
  - `show{id, sync: audio|beat|clock}`.

  A DJ transition plan becomes: duck → `perform(lean_in)` → cached speech (with cues) →
  crossfade → `perform(beat_drop, at=downbeat)` → unduck.
- **Layering.** Shows run on the Show layer and own only the joints they declare. Gaze and
  alive layers keep running underneath, which is what stops a canned show looking canned.
  Show tracks can be marked `additive`, so a head bob adds to live gaze instead of
  overwriting it.

The first shows to author:

1. `dj_intro`: the visor-flip reveal, a ring sweep, "time to boogie";
2. `transition_hype`: the talk-over-the-drop move;
3. `idle_loops`: three 20-40 s loops (tinker, listen to the music, look around), picked
   randomly with habituation;
4. `crowd_reactions`: applause, a boo, "a new face", "someone's dancing" (from vision, later);
5. `malfunction`: the signature breakdown, synced to a cached `*bzzt*` line.

**Authoring.** Use the Lab timeline (physics P3): waveform, beat grid, scrubbing through the
real pipeline, recording live behaviour then simplifying it to keyframes. Until P3 exists,
author clips as hand-written JSON (the library is small) or import Maestro-style steps. The
existing Maestro importer is the bridge, and Bottango-style media tracks are the reference
for audio sync.

## 5. Prior art, and what transfers to 8-17 hobby servos

| Source | What they do | Take for R3X |
|---|---|---|
| Disney Research, *Realistic and Interactive Robot Gaze* (IROS 2020) | Subsumption layers (breathing, blinking, saccades), an attention engine with habituation, read/glance/engage/acknowledge states | The layer stack and the attention states in §3c. Blinks go to the eye LEDs. |
| Disney BD-X (2025) | An animation engine composing perpetual, periodic (phase-driven) and episodic motion, plus show functions (eyes, antennas, audio) | The perpetual/periodic/episodic split maps onto alive, beat and gesture. The music beat is the phase signal. |
| Engineered Arts Ameca / Tritium | An LLM dialogue loop (GPT-4o, Whisper, neural TTS) with automatic lip-sync and a library of authored poses and animations | Keep authored clips; let the LLM *select*, never generate joint values. |
| Furhat / FurChat | LLM responses carry gesture tags from a fixed set; there is a remote API for gaze; users ask for clearer turn-boundary cues | Tag markup from a closed vocabulary, and an explicit end-of-turn cue. |
| GenEM (HRI 2024) | Few-shot LLM → parametrised robot API code for expressive behaviour | Worth it offline for *authoring* new clip variants, not for runtime generation on a voice turn's latency budget. |
| BEAT (Cassell 2001) | Rule-based nonverbal behaviour from linguistic analysis of text | The §3b rule layer on alignment timestamps. |
| Andrist et al. 2014; Skantze 2021 | Gaze aversion signals cognitive effort and holds the floor; gaze is a strong turn-taking cue | The thinking look-away and the end-of-turn gaze lock. |
| van Breemen, iCat | Animation channels, merging logic and a transition filter | The per-joint compositor with weights. |
| Sony aibo | Needs, emotion, mood and personality layers select behaviour autonomously | A light mood state with decay. A full needs model is overkill for a DJ booth. |

**What does not transfer:**

- Learned motion policies (BD-X uses RL for walking).
- Viseme lip-sync; R3X's mouth is a light.
- Continuous speech-to-gesture neural models, which are trained on human arms. R3X has one
  arm DOF.

The hobby-scale lesson is consistent across these sources: a small authored clip library,
selected by language and timed by speech, over procedural alive and gaze layers.

## 6. Phased plan

| Phase | Deliverables | Where | Effort | Demo |
|---|---|---|---|---|
| **B0: rig fixes** | Verify and re-parent `head_lift` (R1); world-frame gaze (R2); profile-aware behaviour and frozen unpowered joints (R3); `anim` and duty fields in `servo_map.json` | `sim/model/build_r3x.py`, `behavior.ts`, `servo_map.json` | 0.5-1 day | sim |
| **B1: gesture library in the sim** | `motion/` data dir; the 15 clips in §2.3; compositor with weights and exits; mood offsets; dev buttons and `__r3x.perform()`; clip lint tests (limits, v/a/j, envelope) | `motion/`, `sim/web/src/behavior*` | 3-5 days | sim, offline |
| **B2: BodyLanguageService** | Python compositor reading `motion/`; event triggers (listening, thinking, speaking, intents, music, DJ); `motion.targets` via SimBridge; sim live-body mode; parity test | `cantina_os/services/body_language_service.py`, `sim_bridge_service.py` | 3-4 days | sim, live CantinaOS |
| **B3: Jev body intents** | parallel body request, catalogue and tiers, speculative pre-arm, `GATE.resolve_body`, refractory, CLI `body …`, the 80-utterance eval | `llm/jev_intents_body.py`, `jev_intent_service.py`, `fast_router_gate.py` | 2-3 days + eval | sim, live |
| **B4: Claude markup + alignment** | persona block; streaming tag parser; cues on `LLM_RESPONSE`; `stream_with_timestamps`; `speech.alignment`; cue scheduler; rule layer; mood revives sentiment | `claude_service.py`, `elevenlabs_service.py`, `cached_speech_service.py`, persona | 2-3 days | sim, live |
| **B5: continuous signals** | beat-grid analyser and cache, VLC phase sync; `vision.faces` + attention engine + engagement events; face firmware `G`/`K`/`X` (and emulator) | music controller, vision, `arduino/rex_face_*`, `firmware.ts` | 3-4 days | sim; eyes on hardware |
| **B6: shows** | `perform`/`show` timeline steps; 5 authored shows; DJ transition plans with motion | timeline executor, brain service, `motion/shows/` | 1-2 weeks (overlaps physics P3) | sim |
| **B7: hardware** | `MotionControllerService` with the safety governor, goals protocol, heartbeat and telemetry; controller firmware; bring-up at intensity 0.3, then ramp; duty and current logging | controller repo, CantinaOS | 1-2 weeks + bench | robot |

**Order:** B0 → B1 → B2 → B3 (Jev is the biggest perceived-latency win) → B4 → B5 → B6 → B7.
Physics P1 (the offline MuJoCo validator) should land before B7, so that every clip is
collision- and current-linted before it touches metal.

## 7. Risks

| Risk | Mitigation |
|---|---|
| **False triggers move hardware** at a guest who was asking a question | Parallel request (the tool path is unchanged); a command gate plus a noul on every request; big-tier gates; proximity blocks the big tier; refractory; a global `intensity` knob; eval ship gate §3a |
| **Servo wear:** lift and tilt beat bouncing, hunting on micro-motion | Duty budgets move accents to the visor and eyes; micro-motion ≥2° but at ≤0.3 Hz; idle park; wear logging per channel; the 60 kg lift is the item to watch |
| **Latency:** gestures late for their words | Alignment timestamps plus `stroke_ms` and the servo lead; Jev reaction at ~200 ms (0 ms on a speculative hit); measure the lead on the bench (motion-control §7) |
| **Safety near people:** ring gear sectors and the arm swing are pinch points | Speed caps, proximity scaling, the freeze intent (eager tier), a physical e-stop, heartbeat → hold, park on boot and shutdown, staggered power-up |
| **Sim and hardware diverge:** assumed calibration, re-parented head | B0 first; calibrate `centerUs`, invert and gearing on the bench; replay hardware frame logs through the sim plant |
| **Two implementations drift:** Python and TS compositors | Shared data dir, a thin procedural core, and the parity test in CI |
| **Markup misuse:** Claude over-tags, or tags leak into speech | Cap of 2 per reply, whitelist parser, strip before TTS and CLI, an eval of 50 replies |
| **Silent subscriptions:** new topics with no emitter | Grep for the emitter before trusting a subscription; add a startup self-check that warns on body topics nothing emits |

## Sources

- ElevenLabs, stream speech with timing: https://elevenlabs.io/docs/api-reference/text-to-speech/stream-with-timestamps
- ElevenLabs, TTS endpoints with timestamps: https://elevenlabs.io/blog/new-text-to-speech-endpoints-with-timestamps
- Disney Research, Realistic and Interactive Robot Gaze (IROS 2020): https://la.disneyresearch.com/wp-content/uploads/root.pdf ; https://la.disneyresearch.com/publication/realistic-and-interactive-robot-gaze/
- Disney Research, Design and Control of a Bipedal Robotic Character (BD-X): https://la.disneyresearch.com/wp-content/uploads/BD_X_paper.pdf ; https://arxiv.org/abs/2501.05204
- Engineered Arts, Ameca: https://engineeredarts.com/robots/ameca
- LLM-enabled interaction with Ameca (Tritium + GPT-4o + Whisper): https://www.researchsquare.com/article/rs-9212133/v1
- Furhat Robotics: https://www.furhatrobotics.com/furhat-robot ; FurChat: https://arxiv.org/pdf/2308.15214
- Fast multi-party open-ended conversation with a social robot (turn-cue feedback): https://arxiv.org/html/2503.15496
- GenEM, Generative Expressive Robot Behaviors using LLMs (HRI 2024): https://arxiv.org/abs/2401.14673
- Simultaneous text and gesture generation for social robots with small LMs: https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2025.1581024/full
- Cassell et al., BEAT (SIGGRAPH 2001): https://dl.acm.org/doi/10.1145/383259.383315
- Andrist, Tan, Gleicher, Mutlu, Conversational Gaze Aversion for Humanlike Robots (HRI 2014): https://pages.cs.wisc.edu/~bilge/pubs/2014/HRI14-Andrist.pdf
- Skantze, Turn-taking in Conversational Systems and HRI: A Review (2021): https://www.semanticscholar.org/paper/Turn-taking-in-Conversational-Systems-and-A-Review-Skantze/697589187eeb8e61de7bd39a5d5005e20c4d7b89
- van Breemen, Animation engine for believable interactive user-interface robots: https://ieeexplore.ieee.org/document/1389845/
- Sony aibo, desires and emotions: https://helpguide.sony.net/aibo/ers1000/v1/en-us/contents/TP0001970094.html ; Personality model for a companion AIBO: https://dl.acm.org/doi/10.1145/1178477.1178575
- Takayama et al., Expressing thought: animation principles for robots (HRI 2011): https://dl.acm.org/doi/10.1145/1957656.1957674
- librosa `beat_track`: http://librosa.org/doc/0.11.0/generated/librosa.beat.beat_track.html
- Spotify cuts audio-features/analysis for new apps (Nov 2024): https://techcrunch.com/2024/11/27/spotify-cuts-developer-access-to-several-of-its-recommendation-features/
- Bottango media tracks (audio-synced keyframing): https://docs.bottango.com/learn-bottango/animating/media-tracks/
