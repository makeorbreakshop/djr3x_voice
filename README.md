# DJ-R3X Voice Assistant

A voice-first "mini-assistant" that listens, thinks, and speaks back in a DJ R3X-inspired voice from Star Wars.

## Overview

DJ-R3X Voice Assistant is a Python application that creates an interactive Star Wars droid DJ experience with:

- **Speech recognition**: Deepgram Nova-3 over a persistent streaming WebSocket
- **Fast intent routing**: deterministic tools fire in ~190 ms, before the LLM is even called
- **Conversation**: Claude Haiku 4.5, with prompt caching
- **Text-to-speech**: ElevenLabs Flash v2.5
- **LED animation**: eye and mouth animations driven off speech amplitude, over serial to an Arduino
- **Music**: local library or Spotify via VLC, auto-ducking under speech

The runtime is the event-driven **CantinaOS** architecture: one event bus, 22 services,
started in a fixed order by `cantina_os/cantina_os/main.py`.

## 🎛️ Control Panel

```bash
./r3x                # CantinaOS + control panel; opens http://localhost:5391
./r3x --no-open      # don't open a browser tab
./r3x --panel-only   # just the panel (its offline demo runs until a backend connects)
```

`./setup-dj-r3x-command.sh` installs a `dj-r3x` command that runs `./r3x` from anywhere.
Ctrl-C stops everything; the terminal keeps the CantinaOS CLI.

The panel is `sim/web` - the 3D R3X - with four tabs:
- **Talk**: push-to-talk (hold the button, click to toggle, or hold Space), typed turns, mode
  buttons, and the conversation with per-turn reply/voice latency
- **Control**: music library and playback, DJ mode, eye patterns, the CLI, service health
- **Logs**: the backend's log records and bus events, live, filterable by level and text
- **Sim**: the digital twin's own tools - show system, puppeteer, LEDs, servos, cameras

It talks to `SimBridgeService` on `ws://127.0.0.1:8765`. Everything the panel does is
emitted as the same bus event the mouse, the capture services or the terminal would emit.
While a panel is connected, global mouse clicks no longer toggle the mic.

## Hardware Configuration

### Components

The DJ-R3X Voice Assistant utilizes the following hardware components:

1. **Arduino Mega 2560 R3**:
   - Main microcontroller for LED matrix control
   - Connected via USB to the host computer
   - Serial communication at 115200 baud rate

2. **MAX7219 LED Matrix Modules** (2):
   - Single color (typically red) 8x8 LED matrix modules
   - Connected to Arduino for eyes visualization
   - Wiring:
     - VCC: 5V from Arduino
     - GND: Ground from Arduino
     - DIN: Pin 51 on Arduino
     - CS: Pin 53 on Arduino
     - CLK: Pin 52 on Arduino

3. **Computer with Microphone**:
   - For voice input and processing
   - Required for running the Python software
   - Connects to Arduino via USB

4. **Speaker System**:
   - For voice output and music playback
   - Connected to the computer

### Wiring Diagram

```
Arduino Mega 2560 R3       MAX7219 LED Matrices (2)
+----------------+         +------------------+
|                |         |                  |
| Pin 51 (DIN) --|---------|DIN               |
| Pin 52 (CLK) --|---------|CLK               |
| Pin 53 (CS)  --|---------|CS                |
| 5V           --|---------|VCC               |
| GND          --|---------|GND               |
|                |         |                  |
+----------------+         +------------------+
       |
       | USB
       |
+----------------+
|    Computer    |
+----------------+
```

The two MAX7219 modules are daisy-chained together, with the first module controlling the left eye and the second module controlling the right eye.

### LED Matrix Configuration

Each 8x8 LED matrix represents one eye of DJ-R3X:
- Center position is at (3,3) for each matrix
- Animations typically use a 3x3 grid centered at this position
- Different animation patterns represent different states (idle, listening, speaking, etc.)
- LED intensity is configurable in the Arduino code

## Setup Instructions

### 1. Install Dependencies

CantinaOS runs from a virtualenv at the repo root and needs Python 3.11 (see
`pyproject.toml`). PortAudio must be present before pyaudio will build:

```bash
brew install portaudio            # macOS; required by pyaudio
brew install --cask vlc           # required by MusicControllerService via python-vlc

python3.11 -m venv venv
./venv/bin/python -m pip install --upgrade pip setuptools wheel
./venv/bin/python -m pip install -r cantina_os/requirements.txt
```

