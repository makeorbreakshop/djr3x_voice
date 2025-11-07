# DJ R3X Voice - Architecture Overview

## Executive Summary

DJ R3X is undergoing a major architectural transition from a legacy MVP design (`src/`) to the new **CantinaOS** framework (`cantina_os/`). The system is an AI-powered voice-interactive DJ robot that processes user speech, generates intelligent responses, and controls music playback with synchronized LED animations. The architecture emphasizes event-driven decoupling, ROS-inspired service patterns, and precise audio pipeline coordination.

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

**Status**: Still functional, serves as reference but being phased out.

### CantinaOS Architecture (`cantina_os/`)

The new architecture implements strict service decoupling with event-only inter-service communication:

**Key Principles**:
1. **Services**: Independent components that communicate exclusively via events
2. **Event-Driven**: String-based hierarchical topics (e.g., `/audio/transcription/final`)
3. **Pydantic Payloads**: All events carry typed payloads for validation and clarity
4. **ROS-Inspired**: Loosely coupled, replaceable components with clear interfaces

**Active Services**:
- `DeepgramDirectMicService`: Microphone → Deepgram streaming transcription
- `GPTService`: Transcription → OpenAI LLM → Intent routing
- `ElevenLabsService`: LLM response → TTS synthesis with streaming playback
- `EyeLightControllerService`: Arduino LED control via serial
- `MusicControllerService`: Music playback with mode-aware behavior and ducking
- `YodaModeManagerService`: System mode transitions (IDLE, AMBIENT, INTERACTIVE)
- `BrainService`: High-level orchestration for DJ mode planning
- `MemoryService`: Persistent state store for system context
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

```
Service A → EventBus.emit(TOPIC, Payload) → 
  → EventBus.on(TOPIC, callback) → Service B Handler
```

Services never directly call each other—all communication flows through events.

---

## 3. Audio Processing Pipeline

### Complete Flow: Mic → GPT → Speaker

#### Stage 1: Speech Recognition (Mic → Text)

**DeepgramDirectMicService** (`services/deepgram_direct_mic_service.py`):
- Captures microphone audio using Deepgram's `Microphone` class
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
2. MemoryService (global state store)
3. DeepgramDirectMicService (audio input)
4. GPTService (LLM processing)
5. ElevenLabsService (TTS)
6. EyeLightControllerService (LED control)
7. MusicControllerService (music playback)
8. CLIService (user interface)
9. BrainService (DJ orchestration)
10. CachedSpeechService (DJ commentary caching)
11. TimelineExecutorService (complex timing coordination)

**Shutdown** (reverse order with graceful cleanup)

---

## 7. Critical Architectural Patterns

### Pattern 1: Conversation ID Propagation

All events related to a single user utterance carry the same `conversation_id`. This enables:
- Tracking interaction latency end-to-end
- Preventing stale events from old conversations affecting current interaction
- Debugging and performance analysis

```
User speaks → TRANSCRIPTION_FINAL (conversation_id: "abc123")
  → LLM_RESPONSE_TEXT (same conversation_id)
  → SPEECH_SYNTHESIS_STARTED (same conversation_id)
  → LED updates ignore old conversation_id events
```

### Pattern 2: Event-Only Communication

**Strict Rule**: Services NEVER directly call methods on other services.

```python
# ❌ WRONG - Direct coupling
music_service.set_volume(50)

# ✅ CORRECT - Event-based
event_bus.emit(EventTopics.AUDIO_DUCKING_START, 
               AudioDuckingPayload(target_volume=50))
```

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

## 8. Configuration & Environment

### Configuration Sources

1. **Environment Variables** (`.env` file):
   - API keys: `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `DEEPGRAM_API_KEY`
   - Hardware: `LED_SERIAL_PORT`, `LED_BAUD_RATE`
   - Audio: `SAMPLE_RATE`, `CHANNELS`

2. **Service-Level Config** (passed during initialization):
   - Pydantic models validate all configuration
   - Defaults provided for optional settings
   - Example: `MusicControllerConfig` with `normal_volume`, `ducking_volume`, `crossfade_duration`

3. **Mode Personas** (text files):
   - `dj_r3x-transition-persona.txt`: DJ commentary style
   - `dj_r3x-verbal-feedback-persona.txt`: Verbal tool execution feedback

---

## 9. Testing Strategy

### Service Isolation

All services can be instantiated with mock components:
- `MockDeepgramService`: Returns pre-scripted transcriptions
- `MockElevenLabsService`: Skips actual TTS API calls
- `MockMusicControllerService`: Simulates playback events
- Mock event bus for unit testing individual services

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
- API keys loaded from `.env` file in project root (ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, DEEPGRAM_API_KEY)
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

## 10. Known Architectural Decisions & Trade-offs

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

## 11. Migration Notes: src/ → cantina_os/

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

## 12. Key Files Reference

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

## 13. Future Architecture Considerations

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

## 14. How to Extend the System

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
