# R3X Platform Architecture — All-Rust Runtime

Status: plan, 2026-09-29, **rev 3** (rev 2 + a full audit against the code on `r3x-sim-2`).
Written for the implementing agent. Every factual claim about the current system below was
checked against code, not against CLAUDE.md. Where the two disagree, §11 lists the corrections.

Rev 3 changes, in one place:
- Parity harness redesigned around recorded fixtures (the old design could not work).
- Bridge mode uses a new full-bus tap, not SimBridge (which forwards only a topic subset).
- LED firmware keeps on-board animation; pixel frames are sim-only for now.
- Servo goals are sent at event time, per `sim/docs/motion-control.md` §8; 50 Hz is for frames.
- Layer order matches the compositor that already exists.
- Remote voice pulled forward to Phase 2 — it is the stated blocker.
- Every running service has a destination (§6). §7 splits behaviours into preserve / fix / drop.
- One LLM model everywhere (`CLAUDE_MODEL`); no Haiku-specific paths.
- Workspace lives in `rust/` (`r3x` at the repo root is the launcher script).

## 0. What we are building (Brandon's intent)

- One character, DJ R3X, that runs in four places: **locally** on a Mac, **in a browser**
  (including a panel in the Lobot dashboard), **on a public server** anyone can play with, and
  **embedded in the physical robot**.
- Operating modes: **Show** (full autonomy), **Bench** (all automatic behaviour off, test one
  output at a time), **Studio** (author animations: keyframes, animate-to-audio; record-a-take
  later).
- Puppeteering is **per-channel override** (Disney style): touching the head gives you the head,
  everything else stays alive underneath, release eases back.
- In a Lobot panel R3X must at minimum **talk both ways**; full character if possible.
- **Data-driven robot profiles now.** R3X is the first profile.
- Hardware: hobby servos on a custom controller first (`sim/docs/motion-control.md` §8); smart
  servos (Feetech/Dynamixel) must be addable later.
- Replay: not needed now; good structured logs are.
- **Language: everything that runs is Rust**, except the browser UI (TypeScript + three.js,
  types generated from Rust) and the Arduino LED firmware (C++, **unchanged in this plan**).
  New servo-controller firmware may be Rust (Embassy). Python survives only as offline scripts
  (model export, evals, fixture recording) — never in the runtime.
- **LLM: one model, set by `CLAUDE_MODEL`** (live default `claude-sonnet-5`, `main.py:214`),
  used for conversation, memory summaries and scene description alike. No Haiku defaults.

## 1. Diagnosis of the current system (verified)

| Claim | Evidence |
|---|---|
| Three kinds of traffic share one untyped bus | Panel sends `cli {text}` through SimBridge (`panel.ts:522`, `sim_bridge_service.py:518-529`) |
| Two conductors for the body | `show_player.py` (660 lines) and `sim/web/src/show/*`; parity is 2 golden files, expansion only |
| No retained state, no acks | SimBridge reads `_brain._dj_mode_active` (`sim_bridge_service.py:282`) and keeps a shadow copy |
| Soft contracts | 166 of 341 emit sites are dict literals; 3 payload modules; `ServiceStatus` is `"RUNNING"` in one and `"running"` in another; 63 of 193 topics never referenced |
| Unauthenticated side door | `serve()` with no token or origin check; `say` injects text into the voice pipeline; `cli show <id>` gets show-tier permission |
| Audio welded to the host | PyAudio mic, sounddevice TTS, VLC `--aout auhal`, no device selection, no mixer |
| Mode is scattered | 7 services keep their own system-mode copy; DJ-active lives in 5 places |

Size of what is being replaced: **43,546 lines** of non-test Python, **24 started services**,
636 passing tests. The current system is the **behavioural reference**, filtered through §7.

## 2. Target architecture

