# DJ R3X Voice - Architecture Overview

## Executive Summary

DJ R3X is an AI-powered voice-interactive DJ robot that processes user speech, generates
intelligent responses, and controls music playback with synchronized LED animations. The
architecture emphasizes event-driven decoupling, ROS-inspired service patterns, and precise
audio pipeline coordination.

**The runtime is CantinaOS** (`cantina_os/`). The legacy MVP under `src/` is superseded and
no longer runs - it is described in section 1 only so nobody edits it expecting an effect.

**The LLM is Claude Haiku 4.5, not GPT.** `openai` was removed as a dependency on
2026-09-17; nothing under `cantina_os/` imports it, and `gpt_service.py` - though still on
disk and still in main.py's `service_class_map` - is absent from `service_order`, so it
never starts. Any doc or comment in this repo referring to "GPT" in the live path is stale.

---

## 1. Architecture Layers: Legacy MVP vs. CantinaOS

### Legacy Architecture (`src/`)

The original MVP implementation uses a tightly-coupled monolithic approach:

- **Event Bus**: Simple `EventBus` with enum-based `EventTypes`
- **Components** (tightly coupled):
  - `VoiceManager`: Handles ASR (Deepgram), LLM (OpenAI), and TTS (ElevenLabs)
  - `StreamManager`: Manages Deepgram streaming connection
  - `LEDManager`: Arduino LED control
  - `MusicManager`: VLC-based music playback
  - `SystemModeManager`: System state management
  - `CommandInputThread`: CLI interface

**Status**: **Dead.** Not started by anything; `main.py` builds CantinaOS only. Retained as
a historical reference. A fix applied here is a fix applied to nothing.

### CantinaOS Architecture (`cantina_os/`)

The new architecture implements strict service decoupling with event-only inter-service communication:

**Key Principles**:
1. **Services**: Independent components that communicate exclusively via events
2. **Event-Driven**: String-based hierarchical topics (e.g., `/audio/transcription/final`)
3. **Pydantic Payloads**: All events carry typed payloads for validation and clarity
4. **ROS-Inspired**: Loosely coupled, replaceable components with clear interfaces

**Active Services**:
- `DeepgramDirectMicService`: Microphone → Deepgram streaming transcription
- `ClaudeService`: Transcription → Claude Haiku/Sonnet → Intent routing (replaces GPTService)
- `GPTService`: Legacy OpenAI LLM (deprecated, ClaudeService preferred)
- `ElevenLabsService`: LLM response → TTS synthesis with streaming playback
- `EyeLightControllerService`: Arduino LED control via serial (face: eyes + mouth)
- `ChestLightControllerService`: second Arduino (`cantina_os/arduino/rex_chest_v1`) for the
  chest logic panels - mirrors the eye states, speech amplitude, music tempo, and shows
  machine status (boot sweep, per-subsystem health windows, fault alarm). Every command is
  also emitted on `CHEST_COMMAND`. Fail-open to mock mode.
- `SimBridgeService`: read-only websocket (127.0.0.1:8765) feeding the 3D digital twin in
  `sim/` (see `sim/README.md`). Fail-open.
- `MusicControllerService`: Music playback with mode-aware behavior and ducking
- `YodaModeManagerService`: System mode transitions (IDLE, AMBIENT, INTERACTIVE)
- `BrainService`: High-level orchestration for DJ mode planning
- `NervousSystemService`: Real-time operational state (sensor readings, runtime context)
- `MemoryService`: Long-term memory (person profiles, event timeline)
- `VisionService`: Scene understanding and continuous face recognition
- `TimelineExecutorService`: Layered timeline execution for coordinated audio sequences
- `CachedSpeechService`: Pre-rendered speech caching for DJ commentary
- `CLIService`: Command-line interface
- `CommandDispatcherService`: Command routing from CLI/voice
- `IntentRouterService`: Intent classification and routing

---

## 2. Event Bus Architecture

### Core Event System (`cantina_os/core/`)

**Event Bus Implementation** (`event_bus.py`):
- Uses `pyee.asyncio.AsyncIOEventEmitter` for async event handling
- Simple interface: `emit(event_name, data)` and `on(event_name, callback)`
- String-based topics instead of enums (allows future flexibility for distributed systems)

**Event Topics** (`event_topics.py`):
- Hierarchical enum-based topics (e.g., `TRANSCRIPTION_FINAL`, `SPEECH_SYNTHESIS_STARTED`)
- Organized by domain: system, audio, transcription, speech, LLM, music, etc.
- Over 150 distinct event types covering the entire system

**Event Payloads** (`event_payloads.py`, `event_schemas.py`):
- **Base Event Payload**: All events inherit from `BaseEventPayload` containing:
  - `timestamp`: Unix timestamp for latency tracking
  - `event_id`: Unique event identifier
  - `conversation_id`: Links related events across an interaction turn
  - `schema_version`: Enables schema evolution
- Domain-specific payloads: `TranscriptionTextPayload`, `LLMResponsePayload`, `SpeechGenerationRequestPayload`, etc.
- Pydantic validation ensures type safety and catches errors early

### Service Communication Pattern

**Primary Pattern (Event-Driven)**:
```
Service A → EventBus.emit(TOPIC, Payload) →
  → EventBus.on(TOPIC, callback) → Service B Handler
```

**Exception Pattern (Synchronous State Queries)**:
```
Service A → memory_service.get_person_profile(name) →
  → Returns profile data immediately (read-only)
```

Services communicate via events by default. Direct method calls are permitted only for **read-only state queries** in performance-critical paths (see Pattern 2 in section 7).

---

## 3. Audio Processing Pipeline

### Complete Flow: Mic → Claude → Speaker

#### Stage 1: Speech Recognition (Mic → Text)

**DeepgramDirectMicService** (`services/deepgram_direct_mic_service.py`):
- Captures microphone audio by driving **PyAudio directly** (there is no Deepgram
  `Microphone` helper in the live service; that was the SDK-4 variant, now dead)
