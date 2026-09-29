# R3X Rust workspace

The all-Rust R3X runtime. Spec: `docs/plans/r3x-platform-architecture.md`.

```
crates/
  r3x-contracts   Envelope, Source/Tier, Command/Ack, Event, retained state, Frames, RobotProfile
  r3x-bus         typed bus: broadcast per event domain, watch per state domain,
                  mpsc + oneshot Ack per command class, seq/clock stamping, JSONL session log
  r3x-gateway     axum WS, protocol v1: token + Origin on every bind, stamped source, class/tier gates
  r3x-ops         health -> state.services, runtime log fan-out, log-level control
  r3x-stage       StageManager: Show/Bench/Studio, outputs, alive layers, brain, autonomy, freeze, engagement
  r3x-runtime     the binary; --bridge drives a running CantinaOS through its bus tap
  r3x-cli         terminal gateway client (CantinaOS command set + shortcuts, history)
  r3x-brain       turns (Jev router || Claude + dedup), tool dispatch, show tags, plan executor,
                  DJ planner + commentary cache; `--brain rust` (default `cantina` until Phase 5)
  r3x-audio       cpal output engine + mixer (speech/music/sfx buses, ramped ducking), speech
                  sinks (local device with output latency, paced remote), 20 ms AGC mouth, mic
  r3x-voice       Deepgram STT, ElevenLabs dialogue-socket TTS, speech FIFO, push-to-talk,
                  gateway audio, CantinaOS voice adapter; `r3x-voice` tool binary
  r3x-beats       tempo + beat grid (librosa beat_track ported), cache, background analysis
  r3x-music       library + matching, playback engine (crossfade, ducking, next, ending-soon),
                  CLAP search (ort), sfx, commentary cache on the bus, CantinaOS music adapter
  r3x-vision      camera (AVFoundation via ffmpeg), YuNet + SFace on ort, gallery enrolment,
                  presence, Claude scene description; `r3x-vision` tool binary
  r3x-performer-core, ...   see the plan, section 5
contracts-schema/ generated JSON Schema (do not edit)
```

Generated TypeScript lives in `sim/web/src/generated/` (do not edit). The robot profile is
`profiles/r3x/robot.json` at the repo root.

## Run (Phase 1, bridged to CantinaOS)

```bash
cargo run -p r3x-runtime -- --bridge      # gateway on 127.0.0.1:8780, tap at ws://127.0.0.1:8766/
cargo run -p r3x-cli                      # or: cargo run -p r3x-cli -- -c "mode bench" -c "emote greet"
```

Tokens: the panel uses the shared local token (`R3X_TAP_TOKEN` or `~/.config/dj-r3x/tap_token`,
opened as `#token=`); the CLI uses `~/.config/dj-r3x/cli_token`. Origins: the panel dev
servers plus `R3X_ALLOWED_ORIGINS`.

## Run (Phase 2, r3x owns voice)

```bash
R3X_EXTERNAL_VOICE=1 ./r3x ...            # CantinaOS without its mouse/Deepgram/ElevenLabs services
cargo run -p r3x-runtime -- --bridge --voice            # add --features mouse and --mouse for click PTT
open "http://localhost:5173/voice.html#token=$(cat ~/.config/dj-r3x/tap_token)"   # hold-to-talk page
cargo run -p r3x-voice -- roundtrip       # headless ElevenLabs -> Deepgram check (2 paid requests)
```

Keys from the env or the repo-root `.env`. Devices: `R3X_MIC_DEVICE`, `R3X_AUDIO_OUTPUT` (name
substrings; `r3x-voice devices` lists them), `R3X_LOCAL_AUDIO=0` for headless. Another machine
needs `--bind 0.0.0.0:8780`, its origin in `R3X_ALLOWED_ORIGINS`, and HTTPS/WSS for the page
(browsers only open the mic in a secure context).

## The performer (Phase 3)

The runtime hosts the only conductor: `r3x-performer-core` ticking at 50 Hz on the bus clock
(`r3x-runtime/src/performer.rs`). It takes the `perf` command class (tiers checked per
source), follows `state.stage` (outputs, alive layers, autonomy = idle policy, freeze) and
engagement, and the conversation/music/DJ/health events; it publishes `perf.*` events,
`state.perf`, frames (virtual driver, 50 Hz, for `telemetry.frames` followers) and hands named
LED lines and servo goals to `r3x-drivers`. The show folder (`SHOW_DIR`, else `show/`) is
reloaded when a file changes.