`cantina_os/requirements.txt` is the single source of truth - it is the file
`./r3x` installs, and the root `requirements.txt` now just defers to it with
`-r`. (Until 2026-09-17 these were two independent lists that disagreed with each other,
and this README pointed at the wrong one.)

`./r3x` installs dependencies automatically, keyed on a SHA-256 of
`cantina_os/requirements.txt`, so editing that file triggers a reinstall on the next start
and an unchanged file costs nothing.

**Two pins are deliberate. Do not widen them without reading why:**

| Pin | Why |
|---|---|
| `anthropic>=0.72.0,<1.0` | anthropic 1.x removes `temperature` from `messages.create()` with no `**kwargs` passthrough and no replacement anywhere in `anthropic.types`. Seven call sites pass it. Migrating is a real behaviour change, not a version bump. |
| `deepgram-sdk>=5.3.4,<6.0` | 6.x and 7.x delete the `deepgram.extensions` package that the live STT service needs for `ListenV1ControlMessage`. The 7.x equivalent is `deepgram.listen.v1.ListenV1KeepAlive`, which means rewriting the only live STT path. |

### 2. API Keys Setup
You need to set up the following API keys:

#### ElevenLabs API Key
1. Go to https://elevenlabs.io and create an account
2. Go to https://elevenlabs.io/app
3. Click on your profile icon in the top right
4. Select 'Profile' or 'API Key'
5. Copy one of your API keys

You can also use the helper script to test your ElevenLabs API key:
```bash
python3 get_new_elevenlabs_key.py
```

#### ElevenLabs Voice ID
1. After getting your API key, go to https://elevenlabs.io/voice-library
2. Find or create a voice you want to use
3. Click on the voice
4. Copy the Voice ID from the URL (the string after /voice-lab/)

#### Anthropic API Key
The LLM is Claude Haiku 4.5, not GPT. Get a key at https://console.anthropic.com/settings/keys.
(`openai` was removed as a dependency on 2026-09-17 - nothing under `cantina_os/` imports it.)

#### Deepgram API Key
Streaming speech-to-text: https://console.deepgram.com/

#### Anthropic API Key — or an OpenRouter key instead
The conversational LLM is Claude Haiku 4.5. There are two ways to reach it, and the code
picks between them on its own (`cantina_os/llm/anthropic_provider.py`):

| Set this | What happens |
|---|---|
| `ANTHROPIC_API_KEY` | Direct to Anthropic. This wins whenever it is present. |
| `OPENROUTER_API_KEY` only | The same `anthropic` SDK, pointed at OpenRouter's Anthropic-compatible `/v1/messages`. |
| Neither | ClaudeService fails to initialise and every turn falls back to the fast router alone. |

OpenRouter works because it speaks the Anthropic Messages format verbatim, and the official
SDK is not bound to Anthropic's host: it sends `x-api-key` and `anthropic-version` to
`{base_url}/v1/messages`. Streaming, `temperature`, `tools` and `tool_choice: {"type":
"none"}` all behave identically — measured, see `scripts/claude_live_verify.py`.

Two knobs, both optional:

* `LLM_PROVIDER` — `anthropic`, `openrouter`, or `auto` (the default). A forced provider
  whose key is missing is treated as unavailable; it does **not** fall through to the other.
* `ANTHROPIC_BASE_URL` — overrides the host for whichever provider was chosen. For a local
  proxy or another gateway.

Model ids are translated at the client boundary, so configure `CLAUDE_MODEL` with the
Anthropic id either way (`claude-haiku-4-5-20251001` becomes `anthropic/claude-haiku-4.5`).
Note for anyone adding a provider: the SDK appends its own `/v1`, so the base URL is
`https://openrouter.ai/api`, **not** `.../api/v1`.

Pricing is the same on both for Haiku 4.5 — $1.00 / $5.00 per million input/output tokens.

#### typesafe.ai API Key
Powers the Jev fast intent router (see below). https://typesafe.ai
**Optional** - without it the router is simply inactive and every turn takes the slower
Claude path.

### 3. Create a .env File
Create a `.env` in the repo root. `env.example` is the template.

