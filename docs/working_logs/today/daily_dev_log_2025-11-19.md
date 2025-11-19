# Daily Dev Log - November 19, 2025

## Session 1: Wake Word Detection Setup & Planning

### Accomplishments

#### 1. Porcupine Wake Word Test Implementation ✅

**What We Built:**
- Created standalone test script for Porcupine wake word detection
- Successfully tested "DJ Rex" wake word with custom `.ppn` keyword file
- Used existing PyAudio library (already installed for Deepgram) instead of adding new dependencies

**Files Created:**
- `test_wake_word.py` - Standalone test script using PyAudio
- `cantina_os/wake_word/DJ-Rex_en_mac_v3_0_0.ppn` - Custom Porcupine keyword file
- `WAKE_WORD_TEST_README.md` - Complete setup and usage instructions

**Key Technical Decisions:**
- ✅ Used PyAudio (already installed) instead of pvrecorder
- ✅ Loads from `.env` file using python-dotenv
- ✅ Standalone test - NOT integrated into CantinaOS yet
- ✅ Requires PICOVOICE_ACCESS_KEY from Picovoice Console

**Testing Results:**
- Wake word detection works successfully
- Script runs from project root: `venv/bin/python test_wake_word.py`
- Detects "DJ Rex" spoken at conversational distance

**Git Commit:**
```
feat: Add Porcupine wake word detection test for "DJ Rex"
Commit: 82b44b4
```

---

### Architecture Analysis: Current System

Before implementing wake word integration, we analyzed the existing system architecture to understand integration points.

#### Current Event Flow (Mouse-Based Click-to-Talk)

```
1. User types "engage"
   └→ YodaModeManagerService: IDLE → INTERACTIVE
   └→ DeepgramDirectMicService: Opens WebSocket (persistent)
   └→ KeepAlive loop starts (5-second interval)

2. User clicks mouse (start recording)
   └→ MouseInputService: Emits MIC_RECORDING_START
   └→ DeepgramDirectMicService: Starts PyAudio stream
   └→ Audio chunks sent to open WebSocket
   └→ Deepgram processes real-time

3. User clicks mouse (stop recording)
   └→ MouseInputService: Emits MIC_RECORDING_STOP
   └→ DeepgramDirectMicService: Stops PyAudio stream
   └→ WebSocket stays open (KeepAlive continues)
   └→ TRANSCRIPTION_FINAL emitted

4. User types "disengage"
   └→ YodaModeManagerService: INTERACTIVE → IDLE
   └→ DeepgramDirectMicService: Closes WebSocket
```

#### Key Architecture Insights

**WebSocket Lifecycle:**
- **Persistent per-session**, not per-utterance
- Opens when entering INTERACTIVE mode
- Stays open across multiple mouse-click utterances
- KeepAlive messages every 5 seconds prevent timeout
- Closes when leaving INTERACTIVE mode

**Audio Pipeline:**
- PyAudio → Direct streaming → Deepgram WebSocket
- **No existing buffering** (chunks sent immediately)
- Sample rate: 16,000 Hz, mono, linear16 PCM
- Chunk size: 8,000 bytes (~250ms of audio)

**Mode Transitions:**
- Managed by `YodaModeManagerService`
- Triggered by CLI commands (`engage`, `disengage`, etc.)
- Events: `MODE_TRANSITION_STARTED` → `SYSTEM_MODE_CHANGE` → `MODE_TRANSITION_COMPLETE`

**Mouse Input:**
- `MouseInputService` toggles recording state
- Only active in INTERACTIVE mode
- Dashboard-aware (disables if web bridge is active)
- Events: `MIC_RECORDING_START`, `MIC_RECORDING_STOP`

#### Relevant Services & Files

| Service | File Path | Responsibility |
|---------|-----------|----------------|
| YodaModeManagerService | `cantina_os/services/yoda_mode_manager_service.py` | Mode state management (IDLE, AMBIENT, INTERACTIVE) |
| ModeCommandHandlerService | `cantina_os/services/mode_command_handler_service.py` | Handles mode transition commands |
| MouseInputService | `cantina_os/services/mouse_input_service.py` | Detects mouse clicks, emits recording events |
| DeepgramDirectMicService | `cantina_os/services/deepgram_direct_mic_service.py` | Audio capture, Deepgram streaming, WebSocket lifecycle |
| Event Topics | `cantina_os/core/event_topics.py` | All event topic definitions |
| Event Payloads | `cantina_os/core/event_payloads.py` | Pydantic payload models |