- Streams audio to Deepgram in real-time via WebSocket
- Emits interim transcriptions (`TRANSCRIPTION_INTERIM`) with confidence
- Emits final transcriptions (`TRANSCRIPTION_FINAL`) when voice activity ends
- Events carry `conversation_id` for end-to-end tracking

```
Microphone Audio
    ↓
DeepgramDirectMicService
    ↓ TRANSCRIPTION_INTERIM (real-time)
    ↓ TRANSCRIPTION_FINAL (end-of-utterance)
GPTService (listens to TRANSCRIPTION_FINAL)
```

#### Stage 2: Language Understanding (Text → Intent)

**GPTService** (`services/gpt_service.py`):
- Manages conversation history via `SessionMemory` class
- System prompt defines available tools and personality
- Uses OpenAI function calling to extract structured intents
- Emits `LLM_RESPONSE_TEXT` with generated response
- Emits `INTENT_EXECUTION_RESULT` with parsed tool calls (two-step process for reliability)
- Sentiment analysis on responses (for eye LED color feedback)

```
TRANSCRIPTION_FINAL
    ↓
GPTService (SessionMemory + OpenAI API)
    ↓ LLM_RESPONSE_TEXT
    ↓ LLM_SENTIMENT_ANALYZED
    ↓ INTENT_EXECUTION_RESULT (tool calls)
ElevenLabsService, ToolExecutorService (listen)
```

**SessionMemory** (within GPTService):
- Maintains conversation history as `Message` objects with roles (system, user, assistant, tool)
- Token-based pruning: removes oldest messages when exceeding `max_tokens` limit
- System prompt loaded from DJ persona file for consistent personality

#### Stage 3: Speech Synthesis (Text → Audio)

**ElevenLabsService** (`services/elevenlabs_service.py`):
- Receives `LLM_RESPONSE_TEXT` events
- Calls ElevenLabs TTS API with configurable voice parameters
- Supports streaming playback via `sounddevice` or `system` player
- Emits speech lifecycle events:
  - `SPEECH_SYNTHESIS_STARTED` (with estimated duration)
  - `SPEECH_SYNTHESIS_AMPLITUDE` (for real-time LED pulsing)
  - `SPEECH_SYNTHESIS_ENDED` (for cleanup/transitions)