```
# --- Required ---
# One of these two. ANTHROPIC_API_KEY wins if both are set; see above.
ANTHROPIC_API_KEY=...            # Claude Haiku 4.5 - the conversational LLM
OPENROUTER_API_KEY=...           # same SDK, Anthropic-compatible gateway
# LLM_PROVIDER=auto              # anthropic | openrouter | auto (default)
# ANTHROPIC_BASE_URL=            # override the host for the chosen provider
DEEPGRAM_API_KEY=...             # streaming speech-to-text
ELEVENLABS_API_KEY=...           # speech synthesis
ELEVENLABS_VOICE_ID=...

# --- Fast intent router (optional but strongly recommended) ---
# Absent -> router inactive, every turn takes the Claude path (~1.9 s to action).
TYPESAFE_API_KEY=...
JEV_ROUTER_ENABLED=true          # kill switch
JEV_CONFIDENCE_THRESHOLD=0.85    # cheap-tier gate; the free tier is this minus 0.10
JEV_COMMAND_THRESHOLD=0.5        # the is_a_command veto
JEV_TIMEOUT_S=0.8                # HTTP timeout; no retries on the hot path
JEV_SPECULATE=true               # classify partial transcripts while the user is still talking
FAST_ROUTER_WAIT_S=1.2           # how long ClaudeService waits for the router's verdict

# --- Hardware ---
ARDUINO_SERIAL_PORT=/dev/cu.usbmodemXXXXX
MOCK_LED_CONTROLLER=false
FORCE_MOCK_LED_CONTROLLER=false  # set true to run with no Arduino attached

# --- Music ---
MUSIC_DEFAULT_SOURCE=local       # local | spotify
ENABLE_SPOTIFY=false
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REDIRECT_URI=
ENABLE_SEMANTIC_MUSIC_SEARCH=true # CLAP search over the local audio library
SEMANTIC_MUSIC_DEVICE=cpu         # steadier tail latency than MPS on the Mac Studio
SEMANTIC_MUSIC_NEGATIVE_WEIGHT=0.5

# --- Personality ---
DJ_R3X_PERSONA_FILE=dj_r3x-persona.txt
```

On the first start, semantic music search downloads the CLAP model and indexes three
ten-second excerpts from every local track. The resulting vectors are cached under
`~/.cache/dj-r3x/`; later starts load the cache. Named requests still use title/artist matching,
while requests such as `play music fun and upbeat` use the audio index. Voice requests are
first reduced by Jev, so unrelated conversation in a long transcript is not sent to CLAP.

Note: `ARDUINO_SERIAL_PORT` in `.env` **overrides** the `serial_port` passed to
`EyeLightControllerService` in code (`eye_light_controller_service.py:258-266`). That is by
design, but it means a stale value in `.env` wins over anything a caller asks for.

### 4. Verify the keys

```bash
cd cantina_os
../venv/bin/python -m pytest tests/test_jev_integration_live.py -q   # exercises TYPESAFE_API_KEY
../venv/bin/python scripts/system_smoke_run.py --with-tts            # exercises ELEVENLABS_API_KEY
```

(The old `test_elevenlabs_rest.py` this section used to point at no longer exists.)

### 5. Arduino Setup

1. Connect the Arduino Mega 2560 to your computer via USB
2. Connect the MAX7219 LED matrix modules following the wiring diagram
3. Upload the `arduino/rex_eyes/rex_eyes.ino` sketch to the Arduino using the Arduino IDE
4. Verify the serial connection is working (Arduino will output "READY" when initialized)
5. Note the serial port being used (typically something like `/dev/ttyACM0` on Linux, `/dev/tty.usbmodem*` on macOS, or `COM*` on Windows)
6. Update the `.env` file with your Arduino serial port:
   ```
   LED_SERIAL_PORT=/dev/ttyACM0  # Replace with your actual port
   LED_BAUD_RATE=115200
   ```

### 6. Run the Program

Everything - dependency install, CantinaOS and the control panel - starts from one script:

```bash
./r3x                    # CantinaOS + control panel on http://localhost:5391
```

It reinstalls Python dependencies only when `cantina_os/requirements.txt` changes (a
checksum stamp), and points python-vlc at `/Applications/VLC.app`.

CantinaOS alone, with its CLI on stdin:

```bash
cd cantina_os
../venv/bin/python -m cantina_os.main
```

Then type `engage` to enter interactive voice mode. Without the panel the trigger is a
**left mouse click** (click once to start recording, click again to stop) - there is no wake word in the live
loop, despite the Porcupine model and `test_wake_word.py` still being on disk. On macOS the
click trigger needs Accessibility permission, and it must be granted to **Terminal.app** -
Warp does not work.