---

## Full Implementation Plan: Wake Word + Flux VAD

### Project Phases (Revised Priority Order)

**Phase 1: Wake Word Detection** (First - Foundation)
**Phase 2: Deepgram Flux VAD Integration** (Second - Auto Turn Detection)
**Phase 3: Interruption Handling** (Third - Advanced Feature)

### Phase 1: Wake Word Detection (Priority 1)

**Goal:** Replace manual "engage" command with "Hey DJ Rex" wake word detection.

#### Implementation Tasks

##### 1.1: Create PorcupineWakeWordService

**File:** `cantina_os/services/porcupine_wake_word_service.py`

**Responsibilities:**
- Run Porcupine wake word detection in background thread
- Buffer last 2 seconds of audio (pre-roll for Deepgram)
- Emit `WAKE_WORD_DETECTED` event with pre-roll buffer
- Auto-pause during INTERACTIVE mode (no need to detect wake word while already engaged)
- Resume during AMBIENT/IDLE modes

**Key State:**
```python
self._audio_buffer = deque(maxlen=32000)  # 2 seconds @ 16kHz
self._detection_active = True
self._porcupine = None
self._wake_words = ["dj_rex"]  # Can add more later
self._current_mode = SystemMode.IDLE
```

**Events Emitted:**
- `WAKE_WORD_DETECTED` → Triggers auto-engagement

**Events Subscribed:**
- `SYSTEM_MODE_CHANGE` → Pause/resume wake word detection

**Audio Processing Loop:**
```python
while self._running:
    # Read audio from PyAudio
    pcm = self._audio_stream.read(porcupine.frame_length)
    pcm_unpacked = struct.unpack_from("h" * porcupine.frame_length, pcm)

    # Buffer for pre-roll (last 2 seconds)
    self._audio_buffer.extend(pcm_unpacked)

    # Only process if detection active (not in INTERACTIVE mode)
    if self._detection_active:
        keyword_index = self._porcupine.process(pcm_unpacked)

        if keyword_index >= 0:
            # Wake word detected!
            preroll_audio = list(self._audio_buffer)
            self._emit_wake_word_detected(preroll_audio)
```

##### 1.2: Modify YodaModeManagerService

**File:** `cantina_os/services/yoda_mode_manager_service.py`

**Changes:**
- Subscribe to `WAKE_WORD_DETECTED` event
- Auto-transition to INTERACTIVE mode when wake word detected
- Add configuration flag: `wake_word_auto_engage_enabled`

**New Event Handler:**
```python
async def _handle_wake_word_detected(self, event_data):
    """Auto-engage when wake word detected."""
    if not self._config.wake_word_auto_engage_enabled:
        return

    current_mode = self._current_mode

    # Only auto-engage from IDLE or AMBIENT
    if current_mode in [SystemMode.IDLE, SystemMode.AMBIENT]:
        await self.set_mode(SystemMode.INTERACTIVE)
        self._logger.info("Wake word detected - auto-engaging to INTERACTIVE mode")
```

##### 1.3: Modify DeepgramDirectMicService

**File:** `cantina_os/services/deepgram_direct_mic_service.py`

**Changes:**
- Subscribe to `WAKE_WORD_DETECTED` event
- Accept pre-roll audio buffer from wake word service
- Send pre-roll buffer to Deepgram after WebSocket opens
- Handle scenario where WebSocket is still opening when pre-roll arrives

