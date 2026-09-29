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
  r3x-runtime     the binary: the whole robot in-process (default), or --bridge to a running CantinaOS
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
  r3x-motion      no_std follower + soft-limit braking + servo calibration (performer and firmware)
  r3x-servo-ctl   no_std servo controller logic (PROTOCOL.md); firmware/servo runs it on an RP2040
  r3x-performer-core, ...   see the plan, section 5
contracts-schema/ generated JSON Schema (do not edit)
```

Generated TypeScript lives in `sim/web/src/generated/` (do not edit). The robot profile is
`profiles/r3x/robot.json` at the repo root.

## Run (standalone: the default, no CantinaOS)

```bash
./r3x                                     # repo root: runtime (background, logs/r3x-runtime.log) + panel + r3x-cli here
./r3x --logs                              # follow the runtime log instead of the CLI
./r3x -- --no-vision                      # extra runtime flags after --
./r3x --legacy                            # CantinaOS instead, until the live check retires it
cargo run -p r3x-runtime                  # just the runtime (= --standalone)
cargo run -p r3x-cli                      # typed commands against it
```

Standalone = voice (Deepgram + ElevenLabs, local mic/speaker, remote browsers through the
gateway) -> the Rust brain (Jev router + Claude, memory, tools, show tags, DJ) -> the Rust music
engine on the same mixer -> performer -> face/chest/servo drivers, plus vision (fail-open) and
the gateway the panel talks to. Stage modes gate it (§4): Show = brain + DJ autonomy on;
Bench/Studio = brain off, no DJ transitions. `--no-voice`, `--no-vision`, `--brain`, `--music`
override one part; `--mouse` (feature `mouse`) = global click push-to-talk; `--audio null` =
no sound device (silent mic, `R3X_NULL_AUDIO_SPEED`).

No paid calls: `R3X_FIXTURES=replay R3X_FIXTURE_DIR=../fixtures/smoke-voice` replays Claude,
Jev, ElevenLabs audio and the recorded picks; STT is scripted (typed turns). With `--audio
null` and `FORCE_MOCK_LED_CONTROLLER=1 FORCE_MOCK_CHEST=1 FORCE_MOCK_SERVO=1` nothing touches
hardware.

Memory: `R3X_MEMORY_DB` (default `~/.config/dj-r3x/memory.sqlite`); on first start it imports
CantinaOS's `memory_data/` + DJ keys once (`R3X_CANTINA_DIR`), catches up visit summaries, and
writes a rolling summary when engagement leaves INTERACTIVE. The DJ commentary cache is the
voice's: lines are synthesised ahead (HTTP) and played through the speech FIFO, so they move
the mouth and carry character timings like any reply.

Acceptance (slow, `#[ignore]`d so `cargo test --workspace` stays quick):
`cargo test -p r3x-runtime --test standalone_acceptance -- --ignored --nocapture` (~90 s)
boots the standalone runtime (scripted STT, replayed TTS on a 6x null device, replayed
Claude/Jev, the real music engine and library, performer + mock drivers), speaks the smoke
corpus through push-to-talk, compares every turn with the CantinaOS recording
(`r3x-brain/tests/common/parity.rs`, allow-lists with reasons), runs a DJ transition with
cached commentary, and prints the latency legs against the Phase 0 baseline turns.

Live check before `cantina_os/` is archived: `docs/plans/live-check.md`.

## Run (Phase 1, bridged to CantinaOS)

```bash
cargo run -p r3x-runtime -- --bridge      # gateway on 127.0.0.1:8780, tap at ws://127.0.0.1:8766/
                                          # (--bridge: brain/music/voice default to CantinaOS)
cargo run -p r3x-cli                      # or: cargo run -p r3x-cli -- -c "mode bench" -c "emote greet"
```

Tokens: the panel uses the shared local token (`R3X_TAP_TOKEN` or `~/.config/dj-r3x/tap_token`,
opened as `#token=`); the CLI uses `~/.config/dj-r3x/cli_token`. Origins: the panel dev
servers plus `R3X_ALLOWED_ORIGINS`.

## Run (Phase 2, r3x owns voice)

```bash
R3X_EXTERNAL_VOICE=1 ./r3x --legacy       # CantinaOS without its mouse/Deepgram/ElevenLabs services
cargo run -p r3x-runtime -- --bridge --voice            # add --features mouse and --mouse for click PTT
open "http://localhost:5173/voice.html#token=$(cat ~/.config/dj-r3x/tap_token)"   # hold-to-talk page
cargo run -p r3x-voice -- roundtrip       # headless ElevenLabs -> Deepgram check (2 paid requests)
```