`help` lists the CLI commands. `engage` / `ambient` / `disengage` change mode; `play music`,
`stop music`, `dj start`, `dj stop`, `dj next`, `eye pattern <name>` and
`debug latency` are all reachable without speaking.

### Running the system without a microphone

`cantina_os/scripts/system_smoke_run.py` boots a real CantinaOS - real event bus, real
IntentRouterService, CommandDispatcherService, BrainService and MusicControllerService
driving real VLC - mocks the Arduino, skips the three services that need hardware and OS
permissions (mic capture, the mouse trigger, vision), and injects transcripts exactly as
the capture services emit them. It prints every bus event per turn with millisecond offsets
and an event-loop tick count, so a freeze shows up as a tick count near zero.

```bash
cd cantina_os
../venv/bin/python scripts/system_smoke_run.py            # silent; no TTS
../venv/bin/python scripts/system_smoke_run.py --with-tts # also start ElevenLabs (paid, audible)
```

This is how the eye-colour and next-track bugs fixed on 2026-09-17 were found; neither was
visible to unit tests.

### Running the tests

```bash
cd cantina_os
../venv/bin/python -m pytest tests/ -q                       # whole suite
../venv/bin/python -m pytest tests/test_jev_intent_router.py -q   # fast, offline, no API
```

Current state: **274 passed, 55 skipped, 0 failed.** Every skip carries a written reason
naming the production code that changed - dead services (`gpt_service`, the SDK-4 deepgram
service, the flat `music_controller_service.py` shadowed by its package), hardware-bound
tests, tests needing real API credit, and a handful pinned open against production defects
that should be fixed rather than asserted.

Some suites make real network calls:

- `tests/test_jev_integration_live.py` calls the real typesafe.ai API (needs
  `TYPESAFE_API_KEY`; ~30 classifications, about $0.002).
- `tests/test_jev_intent_router.py` is fully offline - it replays recorded `jev-1.13.0`
  responses from `tests/fixtures/jev_recorded_responses.json`.

Dashboard tests:

```bash
cd dj-r3x-dashboard
npm test -- --run
npm run build
```

## Architecture

The runtime is **CantinaOS**: `cantina_os/cantina_os/main.py` builds one
`pyee.AsyncIOEventEmitter` bus and starts 22 services in a fixed order. Services talk only
over the bus - direct calls between them are allowed for read-only state queries and
prohibited for mutations. CLAUDE.md has the full pipeline walkthrough, event-payload
conventions and per-service notes.

(Earlier revisions of this README described an "MVP architecture" under `src/` -
`bus.py`, `voice_manager.py`, `led_manager.py`, `music_manager.py`. That layer is
superseded and no longer runs. It is documented here only so nobody edits it expecting an
effect.)

### A turn, end to end

```
left mouse click  ->  MouseInputService        pynput
  ->  DeepgramDirectMicService                 PyAudio -> Deepgram streaming WebSocket
        mints the turn's conversation_id, emits VOICE_LISTENING_STARTED
  ->  VOICE_LISTENING_STOPPED { transcript, conversation_id }
        |
        +--> JevIntentService      ~190 ms, deterministic, fires the action
        |      -> INTENT_DETECTED -> IntentRouterService -> CommandDispatcherService
        |           -> MUSIC_COMMAND / EYE_COMMAND / DJ_COMMAND
        |
        +--> ClaudeService         waits for the router's verdict, then speaks
               -> LLM_RESPONSE -> ElevenLabsService -> TimelineExecutorService -> speaker
```

### The Jev fast intent router

Measured before this existed: a click-to-stop took **1,899 ms** to actually start the music,
and **73% of that was a single Claude round trip whose only job was to pick a tool name.**
The event bus and the whole tool-routing chain cost **15 ms combined** - the bus was never
the problem.

`JevIntentService` wakes on the same `VOICE_LISTENING_STOPPED` event as `ClaudeService` and
asks the typesafe.ai System One API a fixed set of small questions about the transcript in
one round trip, getting back calibrated probabilities rather than prose. When they agree,
it emits `INTENT_DETECTED` - the exact event `ClaudeService` emits after a tool call, so the
execution side needed no changes at all.