```
 ┌──────────────────────────── r3x runtime (one Rust binary, tokio) ──────────────────────────┐
 │  voice ── Deepgram WS ──► intent (Jev fast router ∥ Claude) ──► tools ──► dj / music        │
 │    ▲                         │ speech text + tags                         │                 │
 │  audio engine: mic · TTS playback (ElevenLabs dialogue WS) · music decode+mix · ducking ·   │
 │                crossfade · sfx · amplitude + char timings · local or remote sinks           │
 │  memory (SQLite)   vision   music search (ort)   stage manager   health   latency           │
 │  ═════════ typed bus: broadcast events · watch state · mpsc commands+reply ═════════════════ │
 │  performer (r3x-performer-core) ──► drivers: virtual · servo controller · pca9685 ·         │
 │                                     maestro · face/chest LED serial (named commands)        │
 │  gateway (axum WS): protocol v1, auth, source stamping, tiers, retained state, audio        │
 └───────────────────────────────────────────┬─────────────────────────────────────────────────┘
         control panel · Lobot panel · public site · Studio · `r3x-cli` (all gateway clients)
         └── browser pages can embed r3x-performer-core as WASM for standalone life
```

### Deployment profiles (same crates, cargo features)
| Target | What runs |
|---|---|
| Mac (today) | full binary; USB Arduinos |
| Robot | full binary on the onboard computer (default assumption: tethered Mac until Q1 is answered) |
| Browser standalone | WASM performer only |
| Browser connected | gateway client; voice via streamed audio |
| Public server | `--headless --public`: per-session brains, no local audio/drivers, budget caps |

### Decisions
- **D1 — One Rust workspace (`rust/`), one runtime binary.** Services are tokio tasks.
- **D2 — Typed bus.** `enum Event` / `enum Command` in `r3x-contracts`.
  - Events: bounded `broadcast` per domain; a lagging receiver logs and resyncs from state.
  - Retained state: `watch` per domain (stage, conversation, music, dj, perf, lights, services).
  - Commands: `mpsc` + `oneshot` → `Ack{accepted | rejected(reason)}`.
  - Every message: `seq`, `t_mono`, `t_wall`, `source`, `conversation_id?`.
- **D3 — Message classes are types**: `Intent*`, `Perf*`, `Stage*`, `Telemetry*`. The gateway
  maps each authenticated client to an allowed set. `source` is stamped, never self-declared.
- **D4 — The performer is the only conductor** for motion, LED *state*, stage lights and sfx
  triggers. `r3x-performer-core` has no I/O, takes a seeded RNG, and compiles to WASM. One
  performer is authoritative per robot session; connected browsers are followers.
- **D5 — LEDs: animation stays on the board.** The host sends the existing named commands
  (`SI SE SL ST SS SF`, `Mnnn`, `Bnnn`, `Xn`, `Hxxx`). The firmware is not modified.
  - For the sim, `r3x-performer-core` contains a Rust port of the firmware emulators
    (`firmware.ts`, `chest.ts` logic) and produces pixel frames from the same command stream —
    the fidelity guarantee that exists today.
  - Why not host-rendered frames: `FastLED.show()` disables interrupts ~0.7–1 ms on AVR while
    bytes arrive every 87 µs, the chest `rxBuf` is 8 bytes, and on-board animation is what keeps
    the face alive when the host stalls. Revisit only with RP2040-class LED boards and a binary
    framed protocol.
  - New expressions (`dj-r3x-embodied-behavior.md` R6: `Gd`, `K`, `Xnn`) are added as named
    commands to firmware + emulator together. That plan's bus topics are superseded by
    `r3x-contracts`; its behaviour design stands.
- **D6 — Servos: firmware does the last mile** (motion-control.md §8, unchanged).
  - Host → controller: **goals at event time** `{joint, target, [vMax,aMax,jMax]}` with `seq`
    and a heartbeat. Not a 50 Hz stream — streaming lets host jitter reach the joints.
  - Controller: ≥200 Hz two-stage jerk-limited follower, soft-limit braking, 1 µs pulses,
    park pose + staggered power-up, heartbeat lost >250 ms → ramp to hold, telemetry 20–50 Hz.
  - MCU: RP2040 or ESP32 (the doc names both). Default RP2040.
  - Stall detect needs current sensing. One INA219 on the rail detects *a* stall, not *which*
    channel; per-channel identification is deferred to feedback servos.
  - Dumb sinks (`pca9685`, `maestro`) have no follower, so for those the **host** runs the
    follower and streams pulses at 50 Hz. Same trajectory module, different home.
  - **50 Hz `frames`** exist for the sim, virtual driver and logs only.