**New Event Handler:**
```python
async def _handle_wake_word_detected(self, event_data):
    """
    Handle wake word detection with pre-roll buffer.

    Timeline:
    - t=0ms: Wake word detected (user may still be speaking)
    - t=1ms: YodaModeManagerService transitions to INTERACTIVE
    - t=2ms: DeepgramDirectMicService starts opening WebSocket (async)
    - t=200ms: User still speaking "...play Cantina Band"
    - t=300ms: WebSocket opens (connection ready)
    - t=301ms: Send pre-roll buffer (includes "Hey DJ Rex play Can—")
    - t=302ms: Continue live streaming ("—tina Band")
    """
    preroll_audio = event_data.get('preroll_audio', [])

    # Wait for WebSocket to be ready (with timeout)
    await self._wait_for_websocket_ready(timeout_ms=500)

    # Send pre-roll buffer
    if preroll_audio and self._dg_socket:
        self._send_audio_buffer_to_deepgram(preroll_audio)
        self._logger.info(f"Sent pre-roll buffer: {len(preroll_audio)} samples")
```

##### 1.4: Add New Event Topics

**File:** `cantina_os/core/event_topics.py`

```python
class EventTopics:
    # ... existing topics ...

    # Wake word detection
    WAKE_WORD_DETECTED = "wake_word.detected"
    WAKE_WORD_SERVICE_STATUS = "wake_word.service.status"
```

##### 1.5: Add New Event Payload

**File:** `cantina_os/core/event_payloads.py`

```python
class WakeWordDetectedPayload(BaseEventPayload):
    """Payload for wake word detection events."""
    keyword: str  # e.g., "dj_rex"
    confidence: float  # 0.0 - 1.0
    preroll_audio: List[int]  # PCM samples (last 2 seconds)
    preroll_duration_ms: int  # Should be ~2000ms
```

##### 1.6: Configuration

**Add to `.env`:**
```bash
# Porcupine Wake Word
PORCUPINE_ACCESS_KEY=your_access_key_here
PORCUPINE_WAKE_WORDS=dj_rex
PORCUPINE_SENSITIVITIES=0.5  # Lower = fewer false positives
PORCUPINE_PREROLL_SECONDS=2.0

# Wake Word Auto-Engage
WAKE_WORD_AUTO_ENGAGE_ENABLED=true
```

#### Testing Strategy - Phase 1

**Unit Tests:**
- `test_porcupine_wake_word_service.py`:
  - Wake word detection accuracy
  - Pre-roll buffer size (should be ~32000 samples = 2 seconds)
  - Pause/resume on mode changes
  - Audio buffering doesn't overflow

**Integration Tests:**
- Wake word detected → `WAKE_WORD_DETECTED` emitted
- `YodaModeManagerService` receives event → mode changes to INTERACTIVE
- `DeepgramDirectMicService` receives event → sends pre-roll buffer

**E2E Tests:**
1. **Single-breath command:**
   - Say "Hey DJ Rex, play Cantina Band" in one breath
   - Verify: Wake word detected, mode changed, full phrase transcribed
   - Expected: Transcription includes entire phrase (not just "play Cantina Band")

2. **Wake word only:**
   - Say "Hey DJ Rex" then pause
   - Verify: Wake word detected, mode changed, WebSocket opens
   - Expected: System enters listening state, eyes change to "listening" pattern

3. **False positive check:**
   - Play background music for 5 minutes
   - Verify: No false wake word detections
   - Target: < 1% false positive rate

**Success Criteria:**
- ✅ Wake word detection from 5+ feet away
- ✅ Pre-roll buffer captures full phrase (including wake word)
- ✅ WebSocket opens within 300ms of detection
- ✅ False positive rate < 1%
- ✅ Works with background music at 60dB

---

### Phase 2: Deepgram Flux VAD Integration (Priority 2)

**Goal:** Replace mouse clicks with automatic turn detection using Deepgram Flux.

#### Key Decisions

**Mouse Toggle Option:**
The plan asks: *"i wan tot keep the mouse as an option to toggle on and off...but i'm not sure if it will work well with flux or we will need to disable VAD maybe?"*

**Recommendation:** Implement **mutually exclusive modes** via configuration flag:

```bash
# In .env file:
VAD_MODE=flux      # Options: "flux" (auto VAD) or "mouse" (manual clicks)
```