In bridge mode CantinaOS only *requests* shows: its `show.perform` / `show.stop` /
`motion.freeze` / `eye.command` become perf commands, and the bridge sends back
`show.started|ended`, `show.sfx`, `stage.lights`, a show's `speak` line as
`tts.generate.request` and its `duck` as `audio.ducking.*`. The LED boards are r3x's by
default: run CantinaOS with `R3X_EXTERNAL_BODY=1`, or give the runtime `--leds cantina`
(`R3X_LEDS=cantina`) to leave them to CantinaOS (eye/chest actions are then mirrored as
`eye.command` / `chest.override`). The sim follows gateway frames, or embeds the same
performer as WASM with `?offline` (`npm run build:wasm` in `sim/web` first).

## Rust brain (Phase 5, instead of CantinaOS)

```bash
cargo run -p r3x-runtime -- --voice --brain rust        # not with --bridge
R3X_FIXTURES=replay R3X_FIXTURE_DIR=../fixtures/smoke-voice cargo run -p r3x-runtime -- --brain rust
```

Env: `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `R3X_MEMORY_DB`,
`R3X_PERSONA_DIR`, `SHOW_DIR`, `SHOW_TAG_CHARS_PER_SEC`. Parity with the CantinaOS recordings:
`cargo test -p r3x-brain --test parity` (`PARITY_SHOW=1` prints the per-turn summaries).

## Music engine (Phase 4)

```bash
R3X_EXTERNAL_MUSIC=1 ./r3x ...                       # CantinaOS without MusicController / mode sound
cargo run -p r3x-runtime -- --bridge --music rust    # r3x plays music + sfx (default: cantina)
cargo run --release -p r3x-beats -- compare --out ../docs/plans/beat-analyzer-comparison.md
venv/bin/python scripts/export_clap_onnx.py          # once: CLAP ONNX into ~/.cache/dj-r3x/clap
cargo test -p r3x-music --release --test clap_parity -- --ignored --nocapture
```

`--music rust` plays on the voice's output mixer (or its own device), publishes `state.music`
with `position_s`@`position_t` (the performer's beat-clock anchor, with `track.bpm` and
`first_beat_s`), serves the brain's `music.*` requests and the commentary cache, and plays
`perf.sfx` cues and the mode-change ding. Env: `MUSIC_DIR`, `R3X_SFX_DIR` (default
`sim/web/public/sfx`), `R3X_MODE_SOUND`, `R3X_CLAP_DIR`, `R3X_BEAT_CACHE_DIR`,
`ENABLE_BEAT_ANALYSIS`, `R3X_SEMANTIC`. Spotify is an interface stub (plan D7: ported last).

## Vision (Phase 6)

```bash
crates/r3x-vision/scripts/download_models.sh     # YuNet + SFace -> ~/.cache/dj-r3x/vision (sha256-checked)
cargo run -p r3x-vision --release -- enroll ../cantina_os/vision_data/training   # <root>/<Name>/*.jpg
cargo run -p r3x-vision --release -- eval ../cantina_os/vision_data/training --negatives <lfw dir>
cargo run -p r3x-runtime -- --voice --brain rust --vision
```

Gallery at `R3X_VISION_GALLERY` (default `~/.local/share/dj-r3x/vision/gallery.json`). Env:
`R3X_CAMERA_INDEX` (else the first non-Continuity camera), `R3X_VISION_FPS` (5),
`R3X_VISION_SCENES=0` (no automatic paid scene captures), `R3X_VISION_EP=cpu` (skip CoreML).
Missing models/gallery/camera leave vision off (fail-open). Threshold 0.50 cosine: training
photos leave-one-out 0.747-0.889, 5,749 LFW impostors max 0.472.

## Build and test

```bash
cd rust
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

## Regenerate TS types and JSON Schema

After changing anything in `r3x-contracts`:

```bash
cargo run -p r3x-contracts --bin export          # writes sim/web/src/generated + contracts-schema
cargo run -p r3x-contracts --bin export -- --check   # exits 1 if the committed output is stale
```

`cargo test` runs the `--check` too, so a stale binding fails the suite.

## Robot profile

`profiles/r3x/robot.json` was seeded from `sim/web/src/actuation/servo_map.json` (actuators,
calibration, v/a/j limits) and `sim/web/src/show/rig_limits.json` (hard ranges). Joint parents
and the 33 chest pixel positions were copied once from a locally built
`sim/web/public/model/rig.json` (gitignored). Soft range = hard range intersected with the
servo's reach, minus the channel margin; the animation range starts equal to soft.
`RobotProfile::load` parses and validates it (nested ranges, known joints/parents, no cycles,
unique driver channels, pixel/layout counts).