- **D7 — Own the audio engine** (cpal + symphonia + mixer) with pluggable sinks. Spotify stays
  a *control-only* backend: its audio never passes through the host, so ducking is a volume
  command and crossfades/beat alignment do not apply to it. Off by default, ported last.
- **D8 — Robot Profile is the root config** (§3a).
- **D9 — Contracts live in Rust; TS types and JSON Schema are generated.**
- **D10 — Models run through `ort`.** Face detect/embeddings and CLAP music search are exported
  to ONNX by offline Python scripts, with the audio front end (mel features) either inside the
  exported graph or reimplemented in Rust and checked against recorded Python outputs.

## 3. Contracts

### 3a. Robot Profile (`profiles/<name>/robot.json`, schema generated from Rust)
- `joints`: name, type, unit, hard/soft/animation ranges (motion-control.md §3), vMax/aMax/jMax,
  parent, `extended`.
- `actuators`: joint → driver + channel + calibration (centre, direction, gear, pulse range,
  `calibrated: assumed|measured`). Drivers: `virtual`, `r3x_servo`, `pca9685`, `maestro`,
  `feetech_sts` (stub), `dynamixel` (stub).
- `lights`: groups (eyes 14 px, mouth 8 px, chest 33 px, stage) with layout, driver, port hint,
  protocol (`named_v1`).
- `audio`: outputs and ducking policy. `emotes`: slot → cue. `alive`: procedural layers.
- Seed from `sim/web/src/actuation/servo_map.json` and `sim/web/src/show/rig_limits.json`.
  **Not** from `rig.json` — it is gitignored and build-generated; `rig_limits.json` is its
  committed copy. Chest pixel layout is copied out of a built `rig.json` once, by hand.

### 3b. Gateway protocol v1 (WebSocket)
Envelope `{v:1, kind, seq, t_mono, t_wall, source, id?, re?, body}`;
`kind: hello | state | event | command | ack | result | frames | audio | log`.
- `hello` carries full retained state; `state` deltas follow.
- Tiers: `jev ≤ cheap`, `claude/timeline/ui/cli ≤ show`, `idle = free`,
  `public ≤ cheap` + `intent.say` only.
- **Token auth and an `Origin` allow-list on every bind, loopback included.** A web page can
  reach localhost; loopback is not a trust boundary.
- `frames` `{t_mono, joints{}, lights{}}` at 50 Hz (sim/virtual/log).
- `audio`: binary. Client→runtime 16 kHz PCM between `ptt.start/stop`, 20 ms chunks;
  runtime→client 24 kHz PCM TTS + character timings.
- Push-to-talk ownership is retained state (`state.conversation.ptt_owner`), replacing the
  "panel announces itself so the mouse service yields" handshake.

### 3c. Performance model
Per channel, evaluated each tick. **Order is the existing `BodyCompositor`'s**
(`sim/web/src/show/body.ts`):

`procedural (alive) → background loop → gesture → show → puppet → freeze`
then, downstream and unbypassable, **actuation**: soft-limit clamp → jerk-limited follower →
brake `v ≤ √(2·a·d)` → output.

- Tracks are `override(value, weight)` or `additive(offset)`; blend times by joint class.
- A sequence may **own** joints, which holds them against lower layers.
- Puppet = per-channel additive/override, release fade (default 400 ms min-jerk). The 8 intents
  + 8 cue slots and gamepad mapping in `puppeteer.ts` are ported as-is.