**Reasoning:**
- Flux VAD and mouse clicks serve the same purpose (detecting when user starts/stops speaking)
- Running both simultaneously would create conflicts:
  - Flux detects EndOfTurn → Auto-submits transcription
  - User clicks mouse → Manually submits transcription
  - Result: Duplicate submissions, race conditions
- **Solution:** One mode at a time, configurable

**Implementation:**
```python
# DeepgramDirectMicService
if self._vad_mode == "flux":
    # Subscribe to Flux events, ignore mouse events
    self._use_flux_vad = True
    self._subscribe_to_flux_events()
elif self._vad_mode == "mouse":
    # Subscribe to mouse events, use legacy Nova-3 model
    self._use_flux_vad = False
    self._subscribe_to_mouse_events()
```

#### Implementation Tasks

##### 2.1: Update Deepgram SDK and Model

**File:** `cantina_os/services/deepgram_direct_mic_service.py`

**Changes:**
- Switch model: `nova-3` → `flux-general-en`
- Switch endpoint: Deepgram API v1 → v2
- Add Flux configuration parameters

**New Configuration:**
```python
if self._vad_mode == "flux":
    self._connection_params = {
        "model": "flux-general-en",
        "language": "en",
        "encoding": "linear16",
        "sample_rate": 16000,
        "channels": 1,
        "eot_threshold": 0.7,  # 0.5-0.9, higher = more certain
        "eot_timeout_ms": 1500,  # Max silence before forcing EndOfTurn
        # NO vad_events parameter - Flux has built-in VAD
    }
```

##### 2.2: Add Flux Event Handlers

**Events to Handle:**
- `StartOfTurn` - User started speaking
- `EndOfTurn` - User finished speaking (natural pause)
- `EagerEndOfTurn` - Speculative end detection (optional)
- `TurnResumed` - User resumed speaking after EagerEndOfTurn (optional)

**New Event Handlers:**
```python
async def _handle_flux_start_of_turn(self, deepgram_event):
    """User started speaking."""
    self._emit_event(EventTopics.FLUX_START_OF_TURN, {
        "timestamp": time.time(),
        "conversation_id": self._current_conversation_id
    })

    # Visual feedback: eyes change to "listening" pattern
    self._logger.info("Flux: User started speaking")

async def _handle_flux_end_of_turn(self, deepgram_event):
    """User finished speaking - auto-submit transcription."""
    self._emit_event(EventTopics.FLUX_END_OF_TURN, {
        "timestamp": time.time(),
        "conversation_id": self._current_conversation_id
    })

    # Auto-submit transcription (no mouse click needed!)
    final_transcript = self._get_final_transcript()
    self._emit_event(EventTopics.TRANSCRIPTION_AUTO_SUBMITTED, {
        "text": final_transcript,
        "conversation_id": self._current_conversation_id
    })

    self._logger.info(f"Flux: EndOfTurn detected - auto-submitted: {final_transcript}")
```

##### 2.3: Modify ClaudeService

**File:** `cantina_os/services/claude_service/claude_service.py`

**Changes:**
- Subscribe to `TRANSCRIPTION_AUTO_SUBMITTED` (in addition to existing mouse-triggered events)
- Process auto-submitted transcriptions same as manual submissions

**New Event Subscription:**
```python
async def _start(self):
    # ... existing subscriptions ...

    # Listen for auto-submitted transcriptions (from Flux VAD)
    self._event_bus.on(
        EventTopics.TRANSCRIPTION_AUTO_SUBMITTED,
        self._handle_transcription_auto_submitted
    )
```

##### 2.4: Add Configuration Toggle

**Configuration Class:**
```python
class DeepgramDirectMicServiceConfig:
    # ... existing config ...

    vad_mode: str = "flux"  # Options: "flux" or "mouse"
    flux_eot_threshold: float = 0.7
    flux_eot_timeout_ms: int = 1500
```

**Add to `.env`:**
```bash
# Voice Activity Detection
VAD_MODE=flux  # Options: "flux" (auto VAD) or "mouse" (manual clicks)

# Flux Configuration (only used if VAD_MODE=flux)
FLUX_EOT_THRESHOLD=0.7  # 0.5-0.9, higher = more certain
FLUX_EOT_TIMEOUT_MS=1500  # Max silence before forcing EndOfTurn
```