- Runs audio playback in background thread (doesn't block event loop)

```
LLM_RESPONSE_TEXT
    ↓
ElevenLabsService
    ↓ SPEECH_SYNTHESIS_STARTED
    ↓ SPEECH_SYNTHESIS_AMPLITUDE (100+ events during playback)
    ↓ SPEECH_SYNTHESIS_ENDED
    ↓ (Audio output to speakers)
MusicControllerService, EyeLightControllerService (listen for ducking/visual sync)
```

#### Stage 4: Coordinated Playback (Audio Ducking)

**MusicControllerService** (`services/music_controller_service.py`):
- VLC-based music player with crossfade support
- Listens to `SPEECH_SYNTHESIS_STARTED` → reduces volume to 50%
- Listens to `SPEECH_SYNTHESIS_ENDED` → restores volume
- Supports mode-specific behavior (IDLE plays background, INTERACTIVE is quiet)
- Crossfade for DJ mode transitions (8-second default)

**TimelineExecutorService** (`services/timeline_executor_service/timeline_executor_service.py`):
- Advanced layer-based timeline system with three priority levels: ambient, foreground, override
- Coordinates complex sequences: music fade, speech timing, LED animations
- Executes `DjTransitionPlanPayload` steps synchronously
- Handles audio ducking at precise millisecond intervals

---

## 3b. The Jev Fast Intent Router

Added 2026-09-17 on branch `jev-fast-router`. This is the most important recent change to
the voice loop, and it changes where you should look when a command "does nothing".

### Why

Measured off raw log timestamps, not marketing:

| Interval | Measured |
|---|---|
| Click-stop → music actually playing | **1,899 ms** |
| Click-stop → R3X starts speaking | **~3,745 ms** |
| Event bus + the entire tool-routing chain | **15 ms combined** |

**The bus was never the problem.** 73% of the 1,899 ms was a single Claude round trip whose
only job, for `play_music` / `stop_music` / `set_eye_color`, was to pick a tool name - a
decision that needs no LLM. Worse, tool calls were only extracted after
`stream.get_final_message()`, so the music could not start until Claude had finished talking.

### How it fits

`JevIntentService` (`services/jev_intent_service.py`) subscribes to the same
`VOICE_LISTENING_STOPPED` event as `ClaudeService` and emits **`INTENT_DETECTED`** - the
exact event `ClaudeService` emits after a tool call. `IntentRouterService` already
subscribed to it and `IntentPayload` already carried `confidence`, so **the execution side
needed zero changes.**

```
VOICE_LISTENING_STOPPED { transcript, conversation_id }
   |
   +--> JevIntentService ---- ~190 ms ----> INTENT_DETECTED
   |         (typesafe.ai System One, model pinned to jev-1.13.0)
   |                                              |
   |                                    IntentRouterService
   |                                    CommandDispatcherService
   |                                    MUSIC_COMMAND / EYE_COMMAND / DJ_COMMAND
   |
   +--> ClaudeService --- awaits the router's verdict, then speaks only
```

Files: `llm/jev_client.py` (async httpx client, one pre-warmed connection, `attempts=1`,
800 ms timeout, fails open), `llm/jev_intents.py` (the question catalogue and thresholds),
`services/jev_intent_service.py`, `core/fast_router_gate.py` (the dedup rendezvous).

### The decision rule

Three overlapping reads in **one** request, all of which must agree:

1. a **Choice** over all six tools plus `general_chat` / `unclear` - decides *which*;
2. **one Noul per tool** - an absolute "is the speaker asking for *this*, now?";
3. **`is_a_command`** - "instruction, or conversation?".

The mechanism matters: *"did you turn the music down"* **wins** the Choice competition at
0.94 and **fails** its own Noul at 0.47. The two reads fail independently, and that is what
buys the safety - for one extra request of tokens, not one extra round trip. Over 66
utterances x 5 passes x 8 designs this was the only arm with **0 false triggers and 0 wrong
executions in 330 calls**; every choice-only design false-triggers.

Risk-tiered gates, because one threshold is the wrong model:

| Tier | Tools | Gate |
|---|---|---|
| Free - instantly reversible | `set_eye_animation`, `next_track` | confidence >= 0.75, noul >= 0.5 |
| Cheap - reversible but noticeable | `play_music`, `stop_music`, `dj_mode_on/off` | confidence >= 0.85 **and** noul >= 0.7 |
| Committing - writes shared state | *(empty today)* | never fires from the router alone |

The free tier is derived as `JEV_CONFIDENCE_THRESHOLD - 0.10`, so one knob moves both.

Two deliberate omissions, both from the benchmark: **no `volume_up`/`volume_down`** (R3X has
no volume tool, and they caused the worst confusions - *"quiet please"* → `volume_down`), and
**no conversation history in `state`** (history raised strict accuracy ~4 points but made the
router eager and reintroduced a false trigger; only the assistant's identity line goes in,
which is what fixes *"put on some cantina tunes"* - 0.97 with it, `general_chat` without).

### Dedup - and why Claude does not repeat the action

Both services wake on the same event. Without coordination Claude's own `tool_use` would
restart the music a second time and narrate a future it did not cause.

`ClaudeService` awaits the router's verdict via `core/fast_router_gate.py`, bounded by
`FAST_ROUTER_WAIT_S` (1.2 s). When an action was taken it prepends an
`<action_already_taken>` XML block (same convention as `_build_vision_context_for_message`)
and sets **`tool_choice={"type": "none"}`** - the tools stay in the request so the prompt
cache still hits, but Claude can only speak. `INTENT_CONSUMED` is also emitted for anything
that wants to observe the decision.

Cost: ~190 ms on the *spoken* path, nothing on the *action* path - the music is already
playing before Claude is called.

### Fail-open is the whole design

No `TYPESAFE_API_KEY` → router inactive, no wait at all, every turn takes the Claude path.
Timeout, HTTP error or malformed body → `classify()` returns `None` and never raises into the
voice loop. Router hung → bounded wait, then the turn proceeds exactly as it did before.

A miss costs one slow turn. A false trigger blasts music into the room while someone was
asking a question. **That asymmetry is deliberate**, so *"quiet please"* being declined is
the *correct* failure and not a bug to chase. Every decline logs the full probability map at
INFO - that is your eval set.

### Measured, on the real event bus

| Path | Measured |
|---|---|
| Baseline (Claude tool call) | 1,899 ms |
| Jev router, warm, 5 consecutive turns | **p50 197 ms** (min 160, max 260) |
| Jev router, speculative cache hit | **1.6 ms** |
| — of which all of CantinaOS | **2-3 ms** |

Confirmed again in a full-system run on 2026-09-17 with real VLC: `MUSIC_COMMAND` at 214 ms
(warm), `DJ_COMMAND` 226 ms, `EYE_COMMAND` 316 ms, event loop ticking 147-215 times per turn.

**The ~100 ms target is not reachable on the non-speculative path.** The Jev round trip alone
is p50 ~188 ms, of which ~86 ms is raw network RTT, and CantinaOS contributes 2-3 ms. There
is nothing left to optimise on our side. The speculative path *does* beat it, whenever a
partial transcript matches - the common case for short commands.

### When a voice command "does nothing", check in this order

1. Was it classified? Every decline logs the probability map at INFO.
2. Did `INTENT_DETECTED` fire? If yes, the router did its job.
3. Did the action topic (`MUSIC_COMMAND` / `EYE_COMMAND` / `DJ_COMMAND`) fire?
4. **Did something downstream discard it?** This is the one that bites. Two live examples,
   both found by `scripts/system_smoke_run.py` and invisible to unit tests:
   - eye colour requests dispatched in 209 ms and were then rejected by a CLI arg-count
     check, because the intent was laundered through `CLI_COMMAND` as a two-arg
     `eye pattern <pattern> <color>` while the compound command is registered with
     `max_args=1`. Fixed by emitting `EYE_COMMAND` directly.
   - `next_track` still dispatches correctly and still does nothing outside DJ mode:
     `dj next` is the only skip capability and BrainService refuses it, because
     MusicControllerService understands only "play" and "stop". Open gap.

---

## 4. DJ Mode Coordination

### Multi-Service Orchestration for DJ Auto-Playback

DJ mode enables autonomous track transitions with AI-generated DJ commentary between songs.

#### Key Services & Responsibilities

**BrainService** (`services/brain_service.py`):
- Acts as the "conductor" for DJ mode
- Listens to `TRACK_ENDING_SOON` events from `MusicControllerService`
- Selects the next track using intelligent algorithms (avoid repetition)
- Requests commentary caching via `CachedSpeechService`
- Generates transition plans for `TimelineExecutorService`
- Emits `DJ_MODE_START`, `DJ_MODE_STOP`, `DJ_NEXT_TRACK_SELECTED`
- Maintains recently-played track history to avoid repeats

**MemoryService** (`services/memory_service/memory_service.py`):
- Persistent state store for DJ mode:
  - `dj_mode_active`: Boolean flag
  - `dj_track_history`: List of recently played tracks
  - `dj_current_track`: Current playing track metadata
  - `dj_next_track`: Pre-selected next track
  - `dj_commentary_cache_mappings`: Map of commentary requests to cache keys
  - `dj_commentary_cache_ready`: Ready state of each cached commentary

**CachedSpeechService** (`services/cached_speech_service.py`):
- Pre-generates DJ commentary (introductions for tracks)
- Implements lookahead caching: caches current + next track commentary
- Runs continuous background caching loop
- Emits `SPEECH_CACHE_READY` with playback duration (for timing)
- Enables precise timing synchronization with music crossfades

**TimelineExecutorService**:
- Executes DJ transition "plans":
  - Step 1: Duck music volume
  - Step 2: Play cached DJ commentary
  - Step 3: Crossfade to next track
  - Step 4: Restore music volume
- Waits for speech completion before proceeding to music transition
- Handles interrupts (user "next" command) by pausing/resuming layers

#### DJ Mode Flow

```
TRACK_ENDING_SOON (30 sec before end)
    ↓
BrainService
  - Select next track
  - Request commentary caching
    ↓ DJ_COMMENTARY_REQUEST → CachedSpeechService
    ↓ SPEECH_CACHE_READY (with duration) → MemoryService
  - Generate transition plan
    ↓ PLAN_READY → TimelineExecutorService
    
TimelineExecutorService
  - Pause ambient layer (music)
  - Wait for speech cache to be ready
  - Execute plan: Duck → PlayCachedSpeech → Crossfade → Unduck
    ↓ AUDIO_DUCKING_START → MusicControllerService
    ↓ SPEECH_CACHE_PLAYBACK_REQUEST → CachedSpeechService
    ↓ CROSSFADE_STARTED → MusicControllerService
    ↓ AUDIO_DUCKING_STOP → MusicControllerService
    ↓ PLAN_ENDED

Next track now plays with eyes updated for new mood
```

---

## 5. Hardware Integration

### Arduino LED Control (`EyeLightControllerService`)

**Communication**:
- Serial connection to Arduino (configurable baud rate, default 115200)
- Command protocol: JSON-formatted instructions
- Service automatically detects Arduino port on startup

**LED Pattern Control**:
- Maps sentiment, system mode, and audio events to LED patterns
- Available patterns: IDLE, LISTENING, THINKING, SPEAKING, HAPPY, SAD, ANGRY, ERROR
- Subscribes to:
  - `LLM_SENTIMENT_ANALYZED`: Sets base color/mood (positive=green, negative=red, etc.)
  - `SPEECH_SYNTHESIS_STARTED`: Triggers speaking animation
  - `SPEECH_SYNTHESIS_AMPLITUDE`: Pulses brightness in sync with speech volume
  - `SYSTEM_MODE_CHANGED`: Updates ambient animation based on mode
  - CLI eye commands: Direct pattern override

**Architecture Pattern**:
- `SimpleEyeAdapter` encapsulates Arduino protocol
- Serial communication runs in background to avoid blocking
- Handles disconnections gracefully with retry logic

---

## 6. Service Lifecycle & Startup

### Service Registration & Initialization (`main.py`)

**CantinaOS Class**:
1. Creates `AsyncIOEventEmitter` event bus
2. Instantiates all services in dependency order
3. Calls `await service.start()` for each service
4. Subscribes services to relevant events during startup

**Service Initialization Pattern**:

```python
# Example service pattern
class ExampleService(BaseService):
    def __init__(self, event_bus, config=None):
        super().__init__(service_name="example", event_bus=event_bus)
        self._config = config or {}
    
    async def _start(self):
        # Subscribe to events
        self._event_bus.on(EventTopics.SOME_EVENT, self._handle_event)
        # Initialize resources
        await self._setup_subscriptions()
        # Register with memory service if needed
        
    async def _stop(self):
        # Unsubscribe and cleanup
        pass
```

**Startup Order** (approximate):
1. YodaModeManagerService (system mode state)
2. ModeCommandHandlerService (mode transition commands)
3. CommandDispatcherService (command routing)
4. NervousSystemService (real-time operational state)
5. MemoryService (long-term memory - person profiles)
6. LatencyTrackerService (pipeline performance monitoring)
7. VisionService (scene understanding and face recognition)
8. MouseInputService (click-based recording control)
9. DeepgramDirectMicService (audio input)
10. ClaudeService (LLM processing with Claude Haiku/Sonnet)
11. IntentRouterService (intent classification)
12. BrainService (DJ orchestration)
13. TimelineExecutorService (layered plan execution)
14. ElevenLabsService (TTS synthesis)
15. CachedSpeechService (DJ commentary caching)
16. ModeChangeSoundService (mode transition audio)
17. MusicControllerService (music playback)
18. EyeLightControllerService (LED control)
19. DebugService (LLM logging)
20. CLIService (user interface)

**Shutdown** (reverse order with graceful cleanup)

---

## 7. Architecture Analogy

> **Restaurant Kitchen Analogy**: To understand the event-driven architecture intuitively, see the **restaurant kitchen analogy** in `cantina_os/docs/CANTINA_OS_SYSTEM_ARCHITECTURE.md` Section 1.4. This analogy maps services to kitchen stations, the event bus to order tickets, and direct state queries to reading gauges/thermometers.
>
> **Quick Summary**: Event bus = order tickets (default communication), Direct references = glancing at thermometers (read-only state queries)

---

## 8. Critical Architectural Patterns

### Pattern 1: Conversation ID Propagation

All events related to a single user utterance carry the same `conversation_id`. This enables:
- Tracking interaction latency end-to-end
- Preventing stale events from old conversations affecting current interaction
- Debugging and performance analysis

**The capture service owns the turn id.** `DeepgramDirectMicService._handle_mic_recording_start`
mints a uuid per turn (and `CLIService`'s `record` command does the same on the typed path),
puts it on `VOICE_LISTENING_STARTED`, and carries it on `VOICE_LISTENING_STOPPED`. Everything
downstream **adopts** it - `ClaudeService._handle_voice_transcript` takes the incoming id
rather than keeping its own.

```
VOICE_LISTENING_STARTED  (conversation_id: "abc123")   <- minted here, opens the latency record
  → TRANSCRIPTION_FINAL  (same)
  → VOICE_LISTENING_STOPPED (same)
  → INTENT_DETECTED / LLM_RESPONSE (same)
  → SPEECH_GENERATION_STARTED / _COMPLETE (same)       <- closes the latency record
  → LED updates and CLI display ignore other ids
```

**This was broken until 2026-09-17, and it is worth knowing how.** The capture services threw
the id away on `VOICE_LISTENING_STOPPED` (emitting `{"transcript": ...}`, or a bare `{}` on the
CLI path) and `ClaudeService` minted its own in `reset_conversation()`. Every handler in
`LatencyTrackerService` guards on `conversation_id not in self._conversation_metrics`, so it
dropped **100% of its measurements** and had never recorded anything. `CLIService._handle_llm_response`
has the same guard, so the CLI was also silently swallowing R3X's replies - which nobody had
connected to a latency bug. Note the failure mode: two ids that never match produce **silence**,
not an error. Assume nothing about a metric you have not seen a number for.

Also fixed at the same time: `MemoryService` and `LatencyTrackerService` both subscribed to
`LLM_RESPONSE_TEXT`, `SPEECH_SYNTHESIS_STARTED` and `SPEECH_SYNTHESIS_ENDED` - **none of which
any service emits.** The live topics are `LLM_RESPONSE`, `SPEECH_GENERATION_STARTED` and
`SPEECH_GENERATION_COMPLETE`. MemoryService had therefore been recording what Brandon said and
never what R3X replied, building every person summary from a one-sided transcript. Subscribing
to a topic nothing emits is silent; grep for an emitter before trusting a subscription.

### Pattern 2: Event-Driven Communication (With Exceptions)

**Primary Rule**: Services communicate via **event bus by default** for loose coupling and scalability.

**Exception: Synchronous State Queries**

Direct service references are permitted for **read-only state queries** when:

1. **Performance-critical**: Operation blocks critical path (e.g., LLM message preparation)
2. **Synchronous context**: Caller needs immediate result within same async operation
3. **Single dependency**: 1-to-1 relationship (not broadcast)
4. **Read-only**: No state mutations, idempotent queries only

**Approved Patterns**:
```python
# ✅ ALLOWED - Read-only state query (synchronous dependency)
current_mode = self._mode_manager.current_mode  # Property access
profile = await self._memory_service.get_person_profile(name)  # Read-only query
state = self._nervous_system.get(key)  # State lookup via helper

# ❌ PROHIBITED - State mutation via direct call
music_service.set_volume(50)  # Use AUDIO_DUCKING_START event instead

# ✅ CORRECT - Event-based for mutations/actions
event_bus.emit(EventTopics.AUDIO_DUCKING_START,
               AudioDuckingPayload(target_volume=50))
```

**Requirements for Direct Service References**:
- Service passed during initialization (not lazy lookup)
- Only read operations or idempotent queries
- Documented as "state query service" dependency
- Graceful fallback if service unavailable

**Examples in Codebase**:
- `ModeCommandHandlerService` → `YodaModeManagerService` (mode queries)
- `ClaudeService` → `MemoryService` (person profile lookups)

**Event Payload Standards**:

All events **MUST use Pydantic payloads** for type safety and validation:

```python
# ❌ WRONG - Raw dict emission
self._event_bus.emit(EventTopics.VISION_PERSON_DETECTED, {
    "name": person_name,
    "confidence": confidence
})

# ✅ CORRECT - Pydantic payload
from cantina_os.core.event_payloads import VisionPersonDetectedPayload

payload = VisionPersonDetectedPayload(
    name=person_name,
    confidence=confidence
)
self._event_bus.emit(
    EventTopics.VISION_PERSON_DETECTED,
    payload.model_dump()
)
```

**Payload Requirements**:
- Inherit from `BaseEventPayload` when possible
- Include `timestamp`, `event_id`, `conversation_id` where applicable
- Always emit via `payload.model_dump()` to convert to dict
- No raw dicts unless legacy compatibility required

### Pattern 3: Two-Step Tool Execution

GPT responses with tool calls go through two steps:

1. **LLM_RESPONSE_TEXT**: The full response from GPT (includes tool call info)
2. **INTENT_EXECUTION_RESULT**: After tools execute, GPT gets tool results and generates final user response

This prevents tools from being called before TTS and ensures coherent verbal feedback.

### Pattern 4: Mode-Specific Behavior

`YodaModeManagerService` emits `SYSTEM_MODE_CHANGED` events:
- **STARTUP**: Initial boot sequence, self-checks
- **IDLE**: No user interaction, background ambient mode
- **AMBIENT**: Pre-scripted animations/music
- **INTERACTIVE**: Voice conversation mode (listening/processing/speaking states)

Services adapt behavior based on current mode.

### Pattern 5: Layered Timeline Execution

`TimelineExecutorService` manages three priority layers:
- **Ambient** (priority 0): Background music, ambient animations
- **Foreground** (priority 1): User-initiated responses, DJ commentary
- **Override** (priority 2): Critical alerts, system messages

Higher-priority layers pause lower layers (e.g., DJ commentary pauses background music).

---

## 9. Configuration & Environment

### 9a. Which LLM backend the Claude path uses

`cantina_os/llm/anthropic_provider.py` is the single place this is decided, and all three
Anthropic clients go through it - `ClaudeService`, `MemoryService`'s summariser, and
`VisionService`.

| Credential present | Result |
|---|---|
| `ANTHROPIC_API_KEY` | direct to Anthropic; **always wins** |
| `OPENROUTER_API_KEY` only | the same `anthropic` SDK with `base_url="https://openrouter.ai/api"` |
| neither | `resolve_provider()` returns `None`; the service treats the LLM as unavailable |

`LLM_PROVIDER` (`anthropic`/`openrouter`/`auto`) forces the choice. A forced provider whose
key is missing is **unavailable**, not a redirect to the other one - silent cross-provider
fallback would make a misconfiguration look like a working system on someone else's bill.
`ANTHROPIC_BASE_URL` overrides the host for whichever provider was chosen.

Why it works at all: OpenRouter serves the Anthropic Messages format, and the SDK sends
`x-api-key` + `anthropic-version` to `{base_url}/v1/messages` regardless of host. Streaming,
`temperature`, `tools` and `tool_choice: {"type": "none"}` all behave identically - measured
by `cantina_os/scripts/claude_live_verify.py`, not assumed.

**Two traps.**

1. The base URL is `https://openrouter.ai/api`, *not* `.../api/v1`. The SDK appends its own
   `/v1`; the doubled prefix returns an HTML 404 that surfaces as `NotFoundError` with a
   page of Next.js markup in the message.
2. Model ids differ (`claude-haiku-4-5-20251001` vs `anthropic/claude-haiku-4.5`). They are
   translated once, at client construction, and written back into `_config["MODEL"]` - so
   every call site keeps using the Anthropic id and `CLAUDE_MODEL` stays portable. Add new
   models to `OPENROUTER_MODEL_MAP`; an unmapped id passes through unchanged rather than
   raising.

`anthropic` is pinned `<1.0` because 1.x removed `temperature` from `messages.create()` and
seven call sites pass it. That pin is unrelated to the provider choice.

### Configuration Sources

1. **Environment Variables** (`.env` file):
   - API keys: `ANTHROPIC_API_KEY` **or** `OPENROUTER_API_KEY` (see below),
     `ELEVENLABS_API_KEY`, `DEEPGRAM_API_KEY`, `TYPESAFE_API_KEY` (the Jev fast intent
     router; optional - absent means the router is simply inactive)
   - LLM provider: `LLM_PROVIDER` (`anthropic`/`openrouter`/`auto`), `ANTHROPIC_BASE_URL`
   - Fast router: `JEV_ROUTER_ENABLED`, `JEV_CONFIDENCE_THRESHOLD` (0.85),
     `JEV_COMMAND_THRESHOLD` (0.5), `JEV_TIMEOUT_S` (0.8), `JEV_SPECULATE`,
     `FAST_ROUTER_WAIT_S` (1.2)
   - Hardware: `ARDUINO_SERIAL_PORT`, `ARDUINO_BAUD_RATE`, `FORCE_MOCK_LED_CONTROLLER`
   - Chest board: `CHEST_SERIAL_PORT` (set it, and `ARDUINO_SERIAL_PORT`, whenever both
     Arduinos are plugged in - the face auto-detect takes the first Arduino it sees),
     `CHEST_ENABLED`, `FORCE_MOCK_CHEST`, `CHEST_DEFAULT_BPM` (120), `CHEST_FAULT_HOLD_S` (60)
   - 3D sim link: `SIM_BRIDGE_ENABLED`, `SIM_BRIDGE_HOST`, `SIM_BRIDGE_PORT`
   - Launching CantinaOS from a Claude Code shell: that shell exports `ANTHROPIC_BASE_URL`,
     which the provider honours - with an OpenRouter key it then 401s. `unset ANTHROPIC_BASE_URL`
     first (this is why `system_smoke_run.py`'s pure-chat turn fails when run from an agent).
   - Music: `MUSIC_DEFAULT_SOURCE`, `ENABLE_SPOTIFY`, `SPOTIFY_CLIENT_ID`,
     `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`

   Note: `ARDUINO_SERIAL_PORT` **overrides** the `serial_port` passed to
   `EyeLightControllerService` in code (`eye_light_controller_service.py:258-266`). Anything
   that calls `load_dotenv()` therefore changes that service's behaviour process-wide - which
   is a real test-isolation hazard, since `DeepgramDirectMicService.__init__` does exactly
   that at `deepgram_direct_mic_service.py:59`.

2. **Service-Level Config** (passed during initialization):
   - Pydantic models validate all configuration
   - Defaults provided for optional settings
   - Example: `MusicControllerConfig` with `normal_volume`, `ducking_volume`, `crossfade_duration`

3. **Mode Personas** (text files):
   - `dj_r3x-transition-persona.txt`: DJ commentary style
   - `dj_r3x-verbal-feedback-persona.txt`: Verbal tool execution feedback

---

## 10. Testing Strategy

### Running the suite

```bash
cd cantina_os
../venv/bin/python -m pytest tests/ -q                            # 274 passed, 55 skipped
../venv/bin/python -m pytest tests/test_jev_intent_router.py -q    # fast, offline, no API
```

**Current state as of 2026-09-17: 274 passed, 55 skipped, 0 failed.** For most of 2026 this
was `0 passed, 144 errors` - `tests/conftest.py` patched `deepgram.Deepgram`, a symbol
removed in deepgram-sdk 4.x, inside an `autouse=True` fixture, so setup raised
`AttributeError` for every test in the suite. If you ever see the whole suite error at
setup, suspect the autouse fixture before anything else.

Every one of the 55 skips carries a written reason naming the production code that changed.
Do not "fix" a skip by deleting the reason. The groups are: dead services (`gpt_service`,
the SDK-4 deepgram service, the flat `music_controller_service.py` shadowed by its package),
hardware-bound tests, tests needing real API credit, and a handful pinned open against
production defects that should be fixed rather than asserted.

### Two traps that have wasted real time

**1. The event bus's `emit`/`on` are SYNCHRONOUS.** `main.py:162` builds a
`pyee.AsyncIOEventEmitter`, whose `emit()` returns `bool` and schedules coroutine handlers
on the loop. `BaseService.emit()`/`subscribe()` (`base_service.py:247,269`) therefore call
them **without** `await`, which is correct. Consequences:
- Never write `await bus.emit(...)` in a test: it raises
  `TypeError: object bool can't be used in 'await' expression`.
- Never give a mock bus an `async def emit`: the unawaited coroutine never runs its body, so
  the test silently observes nothing and passes for the wrong reason. This pattern was
  responsible for dozens of the 2026 test failures.
- `cantina_os/event_bus.py`'s `EventBus` class declares these as `async def`. It is a live
  landmine for anything wired to `EventBus()` rather than the pyee bus.

**2. `BaseService._emit_status` emits the literal string `"service_status"`**
(`base_service.py:155`), not `EventTopics.SERVICE_STATUS_UPDATE` (`"service.status.update"`).
Subscribing to the constant will not see lifecycle status events.

### Driving the real system without hardware

`cantina_os/scripts/system_smoke_run.py` boots a real `CantinaOS` - real bus, real
IntentRouterService, CommandDispatcherService, BrainService and MusicControllerService
driving real VLC - mocks the Arduino, skips the three services that need hardware and OS
permissions (mic capture, mouse trigger, vision), and injects transcripts as
`VOICE_LISTENING_STARTED` / `VOICE_LISTENING_STOPPED` exactly as the capture services emit
them. It prints every bus event per turn with millisecond offsets plus an event-loop tick
count, so a freeze shows up as a tick count near zero.

```bash
cd cantina_os
../venv/bin/python scripts/system_smoke_run.py
```

Prefer this over unit tests when the question is "does this actually work on this box". It
is what caught the eye-colour routing bug and the `next_track` gap on 2026-09-17; both were
invisible to unit tests, because both dispatched correctly and were then discarded
downstream.

### Integration Testing

- Full audio pipeline tests (transcription → LLM → TTS)
- DJ mode transition tests (music crossfades, commentary caching)
- Hardware communication tests (Arduino serial protocol)
- Mode transition tests (IDLE → INTERACTIVE → IDLE)

### Performance Monitoring

- Event timestamps enable latency measurement across the pipeline
- Target latencies:
  - Transcription recognition: < 500ms
  - LLM response: < 2s
  - TTS generation: < 3s
  - Total turn-around: < 5s for interactive feeling

### Claude Code Testing Methodology

**CRITICAL**: When testing DJ R3X as Claude Code, always use the correct environment:

**Correct Way** (using virtual environment):
```bash
cd "/Users/brandoncullum/DJ-R3X Voice/cantina_os"
../venv/bin/python -m cantina_os.main

# For automated testing with commands:
echo -e "list music\nquit" | ../venv/bin/python -m cantina_os.main

# For background testing:
echo -e "play music 1\nquit" | ../venv/bin/python -m cantina_os.main 2>&1 &
```

**WRONG Way** (using system Python):
```bash
python -m cantina_os.main  # ❌ Will fail with missing dependencies
python3 -m cantina_os.main # ❌ Will use system Python, not venv
```

**Testing Patterns**:
1. **Startup Verification**: Check logs for service initialization errors
2. **Command Testing**: Pipe commands via stdin to test CLI without interaction
3. **Background Execution**: Use `run_in_background: true` for long-running tests
4. **Isolation Tests**: Create small Python scripts to test specific components (e.g., VLC, pynput)
5. **Log Analysis**: Use `grep`, `head`, `tail` to filter relevant log output
6. **Process Management**: Kill background processes after testing completes

**Environment Requirements**:
- Virtual environment at `/Users/brandoncullum/DJ-R3X Voice/venv/`
- All dependencies installed via `pip install -r requirements.txt`
- API keys loaded from `.env` file in project root (ANTHROPIC_API_KEY *or* OPENROUTER_API_KEY, ELEVENLABS_API_KEY, DEEPGRAM_API_KEY)
- Running from `cantina_os/` directory for correct module resolution
- **Terminal**: Use Terminal.app, NOT Warp (Warp has known issues with macOS Accessibility permissions for mouse input via pynput)

**CRITICAL: Avoid False Positives in Testing**

When asked to test a service "fully", this MUST include:
- ✅ Unit tests with mocks (verifies internal logic)
- ✅ Integration tests (verifies service interactions)
- ✅ **End-to-End tests with REAL API keys** (verifies actual integration works)

**Do NOT claim "fully tested" if you only run unit/integration tests with mocked APIs.** This misses:
- API authentication failures (bad/missing keys)
- Request format mismatches (sending wrong data structure)
- Response parsing errors (API returns different format)
- Rate limiting, timeouts, network errors
- Actual service behavior and latency

Example of inadequate testing:
```python
# ❌ This doesn't test ANYTHING real
service = ClaudeService(event_bus=MockEventBus())
service.register_tool(mock_tool)
assert mock_tool in service.tools  # ✓ Passes, but doesn't call Claude API
```

Example of PROPER end-to-end testing:
```python
# ✅ This actually verifies the service works
import os
anthropic_key = os.getenv("ANTHROPIC_API_KEY")
assert anthropic_key, "ANTHROPIC_API_KEY not found in .env"

service = ClaudeService(event_bus=real_event_bus)
await service.start()
# Send real transcription → verify Claude API responds correctly
# Send real tool calls → verify Claude executes them
```

---

## 11. Known Architectural Decisions & Trade-offs

### Decision 1: Deepgram Direct Microphone
- **Choice**: Use Deepgram's built-in `Microphone` class instead of separate `MicInputService`
- **Rationale**: Reduces complexity, single service owns mic→transcription pipeline
- **Trade-off**: Less modular (can't swap ASR providers without service refactor)

### Decision 2: SessionMemory in GPTService
- **Choice**: Conversation history managed internally rather than MemoryService
- **Rationale**: GPT-specific needs (token counting, prompt building)
- **Trade-off**: Can't easily share conversation context with other LLM providers

### Decision 3: VLC for Music Playback
- **Choice**: Use VLC library instead of system audio APIs
- **Rationale**: Cross-platform, reliable, good track metadata support
- **Trade-off**: VLC verbose logging requires suppression; heavier than minimal solutions

### Decision 4: Streaming Speech Playback
- **Choice**: ElevenLabs service uses streaming + `sounddevice` instead of file-based
- **Rationale**: Lower latency, no disk I/O, real-time amplitude feedback for LED sync
- **Trade-off**: More complex threading, requires careful resource cleanup

### Decision 5: Timeline Executor for DJ Coordination
- **Choice**: Purpose-built service for complex plan execution instead of ad-hoc coordination
- **Rationale**: Handles interrupts, layering, precise timing
- **Trade-off**: Added complexity, harder to understand at first glance

---

## 12. Migration Notes: src/ → cantina_os/

### What's Being Phased Out (`src/`)

- `VoiceManager`: Split into `DeepgramDirectMicService`, `GPTService`, `ElevenLabsService`
- `StreamManager`: Merged into `DeepgramDirectMicService`
- `LEDManager`: Refactored as `EyeLightControllerService`
- `MusicManager`: Refactored as `MusicControllerService`
- `SystemModeManager`: Refactored as `YodaModeManagerService`
- Flat `EventTypes` enum: Replaced with hierarchical `EventTopics` enum
- Direct method calls: Replaced with event-based communication

### Migration Benefits

1. **Testability**: Each service can be tested in isolation
2. **Maintainability**: Clear responsibilities, single-purpose services
3. **Extensibility**: New services can be added without modifying existing ones
4. **Distributability**: Event API allows future migration to distributed systems (Redis, ZeroMQ)
5. **Debuggability**: Event logs provide full interaction traces

### Current Status

- CantinaOS architecture is **primary** (fully functional)
- Legacy `src/` code remains for reference only
- All new features developed in `cantina_os/`
- No active migration of old code needed

---

## 13. Key Files Reference

### Core Architecture
- `cantina_os/core/event_bus.py`: Event bus implementation
- `cantina_os/core/event_topics.py`: Event topic definitions (150+ topics)
- `cantina_os/core/event_payloads.py`: Pydantic payload models
- `cantina_os/base_service.py`: Base class for all services

### Critical Services
- `cantina_os/services/deepgram_direct_mic_service.py`: Speech recognition
- `cantina_os/services/gpt_service.py`: LLM processing & intent extraction
- `cantina_os/services/elevenlabs_service.py`: Speech synthesis
- `cantina_os/services/music_controller_service.py`: Music playback & ducking
- `cantina_os/services/eye_light_controller_service.py`: Arduino LED control
- `cantina_os/services/brain_service.py`: DJ mode orchestration
- `cantina_os/services/memory_service/memory_service.py`: State persistence
- `cantina_os/services/timeline_executor_service/timeline_executor_service.py`: Plan execution
- `cantina_os/services/cached_speech_service.py`: DJ commentary caching

### Entry Points & Configuration
- `cantina_os/main.py`: Service initialization and lifecycle
- `config/`: Environment and feature configuration
- `.env`: API keys and hardware settings

### Testing Strategy

Three tiers of testing required for complete coverage:

**1. Unit Tests** (`cantina_os/tests/unit/`)
- Test individual service logic in isolation using mocks
- Verify internal state management, message handling, event emission
- Use `MockEventBus`, mocked API clients, mocked hardware
- Fast execution, no external dependencies needed
- Example: Test SessionMemory token pruning without calling Claude API

**2. Integration Tests** (`cantina_os/tests/integration/`)
- Test services together without external APIs (e.g., service A → event → service B)
- Use real service classes but mock external API calls
- Verify event propagation, subscription patterns, state transitions
- Should work without API keys

**3. End-to-End Tests** (real API calls)
- **CRITICAL**: Test against real external APIs (Anthropic, ElevenLabs, Deepgram)
- Requires valid API keys in `.env` (ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, DEEPGRAM_API_KEY)
- Verifies actual service behavior: API connectivity, response parsing, error handling
- Must be run before declaring "EVERYTHING is working"
- Tests should verify:
  - Service can connect to actual API
  - Request formatting is correct
  - Response parsing matches expectations
  - Streaming works if enabled
  - Tool calls execute with real API
  - Error handling for actual API errors

**What "fully tested" means**:
- ✅ All unit tests pass (mocked)
- ✅ All integration tests pass (service-to-service, mocked APIs)
- ✅ E2E tests pass with real API keys (actual external service calls)

**Common mistake to avoid**:
- Mocking everything and claiming "EVERYTHING works" without real API validation
- Not testing with actual API keys = missing critical integration failures

---

## 14. Future Architecture Considerations

### Planned Extensions
1. **Distributed Event Bus**: Swap `pyee` for Redis Pub/Sub for multi-machine deployment
2. **Vector Memory**: Long-term semantic memory for conversation context
3. **Web Interface**: Real-time dashboard showing service health, event flows
4. **Advanced Logging**: Structured JSON logs for analytics
5. **Configuration Service**: Centralized config with runtime updates
6. **Health Monitor Service**: Aggregated service health reporting

### Current Tech Debt
- Some services have complex internal state (simplify with clearer state separation)
- Error handling could be more uniform (standardize error event types)
- Performance metrics collection could be more comprehensive

---

## 15. How to Extend the System

### Adding a New Service

1. Create service class inheriting from `BaseService`
2. Define event topics service cares about in `event_topics.py`
3. Create Pydantic payloads for service's events
4. Implement `async _start()` to subscribe to events
5. Add service to initialization list in `main.py`
6. Write unit tests with mock event bus
7. Add integration test with dependent services

### Adding a New Command

1. Define command in CLI/voice intent routing
2. Emit event from `CommandDispatcherService` or `IntentRouterService`
3. Have target service listen and handle
4. Emit result event back
5. Example: "music next" → emits `DJ_NEXT_TRACK` → BrainService handles → selects track

---

**This document reflects the architecture as of the latest commits. Refer to git history for evolution details.**