**Measured on the real event bus:** transcript -> `MUSIC_COMMAND` **p50 197 ms** (min 160,
max 260), of which CantinaOS itself is **2-3 ms**; the rest is the network. A speculative
cache hit - the router classifies partial transcripts while the user is still talking -
dispatches in **1.6 ms**.

Three reads must agree before anything fires:

1. a **Choice** across all six tools plus `general_chat` / `unclear` - decides *which*;
2. **one Noul per tool** - an absolute "is the speaker asking for *this*, now?";
3. **`is_a_command`** - "instruction, or conversation?".

The independence is what buys the safety. *"did you turn the music down"* **wins** the
Choice competition at 0.94 and **fails** its own Noul at 0.47. Over 66 utterances x 5 passes
x 8 designs, this was the only arm with **0 false triggers and 0 wrong executions in 330
calls**; every choice-only design false-triggers.

Thresholds are risk-tiered, because one number is the wrong model:

| Tier | Tools | Gate |
|---|---|---|
| Free - instantly reversible | `set_eye_animation`, `next_track` | confidence >= 0.75, noul >= 0.5 |
| Cheap - reversible but noticeable | `play_music`, `stop_music`, `dj_mode_on/off` | confidence >= 0.85 **and** noul >= 0.7 |
| Committing - writes shared state | *(none today)* | never fires from the router alone |

The free tier is derived as `JEV_CONFIDENCE_THRESHOLD - 0.10`, so one knob moves both.

**It fails open by construction.** No `TYPESAFE_API_KEY`, a timeout, an HTTP error or a
malformed body all produce "no verdict", and the turn proceeds down the Claude path exactly
as it did before. A miss costs one slow turn; a false trigger blasts music into the room
while someone was asking a question. The whole design is built around that asymmetry -
which is why *"quiet please"* being declined is the *correct* failure, not a bug.

**Dedup.** Both services wake on the same event, so without coordination Claude's own
`tool_use` would restart the music a second time and narrate a future it did not cause.
`ClaudeService` awaits the router's verdict (bounded by `FAST_ROUTER_WAIT_S`, default 1.2 s;
no router registered means no wait at all), and when an action was taken it prepends an
`<action_already_taken>` block and sets `tool_choice={"type": "none"}` - the tools stay in
the request so the prompt cache still hits, but Claude can only speak.

Model is pinned to **`jev-1.13.0`**, not `jev-latest`: a silent model bump would move every
threshold underneath us.

### Known gaps

- **No wake word in the live loop.** The trigger is a left mouse click. A Porcupine model
  and a working test harness exist and are wired to nothing.
- **No automatic end-of-speech detection.** Deepgram's `endpointing`, `utterance_end_ms`
  and `vad_events` were all deliberately disabled; you click again to stop.
- **`next_track` only works in DJ mode.** `dj next` is the only skip capability, and
  MusicControllerService understands only "play" and "stop" - there is no playlist cursor
  to advance otherwise.
- **`_select_smart_track` has a hardcoded track list**, so generic requests resolve to
  `cantina_band` and the real Spotify library is never consulted.
- **The dashboard is structurally disconnected.** `dj-r3x-bridge/` creates its own
  `EventBus()` in its own process and no script launches it, so dashboard commands reach
  nothing.

## Troubleshooting

- If you see an error about `ELEVENLABS_API_KEY not found in .env file`, make sure your .env file exists and contains the correct API key.
- If PyAudio installation fails, make sure you have installed PortAudio first.
- If you have issues with audio playback, make sure pygame is installed correctly.
- Check that your microphone is working properly for speech recognition.
- For Arduino LED connection issues, verify the correct serial port in the error messages.
- If the Arduino code fails to compile, ensure you have the LedControl library installed. You can install it through Arduino IDE's Library Manager.

## Features

### Speech recognition
- **Deepgram** Nova-3 over a persistent streaming WebSocket kept alive between turns, so no
  handshake is paid mid-utterance
- Trigger is a **left mouse click** (click to start, click again to stop). There is no wake
  word in the live loop and no automatic end-of-speech detection - Deepgram's `endpointing`,
  `utterance_end_ms` and `vad_events` are deliberately disabled
- `openai-whisper` was removed as a dependency on 2026-09-17; nothing imported it

### Intent routing
- The **Jev fast intent router** dispatches `play_music`, `stop_music`, `dj_mode_on/off`,
  `next_track` and `set_eye_animation` in ~190 ms, before Claude is called at all