##### 2.5: Update MouseInputService

**File:** `cantina_os/services/mouse_input_service.py`

**Changes:**
- Add configuration flag to enable/disable service
- When disabled, don't start mouse listener

**Configuration:**
```python
class MouseInputServiceConfig:
    enabled: bool = True  # Can be disabled when using Flux VAD
    # ... rest of existing config ...
```

**Startup Logic:**
```python
async def _start(self):
    if not self._config.enabled:
        self._logger.info("MouseInputService disabled (VAD_MODE=flux)")
        return

    # ... existing startup logic ...
```

##### 2.6: Add New Event Topics

**File:** `cantina_os/core/event_topics.py`

```python
class EventTopics:
    # ... existing topics ...

    # Flux VAD events
    FLUX_START_OF_TURN = "flux.start_of_turn"
    FLUX_END_OF_TURN = "flux.end_of_turn"
    FLUX_EAGER_END_OF_TURN = "flux.eager_end_of_turn"  # Optional
    FLUX_TURN_RESUMED = "flux.turn_resumed"  # Optional

    # Auto-submission (replaces mouse click)
    TRANSCRIPTION_AUTO_SUBMITTED = "transcription.auto_submitted"
```

#### Testing Strategy - Phase 2

**Unit Tests:**
- Flux event parsing (StartOfTurn, EndOfTurn)
- Auto-submission logic (EndOfTurn → emit TRANSCRIPTION_AUTO_SUBMITTED)
- Configuration toggle (VAD_MODE switches between flux/mouse)

**Integration Tests:**
- Flux EndOfTurn → ClaudeService receives transcription
- Mouse disabled when VAD_MODE=flux
- Mouse enabled when VAD_MODE=mouse

**E2E Tests:**
1. **Natural conversation flow (Flux mode):**
   - Say "Hey DJ Rex, what's your favorite song?"
   - Verify: No mouse clicks needed
   - Verify: Transcription auto-submitted after natural pause
   - Verify: Claude responds

2. **Comparison test (Flux vs Mouse):**
   - Record same utterance in both modes
   - Compare: Transcription accuracy, latency
   - Target: Flux accuracy >= Nova-3, latency within 200ms

3. **Silence timeout test:**
   - Say "Hey DJ Rex" then pause for 2+ seconds
   - Verify: Flux detects EndOfTurn after timeout
   - Verify: Partial transcription submitted ("Hey DJ Rex")

**Success Criteria:**
- ✅ Auto-submission works after natural pause (~260ms silence)
- ✅ Transcription accuracy matches Nova-3 baseline
- ✅ No false EndOfTurn detections during normal speech
- ✅ Configuration toggle works (flux ↔ mouse)

---

### Phase 3: Interruption Handling (Priority 3)

**Goal:** Allow user to interrupt DJ mid-response with instant cutoff and context-aware continuation.

#### Implementation Tasks

##### 3.1: Track DJ Speaking State

**File:** `cantina_os/services/deepgram_direct_mic_service.py`

**Changes:**
- Subscribe to `SPEECH_SYNTHESIS_STARTED` and `SPEECH_SYNTHESIS_ENDED`
- Track `_is_dj_speaking` boolean flag
- Detect interruptions (Flux StartOfTurn while `_is_dj_speaking == True`)

**New State:**
```python
self._is_dj_speaking = False
```

**Event Handlers:**
```python
async def _handle_speech_synthesis_started(self, event_data):
    """DJ started speaking."""
    self._is_dj_speaking = True

async def _handle_speech_synthesis_ended(self, event_data):
    """DJ finished speaking."""
    self._is_dj_speaking = False

async def _handle_flux_start_of_turn(self, deepgram_event):
    """User started speaking."""
    if self._is_dj_speaking:
        # This is an INTERRUPTION!
        self._emit_event(EventTopics.SPEECH_INTERRUPTION_DETECTED, {
            "timestamp": time.time(),
            "detected_by": "flux_start_of_turn"
        })
        self._logger.warning("INTERRUPTION DETECTED: User spoke while DJ was speaking")

    # ... rest of existing handler ...
```