- Each procedural layer (breathing, saccades, gaze wander, speech bob) individually switchable.
- `freeze` stops every run, refuses new ones, releases over 0.5 s; the chest holds its state
  and re-sends every channel on release.
- Start-staggering rule from `dj-r3x-embodied-behavior.md` applies in the gesture layer.
- Output enables gate drivers, not layers.

## 4. Operating modes (StageManager owns them)

| | Show | Bench | Studio |
|---|---|---|---|
| Brain (voice/LLM) | on | off (toggle) | off |
| Alive layers | on | off, each toggleable | off (preview toggle) |
| Idle policy / DJ autonomy | on | off | off |
| Outputs | all per profile | all off; enable one at a time | virtual; "send to robot" toggle |
| Puppet | blends | direct jog per channel | record source |

Separate retained states, never conflated: `state.stage` (operating mode),
`state.conversation` (idle/listening/thinking/speaking), `state.dj` (active, current, next),
`state.engagement` (today's STARTUP/IDLE/AMBIENT/INTERACTIVE, which gate the STT socket,
ducking and the mouse trigger).

## 5. Workspace layout

```
rust/                     cargo workspace
  crates/
    r3x-contracts         Event/Command/State/Profile types; ts-rs + JSON Schema export
    r3x-bus               broadcast/watch/mpsc wiring, seq/clock stamping, JSONL session log
    r3x-performer-core    show loader/validate/expand, clip eval, compositor, procedural,
                          trajectory (no_std-friendly), player, idle, LED firmware emulators
    r3x-performer-wasm    wasm-bindgen wrapper
    r3x-drivers           servo controller, pca9685, maestro, face/chest LED serial, virtual
    r3x-audio             capture/playback, decode, mixer, ducking, crossfade, sfx, sinks
    r3x-beats             tempo + beat grid, disk cache
    r3x-music             library scan, track matching, semantic search (ort CLAP), Spotify
    r3x-voice             Deepgram streaming STT, ElevenLabs dialogue-socket TTS
    r3x-llm               Anthropic Messages client, provider resolution
    r3x-intent            Jev client + gate + Claude dedup rendezvous
    r3x-brain             turns, tool dispatch, show-tag parser/scheduler, DJ planner,
                          plan executor (layers), commentary cache
    r3x-memory            SQLite: people, visits, timeline, DJ history, operational state
    r3x-vision            camera, ort face detect + embeddings, scene via Claude
    r3x-ops               health aggregation, latency tracker, debug/trace controls
    r3x-gateway           axum WS server, auth, tiers, audio streaming
    r3x-cli               terminal client of the gateway (commands, shortcuts, history)
    r3x-runtime           the binary; feature flags per deployment profile
sim/web                   renderer + panel + Studio (TS), generated types + WASM
firmware/servo            servo controller
```
The root `./r3x` launcher script stays and is repointed at the new binary in Phase 7.

## 6. Where every current service goes

| Current service | Destination | Phase |
|---|---|---|
| yoda_mode_manager, mode_command_handler | StageManager (`state.engagement`) | 1 |
| command_dispatcher, cli | typed `Command`s; `r3x-cli` keeps the command set and shortcuts | 1 |
| sim_bridge | `r3x-gateway` | 1 |
| textual_dashboard | **dropped**; the panel replaces it | 1 |
| debug | `r3x-ops` + the JSONL log | 1 |
| deepgram_direct_mic | `r3x-voice` | 2 |
| mouse_input | `r3x-voice` input source (global click; needs macOS Accessibility) | 2 |
| elevenlabs | `r3x-voice` + `r3x-audio` | 2 |
| timeline show player | `r3x-performer-core` | 3 |
| eye_light_controller, chest_light_controller | `r3x-drivers` (protocol, port probe, identity check) | 3 |
| chest status display (boot sweep, health mask, fault latch) | `r3x-ops` health → performer chest channel | 3 |
| music_controller (+ beat analysis, semantic search, Spotify) | `r3x-audio`, `r3x-beats`, `r3x-music` | 4 |
| mode_change_sound | `r3x-audio` sfx, triggered by StageManager | 4 |
| cached_speech_service | `r3x-brain` commentary cache + `r3x-audio` | 4 |
| jev_intent_service | `r3x-intent` | 5 |
| claude | `r3x-llm` + `r3x-brain` | 5 |
| intent_router | `r3x-brain` tool dispatch | 5 |
| brain_service, timeline plans | `r3x-brain` | 5 |
| memory_service | `r3x-memory` | 5 |
| nervous_system | `r3x-memory` (persisted) + `watch` state (live) | 5 |
| latency_tracker | `r3x-ops` | 5 |
| vision | `r3x-vision` | 6 |
| gpt, web_bridge, `*_BROKEN`/`*_backup`/sdk4 variants | **dropped** | — |