- Risk-tiered thresholds, three independent reads that must agree, and fail-open behaviour -
  see the Architecture section

### Conversation
- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) with prompt caching
- Blocking SDK calls run on `asyncio.to_thread`, so the event loop keeps ticking during a
  turn - verified at 175 loop ticks through a turn that included both a Jev call and a
  blocking Claude call
- When the fast router already acted, Claude gets an `<action_already_taken>` block and
  `tool_choice={"type": "none"}`, so it narrates rather than re-running the action

### Speech synthesis
- **ElevenLabs** Flash v2.5 (`eleven_flash_v2_5`), PCM, streamed on a worker thread

### LED animation
- Eye and mouth patterns for idle, listening, thinking and speaking, plus sentiment colours
- Mouth brightness is modulated from live speech amplitude
- Runs without an Arduino attached via `FORCE_MOCK_LED_CONTROLLER=true`

### Music
- Local library from `audio/music/` (23 tracks) or Spotify Connect, both through VLC
- Auto-ducks under speech and restores afterwards
- DJ mode queues tracks with generated commentary between them; reachable by voice since
  2026-09-17

## Usage

### Interactive Mode

```bash
cd cantina_os
../venv/bin/python -m cantina_os.main
```

Type `engage` to enter interactive voice mode, then trigger a turn with a left mouse click
(click to start recording, click again to stop). `help` lists every CLI command; the useful
ones are `engage` / `ambient` / `disengage`, `play music`, `stop music`, `list music`,
`dj start` / `dj stop` / `dj next`, `eye pattern <name>`, `debug latency` and `quit`.

## Project Structure

### What actually runs
- `cantina_os/cantina_os/main.py`: the `CantinaOS` application - builds the event bus,
  registers CLI commands, and starts 22 services in a fixed order
- `cantina_os/cantina_os/core/`: event bus, event topics, payload models, the fast-router gate
- `cantina_os/cantina_os/services/`: one directory or module per service (see CLAUDE.md for
  the full pipeline walkthrough)
- `cantina_os/cantina_os/llm/`: the Jev client, its question catalogue, and the Claude prompt
  assembly
- `cantina_os/scripts/system_smoke_run.py`: drive a real CantinaOS with hardware mocked
- `r3x`: the supported way to start everything (CantinaOS + control panel)
- `sim/web/`: the control panel and 3D digital twin
- `dj-r3x-dashboard/`: the retired Next.js dashboard (nothing starts it)
- `audio/music/`: the local music library MusicControllerService plays from
- `arduino/`: the LED firmware

### Configuration
- `env.example`: template for your `.env`
- `cantina_os/requirements.txt`: the only Python dependency list (the root file defers to it)

### Not live, despite appearances
Kept on disk but reachable from nothing. Do not edit these expecting an effect - a fix
applied here is a fix applied to nothing:

- `src/`, `rex_talk.py`, `run_rex.py`, `run_r3x_mvp.py` and the rest of the pre-CantinaOS
  MVP: gone or superseded. Earlier revisions of this README documented them as the way to
  run the project; they are not.
- `services/gpt_service.py`: in main.py's `service_class_map` but absent from
  `service_order`, so never instantiated. The live LLM path is `ClaudeService`.
- `services/music_controller_service.py` (flat file): shadowed by the package of the same
  name, which is what Python imports and what main.py runs.
- `services/web_bridge_service.py` and `dj-r3x-bridge/`: in neither `service_order` nor
  `service_class_map`, and the bridge runs its own `EventBus()` in its own process that
  shares nothing with CantinaOS. No script launches it.
- `services/deepgram_direct_mic_service_sdk4.py` and the three
  `deepgram_direct_mic_service_sdk5*` variants: only
  `deepgram_direct_mic_service.py` is imported. Five files named after the same service is
  a real hazard when grepping.
- `simple_eye_adapter_v2_backup.py`, `simple_eye_adapter_v3.py`,
  `eye_light_controller_service_v3_patch.py`: only `simple_eye_adapter.py` is imported.
- `DJ-R3X-Web/`: contains nothing but `node_modules/`.
- The Porcupine wake-word model and `test_wake_word.py`: a working harness wired to
  nothing. The live trigger is a mouse click.

### Testing
See "Running the tests" above. 274 passing, 55 explicitly skipped.

## License

This project is for personal use and entertainment purposes. Star Wars and DJ R3X are trademarks of Disney/Lucasfilm.