##### 3.2: Modify ClaudeService for Interruption Context

**File:** `cantina_os/services/claude_service/claude_service.py`

**Changes:**
- Subscribe to `SPEECH_INTERRUPTION_DETECTED` and `SPEECH_SYNTHESIS_INTERRUPTED`
- Track current response text (`_current_response_text`)
- Build interruption context when user interrupts
- Add context to Claude API call

**New State:**
```python
self._current_response_active = False
self._current_response_text = ""
self._interrupted_response_text = ""
self._interrupted_spoken_text = ""
```

**Interruption Handler:**
```python
async def _handle_speech_interruption_detected(self, event_data):
    """User interrupted DJ - request TTS stop."""
    self._emit_event(EventTopics.SPEECH_INTERRUPTION_REQUEST, {
        "timestamp": time.time()
    })

async def _handle_speech_synthesis_interrupted(self, event_data):
    """TTS stopped - capture what was actually spoken."""
    self._interrupted_response_text = event_data.get('full_response_text')
    self._interrupted_spoken_text = event_data.get('spoken_text')

async def _handle_transcription_auto_submitted(self, event_data):
    """Process user input - check for interruption."""
    user_text = event_data.get('text')

    if self._interrupted_response_text:
        # This is an interruption transcript!
        context = self._build_interruption_context()
        await self._process_interruption(user_text, context)

        # Clear interruption state
        self._interrupted_response_text = ""
        self._interrupted_spoken_text = ""
    else:
        # Normal turn-based flow
        await self._process_user_input(user_text)

def _build_interruption_context(self) -> str:
    """Build context for Claude API."""
    return f"""
    [INTERRUPTION CONTEXT]
    You were saying: "{self._interrupted_response_text}"
    You actually said: "{self._interrupted_spoken_text}"

    User interrupted with: "{{user_text}}"

    Acknowledge the interruption naturally and respond to their question.
    """
```

##### 3.3: Modify ElevenLabsService for Timestamp Tracking

**File:** `cantina_os/services/elevenlabs_service.py`

**Changes:**
- Switch from HTTP streaming to WebSocket API (for character timestamps)
- Track character-level timing during playback
- Calculate "spoken text" on interruption (based on elapsed time)
- Emit enhanced `SPEECH_SYNTHESIS_INTERRUPTED` event

**New State:**
```python
self._playback_start_time = None
self._current_response_text = ""
self._character_timings = []  # [(char, start_ms, duration_ms), ...]
self._playback_active = False
```

**Interruption Handler:**
```python
async def _handle_speech_interruption_request(self, event_data):
    """Stop TTS immediately and calculate spoken text."""
    if not self._playback_active:
        return

    # Calculate elapsed time since playback started
    elapsed_ms = (time.time() - self._playback_start_time) * 1000

    # Find which character we're at based on elapsed time
    spoken_text = self._calculate_spoken_text(elapsed_ms)
    interrupted_word = self._find_interrupted_word(elapsed_ms)

    # Stop audio playback
    self._stop_audio_playback()

    # Emit detailed interruption event
    self._emit_event(EventTopics.SPEECH_SYNTHESIS_INTERRUPTED, {
        "full_response_text": self._current_response_text,
        "spoken_text": spoken_text,
        "interrupted_word": interrupted_word,
        "elapsed_ms": elapsed_ms
    })

    self._playback_active = False

def _calculate_spoken_text(self, elapsed_ms: float) -> str:
    """Calculate what text was actually spoken based on timing."""
    spoken_chars = []

    for char, start_ms, duration_ms in self._character_timings:
        if start_ms + duration_ms <= elapsed_ms:
            spoken_chars.append(char)
        elif start_ms < elapsed_ms:
            # Partially spoken character (mid-word)
            spoken_chars.append(char)
            spoken_chars.append("—")  # Em dash for cutoff
            break

    return "".join(spoken_chars)
```

##### 3.4: Add New Event Topics

**File:** `cantina_os/core/event_topics.py`