## 7. Behaviours: preserve, fix, drop

Each *preserve* and *fix* item needs a test in the new code before its Python owner is retired.

### 7a. Preserve
**Turn and intent**
- Turn id minted at capture, adopted downstream.
- Jev: three-read rule, tiers (cheap 0.85/0.7, free 0.75/0.5, command gate 0.5), model pinned
  `jev-1.13.0`, one pre-warmed connection, `attempts=1`, 0.8 s timeout, fail-open, probability
  map logged on decline.
- Jev speculation on interims: ≥2 words, 150 ms debounce, ≤8 cached per turn keyed on
  normalised text, cleared each turn; finals skip the debounce.
- Jev semantic music extraction: `music_kind`/`music_vibe` ≥0.70, negative query for "avoid".
- Claude dedup: bounded wait on verdict (1.2 s) then on outcome (1.2 s),
  `<action_already_taken>`, `tool_choice: none`, tools kept in the request for cache hits.

**Claude**
- Tools: `play_music`, `search_music`, `stop_music`, `set_eye_color`, `analyze_scene`,
  `perform_show` (only when routines exist); executor also takes `next_track`,
  `set_eye_animation`.
- Personas (main + verbal-feedback), show catalogue appended once to the cached system prompt;
  `cache_control` on system prompt and last tool.
- Context blocks: `<person_memory>`, `<system_observation>`, `<conversation_history>`,
  `<user_input>`.
- SessionMemory: 50 messages, pruned above 40,000 estimated tokens; keeps tagged text.
- Verbal-feedback second call; skipped for visual-only tools, `analyze_scene`, and when the
  router already acted.
- `SPOKEN_REPLY_MAX_TOKENS=160`; only `text` blocks reach speech; warm-up call on engage.
- Temperature handling per model family (some models reject `temperature`).
- Provider resolution and both OpenRouter traps.

**Speech**
- Dialogue socket: URL/params, first message sends stability only, one socket per model+voice,
  warm-up at start, keep-alive 10 s timed from last *send*.
- Whole-reply synthesis, `new_turn:true` then `flush` — sectioned synthesis sounded disjointed.
- Stop mid-turn drops the socket. Failure before audio → HTTP fallback; after audio → error,
  never a replay.
- Character-timing rebase to absolute time; "speaking" fires at first audible sample.
- One FIFO; one voice per turn; a Claude-triggered show does not talk over the reply.
- Show tags stripped from the stream (unclosed `{` held across chunks), max two per reply,
  fired on the word; fallback pace as in code (13 chars/s at time of writing).
- Mic refuses to start while R3X speaks; released on any completion event.
- Replies route through a one-step plan so the executor owns ducking; duplicate texts per
  conversation dropped.

**Show**
- Monotonic anchor, beat chase of live tempo, `wait speech_end` with 1.5 s grace and hard
  ceiling, `interruptible_after`, nesting ≤ 3, tier rules, `show`/`gesture` layers.
- Golden files are hand-written; never regenerated.