Keys from the env or the repo-root `.env` (every binary loads it at startup without overriding
the shell: `r3x_contracts::dotenv`; `R3X_DOTENV=path|off`). Devices: `R3X_MIC_DEVICE`, `R3X_AUDIO_OUTPUT` (name
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
cargo run -p r3x-runtime                                 # standalone: --brain rust is the default
R3X_FIXTURES=replay R3X_FIXTURE_DIR=../fixtures/smoke-voice cargo run -p r3x-runtime -- --audio null --no-vision
```

Env: `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `R3X_MEMORY_DB`,
`R3X_PERSONA_DIR`, `SHOW_DIR`, `SHOW_TAG_CHARS_PER_SEC`. Parity with the CantinaOS recordings:
`cargo test -p r3x-brain --test parity` (`PARITY_SHOW=1` prints the per-turn summaries).

## Music engine (Phase 4)

```bash
R3X_EXTERNAL_MUSIC=1 ./r3x --legacy                  # CantinaOS without MusicController / mode sound
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
cargo run -p r3x-runtime                        # standalone includes vision (--no-vision to skip)
```

Gallery at `R3X_VISION_GALLERY` (default `~/.local/share/dj-r3x/vision/gallery.json`). Env:
`R3X_CAMERA_INDEX` (else the first non-Continuity camera), `R3X_VISION_FPS` (5),
`R3X_VISION_SCENES=0` (no automatic paid scene captures), `R3X_VISION_EP=cpu` (skip CoreML).
Missing models/gallery/camera leave vision off (fail-open). Threshold 0.50 cosine: training
photos leave-one-out 0.747-0.889, 5,749 LFW impostors max 0.472.

## Servo controller (Phase 8)

```bash
cd ../firmware/servo && cargo build --release     # RP2040 firmware (thumbv6m, flip-link); see its README
cargo test -p r3x-servo-ctl                       # controller on a fake clock, incl. end to end with the host driver
```

`R3X_SERVO_PORT` points the runtime at the board. Stage outputs gate servos per channel (the
heartbeat mask). Bench calibration: Drive tab -> Calibrate (`perf cal_jog` / `cal_save`, Bench
mode, panel/CLI only) writes `calibrated: measured` into the profile (`R3X_PROFILE`, else
`profiles/r3x/robot.json`); it applies at the next start.

## Public hosting (Phase 10)

```bash
R3X_PUBLIC_SECRET=<32+ random chars> R3X_PUBLIC_ADMIN_TOKEN=<random> \
R3X_ALLOWED_ORIGINS=https://r3x.example.com,https://lobot.example.com \
R3X_PUBLIC_TRUST_PROXY=1 cargo run --release -p r3x-runtime -- --public --bind 127.0.0.1:8780
cd ../sim/web && npm run build:visit     # static page -> sim/web/dist-visit (needs public/model built)
```

`--public` (implies `--headless`: no audio device, drivers, camera, vision or music) runs only
the public server (`r3x-runtime/src/public/`): every WebSocket visitor gets its own bus,
stage, frames-only performer (shared read-only show catalogue), a public brain (own
`SessionMemory`, no memory DB, no Jev router, eye tools only, show tags as `public`) and, unless
`--no-voice`, its own Deepgram + ElevenLabs stack whose speech goes to that visitor only. A
session runs on its own thread/runtime; disconnect, idle or token expiry tears all of it down.
Remote clients never get music (plan Q5). `--headless` without `--public` is the normal
standalone runtime on the null audio device with no drivers or vision.

- **Tier**: `public` may send `intent.say`, `ptt_start/stop` (+ mic audio), perf
  `play`/`stop`/`emote` (the performer holds plays to `cheap`) and its own frame stream.
  Stage, music, DJ, console, puppet, freeze and log level are refused.
- **Tokens**: `v1.<exp>.<visitor id>.<hmac-sha256>` signed with `R3X_PUBLIC_SECRET`, TTL
  `R3X_PUBLIC_TOKEN_TTL_S` (900). Mint server-side: `curl -X POST -H "Authorization: Bearer
  $R3X_PUBLIC_ADMIN_TOKEN" https://r3x.example.com/api/token` -> `{token, expires_at}`.
  Never put the admin token in a page. `GET /healthz` = session counts.
- **Limits** (env `R3X_PUBLIC_*`, defaults conservative): per visitor token
  `VISITOR_LLM_TOKENS` 30000 (cost-weighted: input + cache writes + output + reads/10) and
  `VISITOR_TTS_CHARS` 1500; all visitors per UTC day `DAILY_LLM_TOKENS` 600000 and
  `DAILY_TTS_CHARS` 20000; `TURNS_PER_MIN` 6, `COMMANDS_PER_MIN` 120, `MAX_PTT_S` 15,
  `IDLE_S` 300; per IP `CONNECTS_PER_MIN` 10 and `SESSIONS_PER_IP` 2; `MAX_SESSIONS` 8. A
  spent cap refuses the turn with an in-character line shown as the reply (not synthesised);
  a line over the TTS cap is shown but not spoken. Caps live in memory (a restart resets
  them): check the provider dashboards for real spend.
- **Open question (plan §12 Q3): who pays** for public Claude/Deepgram/ElevenLabs traffic,
  and the real per-visitor budget. Until answered, run it with its own low-limit keys.
- **TLS**: not in the binary. Put Caddy in front (bind the runtime to loopback, set
  `R3X_PUBLIC_TRUST_PROXY=1` so limits key on `X-Forwarded-For`):

```caddyfile
r3x.example.com {
    handle_path /api/* {
        reverse_proxy 127.0.0.1:8780    # websocket upgrade is automatic
    }
    root * /srv/r3x/dist-visit
    file_server
}
```

**The page** (`sim/web/visit.html`, built by `npm run build:visit` with relative paths):
the sim renderer + the WASM performer. Without a token it is standalone (idle life, looks at
you, emote buttons; no server, no cost). With `#token=` it follows the visitor's session
(frames, captions, hold-to-talk, typed turns, emotes) and falls back to standalone when the
link drops. `?gw=host[:port][/path]` names the server (`wss` on an https page). Embed
(plan Q2 default: token in the fragment, which browsers never send to a server):

```html
<iframe src="https://r3x.example.com/visit.html?gw=r3x.example.com/api#token=TOKEN_FROM_YOUR_BACKEND"
        allow="microphone; autoplay" style="width:100%;height:600px;border:0"></iframe>
```

The embedding page's origin and the page's own origin both go in `R3X_ALLOWED_ORIGINS`
(the WebSocket `Origin` is the iframe document's, i.e. `https://r3x.example.com`).

## Build and test

```bash
cd rust
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

Slow / acceptance (not in `--workspace`; run before merging runtime/brain/voice/music work
and before the live check):

```bash
cargo test -p r3x-runtime --test standalone_acceptance -- --ignored --nocapture   # ~90 s
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