```python
class EventTopics:
    # ... existing topics ...

    # Interruption handling
    SPEECH_INTERRUPTION_DETECTED = "speech.interruption.detected"
    SPEECH_INTERRUPTION_REQUEST = "speech.interruption.request"
    SPEECH_SYNTHESIS_INTERRUPTED = "speech.synthesis.interrupted"  # Enhanced
```

##### 3.5: Add New Event Payloads

**File:** `cantina_os/core/event_payloads.py`

```python
class SpeechInterruptionDetectedPayload(BaseEventPayload):
    """User interrupted DJ while speaking."""
    detected_by: str  # "flux_start_of_turn"

class SpeechSynthesisInterruptedPayload(BaseEventPayload):
    """Enhanced payload with timing information."""
    full_response_text: str
    spoken_text: str  # What was actually said before interruption
    interrupted_word: str  # The word being spoken when cut off
    interrupted_at_word_index: int
    elapsed_ms: float
```

#### Testing Strategy - Phase 3

**Unit Tests:**
- Character timing calculation accuracy
- Spoken text extraction (given elapsed time)
- Interruption context building

**Integration Tests:**
- Flux StartOfTurn (while DJ speaking) → SPEECH_INTERRUPTION_DETECTED
- SPEECH_INTERRUPTION_REQUEST → TTS stops → SPEECH_SYNTHESIS_INTERRUPTED
- Interruption context added to Claude API call

**E2E Tests:**
1. **Mid-sentence interruption:**
   - Ask DJ a question that generates 10+ second response
   - Interrupt at 3 seconds
   - Verify: DJ stops within 100ms
   - Verify: Next response acknowledges interruption ("As I was saying...")

2. **Spoken text accuracy:**
   - Interrupt at known timestamp (e.g., 5.2 seconds)
   - Verify: `spoken_text` matches manually counted text
   - Target: >95% accuracy

3. **Rapid interruption:**
   - Interrupt DJ multiple times in quick succession
   - Verify: No race conditions, system recovers gracefully

**Success Criteria:**
- ✅ Interruption detection latency < 150ms (user speaks → DJ stops)
- ✅ Spoken text calculation accuracy > 95%
- ✅ Claude responds contextually to interruptions
- ✅ No audio artifacts (clicks, pops) when stopping TTS

---

## Configuration Summary

All new configuration options to add to `.env`:

```bash
# ============================================
# WAKE WORD DETECTION (Phase 1)
# ============================================
PORCUPINE_ACCESS_KEY=your_picovoice_access_key_here
PORCUPINE_WAKE_WORDS=dj_rex
PORCUPINE_SENSITIVITIES=0.5  # 0.0-1.0, lower = fewer false positives
PORCUPINE_PREROLL_SECONDS=2.0

# Wake Word Auto-Engage
WAKE_WORD_AUTO_ENGAGE_ENABLED=true

# ============================================
# DEEPGRAM FLUX VAD (Phase 2)
# ============================================
# Voice Activity Detection Mode
VAD_MODE=flux  # Options: "flux" (auto VAD) or "mouse" (manual clicks)

# Flux Configuration (only used if VAD_MODE=flux)
DEEPGRAM_MODEL=flux-general-en
FLUX_EOT_THRESHOLD=0.7  # 0.5-0.9, higher = more certain before EndOfTurn
FLUX_EOT_TIMEOUT_MS=1500  # Max silence (ms) before forcing EndOfTurn

# Mouse Input (disabled when using Flux)
MOUSE_INPUT_ENABLED=false  # Auto-disabled if VAD_MODE=flux

# ============================================
# INTERRUPTION HANDLING (Phase 3)
# ============================================
INTERRUPTION_HANDLING_ENABLED=true

# ElevenLabs WebSocket (for character timestamps)
ELEVENLABS_USE_WEBSOCKET=true  # Required for interruption timing
```

---

## Testing Process

Following our standard testing methodology (as per CLAUDE.md):

### 1. Unit Tests
- Test individual service logic in isolation using mocks
- Verify internal state management, event emission
- Use `MockEventBus`, mocked API clients
- **No API keys required**

**Example:**
```bash
cd cantina_os
venv/bin/python -m pytest tests/unit/test_porcupine_wake_word_service.py -v
```