**DJ, music, plans**
- Plan layers ambient 0 / foreground 1 / show 1 / override 2 with pause, resume, cancel.
- Step types: `speak`, `play_cached_speech`, `music_crossfade`, `music_duck`, `music_unduck`,
  `play_music`, `eye_pattern`, `move`, `parallel_steps`, `perform`, `sequence`,
  `wait_for_event`, `delay`. `perform`/`sequence` never fail a plan.
- DJ: 15 s commentary caching loop with lookahead, urgent caching, emergency selection,
  crossfade-only fallback plan, intro commentary, `dj_intro` sequence, recent-track avoidance.
- Library: `.mp3 .wav .m4a`, artist/title from filename, duplicate-title suffixes.
- Beat cache keyed path+mtime+size+version, analysed off the playback path, octave fold 70–180.
- Semantic search: 10 s segments at 15/50/85 %, positive/negative weighting, "next" walks the
  ranking.

**Hardware and ops**
- LED: connect handshake (`READY`, else `R` → `+`), 3 retries, fail-open to mock, port probing,
  chest identity check (`CHEST READY`; back off from the face's `READY`).
- Chest: 20 Hz send-on-change loop, boot sweep, 9-window health mask, fault latch rules,
  `chest.override` hold semantics, tempo following.
- Mouth: EMA α 0.3, `M000` at speech end then flash/sparkle.
- Memory: both sides of the conversation, rolling + structured visit summaries, catch-up of
  missed summaries at startup.
- Vision: 5 fps loop, exit only after consecutive empty frames, scene-capture grace/cooldown,
  skip Continuity cameras, `camera list/status/select`.
- Latency legs per conversation, exposed through `debug latency`.

### 7b. Fix deliberately (do not reproduce)
| Today | New behaviour |
|---|---|
| Deepgram never reconnects after a close | Reconnect with backoff; recording refused only while down |
| Mic sent in 0.5 s chunks | 20 ms chunks |
| Stop waits a fixed 100 ms, no `Finalize` | Send `Finalize`, wait for the final (bounded) |
| `TRACK_ENDING_SOON` is a wall-clock sleep | Derived from playback position |
| `audio_t0` ignores output latency | Add the device's reported output latency |
| Ducking is an instant step | Short ramp (default 80 ms) |
| Crossfade is 50 linear volume steps | Sample-accurate equal-power |
| Mouth amplitude depends on ElevenLabs chunk size | Fixed 20 ms analysis window, same AGC rule |
| Adapter throttles mouth to 10 Hz, service to 60 Hz | One rate, in the profile (default 30 Hz) |
| SFX play only in the browser sim | `r3x-audio` plays them; sim mirrors when standalone |
| Face firmware bug: THINKING writes past the eye array | Emulator keeps reproducing it until the firmware is fixed; fix both together |
| `next_track` does nothing outside DJ mode | Music owns `next`; DJ mode layers on top |
| SimBridge unauthenticated | Token + `Origin` check (done in Phase 0, see §9) |
| Separate Haiku defaults for summaries and scene | `CLAUDE_MODEL` everywhere |
| No barge-in | **Unchanged for now** — adding it is new scope, not part of this plan |

Each *fix* is an **intended difference**: the parity harness carries an allow-list naming it,
so an intended change is never mistaken for a regression or waved through as noise.

### 7c. Drop
- Sentiment analysis (`LLM_SENTIMENT_ANALYZED` is subscribed to and never emitted).
- GPTService, web_bridge, the Textual dashboard, legacy non-streaming TTS players
  (`afplay`/`aplay` paths), interim-streaming drafts (`ENABLE_INTERIM_STREAMING`, off).
- The 63 unreferenced topics.
- face_recognition/dlib embeddings: 128-d and incompatible with ArcFace. One person is
  enrolled and 20 source photos exist in `vision_data/training/Brandon/`; re-embed from those.

## 8. Parity harness

The smoke run's output is human-readable text over live Jev and Claude calls, so it cannot be
diffed. The harness is built from recordings instead.

1. **Full-bus tap** (Phase 0): wrap the emitter object built in `main.py`. `BaseService.emit`
   is not a choke point — 40 call sites bypass it and there are two base classes.
2. **Session log**: JSONL, one line per event: `seq, t_mono, t_wall, topic, source, payload`.
3. **Fixture recorder**: capture every external exchange keyed by request — Jev responses,
   Claude streams (chunk boundaries included), ElevenLabs audio + timings, Deepgram transcripts.
4. **Replay**: both implementations run against fixtures with external calls stubbed.
5. **Compare**: normalised traces — topic sequence per turn, key payload fields, which actions
   fired, timings within tolerance. Free text is compared only where a fixture pins it.
6. **Allow-list** of intended differences from §7b.

Small, because recording spends credits: the 7 smoke turns, the 3 `--show` scenarios, one DJ
transition, 2–3 real sessions. A service is retired only when its slice of the trace matches.

## 9. Phased roadmap (strangler)

### Phase 0 — Instrument and secure the old system
- Bus tap, JSONL session log, fixture recorder (§8).
- Tap also serves a loopback, token-protected websocket carrying **every** topic, for the bridge.
- Hand-written per-topic schema for the topics the bridge translates (payloads are untyped).
- SimBridge: token + `Origin` allow-list.
- **Baseline latency**, from real turns: stop → action, stop → first audible sample, and each
  leg. These numbers become the Phase 5 acceptance target.
- No other CantinaOS cleanup.
- Acceptance: a recorded corpus replays into CantinaOS and reproduces its own trace; the
  baseline table is written into this document.

### Phase 1 — Skeleton: contracts, bus, gateway, stage manager, CLI
- `r3x-contracts`, `r3x-bus`, `r3x-gateway`, `r3x-ops` (health), StageManager, `r3x-cli`,
  generated TS types.
- **Bridge mode:** `r3x` connects to the Phase 0 tap, translates events to typed ones, and
  sends commands back as CantinaOS bus events.
- Panel moves to the gateway: mode switch, Drive panel, per-output switches.
- Acceptance: panel works only through typed commands with acks; a test client reads retained
  state, switches to Bench, triggers an emote; an unauthenticated or wrong-origin client is
  refused.

### Phase 2 — Voice I/O, local and remote (the Lobot unblocker)
- `r3x-voice` (Deepgram, ElevenLabs), the speech half of `r3x-audio`, gateway audio frames.
- Brain is still CantinaOS: `r3x` injects the transcript through the bridge and speaks the
  reply it receives. CantinaOS's own mic and TTS services are switched off.
- Acceptance: from a browser on another machine, hold-to-talk and hear R3X; local voice loop
  no slower than the Phase 0 baseline; every §7a *Speech* item tested.

### Phase 3 — Performer + LED drivers (single conductor for the body)
- Port `sim/web/src/show/*`, `actuation/*`, `behavior.ts`, and the logic of `firmware.ts`,
  `chest.ts`, `host.ts` (~3,500 lines TS, 61 vitest tests) into `r3x-performer-core`.
- Port the 61 tests first; add player-level golden traces (blend, idle, interruption, beat
  clock) since today's two goldens cover expansion only.
- WASM build replaces the sim's TS player; face/chest serial drivers in Rust.
- CantinaOS stops playing shows; delete `show_player.py` and the TS player in the same change.
- Acceptance: golden parity; emulator pixel output byte-identical to `firmware.ts` on recorded
  command streams; real boards behave as before; alive layers toggle off in Bench.

### Phase 4 — Music engine
- Rest of `r3x-audio` (music, mixer, ducking, crossfade, sfx), `r3x-beats`, `r3x-music`.
- Beat analysis: re-analyse the library with the new analyser and compare against the librosa
  cache; any track whose BPM differs beyond octave folding is listed and reviewed before
  beat-clocked shows rely on it. `ANALYZER_VERSION` bump invalidates the old cache.
- CLAP exported to ONNX offline; ranking checked against the Python index on a fixed query set.
- Acceptance: DJ transition (duck → commentary → crossfade → unduck) on the Rust engine with
  the Python brain; SFX audible with no sim open.

### Phase 5 — Brain
- `r3x-llm`, `r3x-intent`, `r3x-brain`, `r3x-memory`, latency tracker.
- Memory import: `memory_data/events.jsonl` (192 lines), any profile JSONs (none today),
  `dj_*` keys from `nervous_system_state.json`.
- Acceptance: every §7a item tested; parity harness green; full voice loop and DJ mode on
  `r3x` alone; latency ≤ the Phase 0 baseline on every leg.

### Phase 6 — Vision
- `r3x-vision` + an enrollment command (none exists today). Re-enrol from the training photos.
- Acceptance: recognises enrolled people at least as reliably as today on a recorded clip set.

### Phase 7 — Retire CantinaOS
- Remove bridge mode; archive `cantina_os/` and `src/`; repoint `./r3x`; rewrite CLAUDE.md.

### Phase 8 — Servo hardware (may start any time after Phase 3)
- Controller firmware + `r3x_servo` driver per D6; Bench calibration wizard writes
  `calibrated: measured`.
- Acceptance: servo follows the sim within limits; host killed → controller ramps to hold
  within 250 ms.

### Phase 9 — Studio
- Timeline on the WASM performer: audio track (waveform + beat grid, or voice line + character
  timings), per-channel curves, scrub, loop, limits drawn; save to `show/`.
- Record puppet into a take → key reduction (RDP respecting vMax). `take.ts` is the start.
- Reconcile with `sim/docs/physics-simulation.md` before starting.

### Phase 10 — Public hosting
- `--public`: per-session brains, rate limits, `public` tier, per-visitor LLM/TTS budget caps.

### Checkpoint after Phase 3
Phases 0–3 deliver value whatever happens next. Before Phase 4, compare actual effort for
Phases 1–3 against expectation. If it ran far over, the fallback is to keep the Python brain
behind the typed bridge longer and do Phases 8–9 first. Direction stays all-Rust.

## 10. Don'ts
- Don't keep two conductors alive across a phase boundary.
- Don't put the browser in the hardware control loop.
- Don't hand-mirror a type; generate from `r3x-contracts`.
- Don't clean up CantinaOS beyond Phase 0.
- Don't add Python to the runtime; offline scripts only.
- Don't reproduce a §7b defect for the sake of parity.
- Don't regenerate golden files or fixtures to make a test pass.
- Don't add barge-in or volume tools inside this plan.
- Don't treat loopback as authenticated.

## 11. Corrections to CLAUDE.md (apply in Phase 7, or sooner)
- SimBridge is **not** read-only: it accepts `say`, `ptt`, `cli`, `log_level`.
- The live conversation model default is `claude-sonnet-5`, not Haiku 4.5.
- Sentiment analysis does not exist.
- Memory and latency subscriptions, `gpt_service` sections and "Stage 2" describe dead code.
- Show-tag fallback pace in code differs from the documented 19 chars/s.
- Test suite: 636 passed, 46 skipped (not 274 / 55).
- DJ state lives in NervousSystemService's JSON file, not MemoryService.
- Jev's semantic music extraction is undocumented.

## 12. Open questions for Brandon (none block Phases 0–3; defaults apply until answered)
| # | Question | Default |
|---|---|---|
| 1 | Robot onboard computer: SBC or tethered Mac? | Tethered Mac |
| 2 | Lobot: what is the embedding contract (iframe, component, auth)? Nothing in this repo describes it. | Standalone page a dashboard can iframe, token in the URL fragment |
| 3 | Public server: who pays, per-visitor budget? | Decide before Phase 10 |
| 4 | Studio first slice: timeline-with-audio or record-a-take? | Timeline-with-audio |
| 5 | Music in remote clients: local-only or streamed? | Local-only; remote hears speech only |
| 6 | Servo controller: RP2040 or ESP32; Rust or C++? | RP2040, Rust (shares the trajectory module) |
| 7 | Keep Spotify? | Yes, control-only, ported last |
