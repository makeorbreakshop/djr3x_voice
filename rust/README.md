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
  r3x-audio       cpal output engine + mixer (speech/music/sfx buses, ramped ducking), speech
                  sinks (local device with output latency, paced remote), 20 ms AGC mouth, mic
  r3x-voice       Deepgram STT, ElevenLabs dialogue-socket TTS, speech FIFO, push-to-talk,
                  gateway audio, CantinaOS voice adapter; `r3x-voice` tool binary
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