### 2. Integration Tests
- Test services together without external APIs
- Use real service classes but mock external API calls
- Verify event propagation, state transitions
- **No API keys required**

**Example:**
```bash
venv/bin/python -m pytest tests/integration/test_wake_word_flow.py -v
```

### 3. End-to-End Tests (Real API Calls)
- **CRITICAL:** Test against real external APIs
- Requires valid API keys: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `DEEPGRAM_API_KEY`, `PICOVOICE_ACCESS_KEY`
- Verifies actual service behavior, API connectivity, response parsing
- **Must run before declaring "fully tested"**

**Example:**
```bash
# Run full system with test commands
cd cantina_os
echo -e "engage\nquit" | venv/bin/python -m cantina_os.main 2>&1 | grep "INTERACTIVE"
```

**What "fully tested" means:**
- ✅ All unit tests pass (mocked)
- ✅ All integration tests pass (service-to-service, mocked APIs)
- ✅ E2E tests pass with real API keys (actual external service calls)

---

## Latency Targets

### Phase 1: Wake Word → DJ Responds
```
Wake word detection:        100ms
WebSocket opening:          300ms (hidden by pre-roll)
User speaking:              800ms (actual speech duration)
Flux EndOfTurn:             260ms (after Phase 2)
Claude API:                1200ms
ElevenLabs TTFB:            300ms
────────────────────────────────
Total: ~2960ms (~3 seconds)
User perceives: ~2.2 seconds from finishing speech
```

### Phase 3: Interruption → DJ Stops
```
Flux StartOfTurn:            50ms
Interruption detection:      10ms
Stop audio playback:         40ms
────────────────────────────────
Total: ~100ms (imperceptible!)
```

---

## Rollback Plan

Each phase is independent and can be rolled back without affecting others:

**If Wake Word has issues:**
- Disable `WAKE_WORD_AUTO_ENGAGE_ENABLED=false`
- Keep manual "engage" command
- PorcupineWakeWordService simply doesn't emit events

**If Flux VAD has issues:**
- Set `VAD_MODE=mouse`
- Set `MOUSE_INPUT_ENABLED=true`
- Fall back to click-to-talk with Flux model still active

**If Interruption Handling has issues:**
- Set `INTERRUPTION_HANDLING_ENABLED=false`
- Keep Flux VAD for turn detection
- DJ finishes speaking without interruption support

**Nuclear rollback:**
- Revert to Nova-3 model
- Disable all new features
- Return to current working system (mouse-based, manual engage)

---

## Next Steps

### Immediate (Today):
1. ✅ Create this dev log (DONE)
2. Review plan with user
3. Get approval to start Phase 1 implementation

### Phase 1 Implementation (Tomorrow):
1. Create `PorcupineWakeWordService` with pre-roll buffering
2. Modify `YodaModeManagerService` for wake word auto-engage
3. Modify `DeepgramDirectMicService` to accept pre-roll buffer
4. Add event topics and payloads
5. Write unit tests
6. Write integration tests
7. E2E test with real API keys

### Phase 2 Implementation (Following Session):
1. Update Deepgram SDK if needed
2. Switch to Flux model
3. Add Flux event handlers
4. Implement auto-submission
5. Add VAD_MODE configuration toggle
6. Test both flux and mouse modes

### Phase 3 Implementation (Final Session):
1. Track DJ speaking state
2. Detect interruptions
3. Implement ElevenLabs WebSocket with timestamps
4. Build interruption context
5. Test interruption flow E2E

---

## Questions to Resolve

1. **Mouse + Flux Compatibility:** Confirmed - mutually exclusive modes via `VAD_MODE` config
2. **Testing process:** Following standard 3-tier methodology (unit → integration → E2E)
3. **Phase priority:** Wake Word (Phase 1) → Flux VAD (Phase 2) → Interruption (Phase 3) ✅

---

## Git Commits

**Session 1:**
- `82b44b4` - feat: Add Porcupine wake word detection test for "DJ Rex"

---

*End of Session 1 - Ready for Phase 1 implementation approval*
