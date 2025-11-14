# DJ R3X Interruption & Multi-Speaker Design

## Executive Summary

This document explores advanced interaction patterns for DJ R3X, focusing on:
1. **Continuous listening** (always-on transcription with push-to-talk submission)
2. **Interruption handling** (cutting off DJ R3X mid-sentence)
3. **Conversation context** (DJ R3X knowing what he was saying when interrupted)
4. **Multi-speaker scenarios** (cross-talk, multiple people, children)

---

## Current Architecture Analysis

### How It Works Today (Turn-Based)

**Flow:**
```
1. User clicks mouse → Emit MIC_RECORDING_START
2. DeepgramDirectMicService starts microphone
3. Audio streams to Deepgram WebSocket (already open)
4. User releases mouse → Emit MIC_RECORDING_STOP
5. Deepgram sends final transcription
6. ClaudeService processes → generates response
7. ElevenLabsService plays TTS
8. User waits for TTS to complete before speaking again
```

**Current State:**
- ✅ WebSocket is **persistent** (opens on 'engage', stays open until 'disengage')
- ✅ Microphone start/stop is independent of WebSocket
- ❌ Transcription only happens during mouse click
- ❌ No way to interrupt DJ R3X speaking
- ❌ DJ R3X doesn't know what he was saying if interrupted

---

## Proposed Architecture: Continuous Listening + Push-to-Talk Submission

### Key Insight from Your Question

> "With the web socket connection does that mean we could have deepgram transcribing like the whole time? and then our mouse click could trigger what we want to send to claude?"

**YES! This is exactly the right approach.** Here's how:

### New Flow Design

```
PHASE 1: ALWAYS-ON LISTENING (No mouse interaction needed)
┌─────────────────────────────────────────────────────┐
│ WebSocket: OPEN (persistent)                        │
│ Microphone: ALWAYS RUNNING (continuous capture)     │
│ Deepgram: Transcribing everything it hears          │
│ Output: Interim + final transcripts flowing in      │
│                                                      │
│ Events Emitted:                                      │
│  - TRANSCRIPTION_INTERIM (real-time partial text)   │
│  - TRANSCRIPTION_FINAL (completed utterances)       │
│                                                      │
│ Storage: Rolling buffer of recent transcripts       │
│          (last 30 seconds? configurable)            │
└─────────────────────────────────────────────────────┘

PHASE 2: INTENT SUBMISSION (Mouse click or voice trigger)
┌─────────────────────────────────────────────────────┐
│ User Action: Mouse click (or "Hey DJ-R3X" trigger)  │
│                                                      │
│ DeepgramService Actions:                            │
│  1. Captures current buffered transcripts           │
│  2. Emits TRANSCRIPTION_SUBMITTED event             │
│  3. Payload includes:                                │
│     - buffered_text: Last N seconds of speech       │
│     - conversation_id: New ID for this turn         │
│     - timestamp: When submitted                     │
│                                                      │
│ ClaudeService Actions:                              │
│  - Receives TRANSCRIPTION_SUBMITTED (NOT _FINAL)    │
│  - Processes with full context                      │
│  - Generates response                               │
└─────────────────────────────────────────────────────┘

PHASE 3: DJ R3X RESPONDS
┌─────────────────────────────────────────────────────┐
│ ClaudeService → LLM_RESPONSE_TEXT                   │
│ ElevenLabsService → Starts TTS playback             │
│                                                      │
│ State Tracking:                                      │
│  - current_response_text: What DJ-R3X is saying     │
│  - current_response_id: Track this utterance        │
│  - playback_position: How far into speech (approx)  │
└─────────────────────────────────────────────────────┘

PHASE 4: INTERRUPTION HANDLING
┌─────────────────────────────────────────────────────┐
│ Trigger: Mouse click WHILE DJ-R3X is speaking       │
│                                                      │
│ Actions:                                             │
│  1. Emit SPEECH_INTERRUPTION_REQUEST event          │
│  2. ElevenLabsService stops TTS playback            │
│  3. Capture what DJ-R3X was saying:                 │
│     - full_intended_response: Complete text         │
│     - interrupted_at: Approx where he stopped       │
│  4. Process new user input with context:            │
│     - user_transcript: What user just said          │
│     - dj_context: What DJ-R3X was saying            │
│  5. ClaudeService gets BOTH in prompt:              │
│     "You were saying: '{interrupted_text}'          │
│      User interrupted with: '{user_input}'"         │
└─────────────────────────────────────────────────────┘
```

---

## Architecture Components & Changes

### 1. DeepgramDirectMicService Changes

**Current Behavior:**
- Microphone starts on `MIC_RECORDING_START`
- Microphone stops on `MIC_RECORDING_STOP`

**New Behavior:**
```python
class DeepgramDirectMicService:
    def __init__(self, ...):
        # NEW: Continuous listening mode
        self._continuous_listening_enabled = True  # Config flag
        self._transcript_buffer = deque(maxlen=50)  # Rolling buffer
        self._buffer_window_seconds = 30  # Keep last 30s

    async def _handle_mode_changed(self, event):
        """Mode changes to INTERACTIVE."""
        if new_mode == SystemMode.INTERACTIVE:
            # Open WebSocket
            await self._open_websocket_connection()

            # NEW: Start continuous microphone immediately
            if self._continuous_listening_enabled:
                await self._start_continuous_microphone()

    async def _start_continuous_microphone(self):
        """Start mic and keep it running (no stop until disengage)."""
        self._current_transcription = ""
        self._audio_stream = self._pyaudio.open(...)
        self._audio_running = True
        self._audio_thread = threading.Thread(
            target=self._audio_capture_loop,
            daemon=True
        )
        self._audio_thread.start()
        self._is_listening = True

        self.logger.info("✓ Continuous listening ACTIVE")

    async def _on_transcript_received(self, transcript_data):
        """Deepgram callback - receives ALL transcripts."""
        is_final = transcript_data.get("is_final", False)
        text = transcript_data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")

        if text:
            # NEW: Add to rolling buffer
            self._transcript_buffer.append({
                "text": text,
                "timestamp": time.time(),
                "is_final": is_final
            })

            # Emit for UI/debugging (but DON'T send to Claude yet)
            if is_final:
                await self.emit(EventTopics.TRANSCRIPTION_FINAL, payload)
            else:
                await self.emit(EventTopics.TRANSCRIPTION_INTERIM, payload)

    async def _handle_mic_recording_start(self, event):
        """
        NEW BEHAVIOR: Mouse click = SUBMIT buffered transcripts to Claude.
        (Mic is already running continuously)
        """
        # Capture buffered transcripts from last N seconds
        now = time.time()
        cutoff = now - self._buffer_window_seconds

        recent_transcripts = [
            t for t in self._transcript_buffer
            if t["timestamp"] >= cutoff and t["is_final"]
        ]

        # Combine into submission text
        submission_text = " ".join(t["text"] for t in recent_transcripts)

        conversation_id = str(uuid.uuid4())

        # NEW EVENT: Transcription submission (not just "final")
        await self.emit(EventTopics.TRANSCRIPTION_SUBMITTED, {
            "conversation_id": conversation_id,
            "text": submission_text,
            "timestamp": now,
            "buffer_count": len(recent_transcripts)
        })

        # Clear buffer after submission
        self._transcript_buffer.clear()

    async def _handle_mic_recording_stop(self, event):
        """
        NEW BEHAVIOR: Mouse release = do nothing (mic keeps running).
        OR: Could be used as alternate trigger for submission.
        """
        # Option 1: Do nothing (mouse click on press already submitted)
        # Option 2: Submit on release instead of press
        pass
```

**Key Changes:**
- Microphone runs **continuously** in INTERACTIVE mode
- Transcripts buffer in memory (rolling 30-second window)
- Mouse click **submits** buffered text to Claude (doesn't start/stop mic)
- `TRANSCRIPTION_SUBMITTED` event replaces `TRANSCRIPTION_FINAL` for Claude processing

---

### 2. ClaudeService Changes

**Current Behavior:**
- Listens to `TRANSCRIPTION_FINAL` → processes immediately

**New Behavior:**
```python
class ClaudeService:
    async def _setup_subscriptions(self):
        # OLD: Listen to TRANSCRIPTION_FINAL
        # await self.subscribe(EventTopics.TRANSCRIPTION_FINAL, ...)

        # NEW: Listen to TRANSCRIPTION_SUBMITTED
        await self.subscribe(
            EventTopics.TRANSCRIPTION_SUBMITTED,
            self._handle_transcription_submitted
        )

        # NEW: Listen for interruptions
        await self.subscribe(
            EventTopics.SPEECH_INTERRUPTION_REQUEST,
            self._handle_speech_interrupted
        )

    async def _handle_transcription_submitted(self, event):
        """Process user input when they SUBMIT (not just when they speak)."""
        conversation_id = event.get("conversation_id")
        user_text = event.get("text")

        # Check if DJ-R3X is currently speaking
        if self._current_response_active:
            # User is interrupting!
            await self._handle_interruption(user_text, conversation_id)
        else:
            # Normal turn-based flow
            await self._process_user_input(user_text, conversation_id)

    async def _handle_interruption(self, user_text, conversation_id):
        """User interrupted DJ-R3X while speaking."""
        # Get what DJ-R3X was saying
        interrupted_response = self._current_response_text

        # Stop TTS playback
        await self.emit(EventTopics.SPEECH_INTERRUPTION_REQUEST, {
            "conversation_id": conversation_id,
            "timestamp": time.time()
        })

        # Build context-aware prompt
        interruption_context = f"""
You (DJ R-3X) were in the middle of saying:
"{interrupted_response}"

The user interrupted you and said:
"{user_text}"

Respond naturally to their interruption. You can:
- Acknowledge being interrupted ("Oh! Sorry, what?")
- Address their point directly
- Finish your thought briefly if relevant
- Completely shift focus to their new topic
"""

        # Add to session memory as system message
        self._session_memory.add_message("system", interruption_context)
        self._session_memory.add_message("user", user_text)

        # Generate response with interruption context
        await self._call_claude_api(conversation_id)
```

**Key Changes:**
- Listens to `TRANSCRIPTION_SUBMITTED` instead of `TRANSCRIPTION_FINAL`
- Tracks current response state (`_current_response_active`, `_current_response_text`)
- Detects interruptions and provides context to Claude
- Claude knows what it was saying when interrupted

---

### 3. ElevenLabsService Changes

**Current Behavior:**
- Plays TTS to completion, no interruption support

**New Behavior:**
```python
class ElevenLabsService:
    def __init__(self, ...):
        self._current_playback_active = False
        self._current_playback_id = None
        self._playback_thread = None

    async def _setup_subscriptions(self):
        # ... existing subscriptions ...

        # NEW: Listen for interruption requests
        await self.subscribe(
            EventTopics.SPEECH_INTERRUPTION_REQUEST,
            self._handle_interruption_request
        )

    async def _handle_interruption_request(self, event):
        """Stop current TTS playback immediately."""
        if not self._current_playback_active:
            return

        self.logger.info("🛑 Interruption requested - stopping TTS")

        # Stop audio playback
        if self._playback_thread:
            # Set flag to stop streaming
            self._playback_active = False

            # For sounddevice streaming
            import sounddevice as sd
            sd.stop()

        # Emit interrupted event
        await self.emit(EventTopics.SPEECH_SYNTHESIS_INTERRUPTED, {
            "playback_id": self._current_playback_id,
            "timestamp": time.time()
        })

        self._current_playback_active = False
```

**Key Changes:**
- Tracks active playback state
- Can be interrupted mid-sentence
- Emits `SPEECH_SYNTHESIS_INTERRUPTED` event

---

### 4. New Event Topics

Add to `event_topics.py`:

```python
class EventTopics:
    # ... existing topics ...

    # Continuous listening
    TRANSCRIPTION_SUBMITTED = "transcription.submitted"  # User submits buffered text

    # Interruption handling
    SPEECH_INTERRUPTION_REQUEST = "speech.interruption.request"  # Request to stop TTS
    SPEECH_SYNTHESIS_INTERRUPTED = "speech.synthesis.interrupted"  # TTS was stopped

    # Response state tracking
    RESPONSE_STATE_CHANGED = "response.state.changed"  # DJ-R3X speaking/idle
```

---

## Deepgram Flux Integration (Advanced)

**What is Flux?**
- New Deepgram model built specifically for **conversational AI**
- Detects "eager end-of-turn" (when someone is **probably** done talking)
- Reduces latency by allowing speculative response generation

**How It Helps:**

```python
# In DeepgramDirectMicService config
self._connection_params = {
    "model": "nova-3-flux",  # Use Flux model
    "eager_eot_threshold": 0.7,  # Confidence threshold for "they're done"
    # ... other params ...
}

# New events from Flux
async def _on_eager_end_of_turn(self, event):
    """Flux detected user is probably done talking (70% confidence)."""
    # Option 1: Auto-submit to Claude (speculative)
    # Option 2: Show visual indicator "Ready to respond?"
    # Option 3: Wait for mouse click confirmation

async def _on_turn_resumed(self, event):
    """User kept talking - cancel speculative response."""
    # Cancel any in-progress Claude call
    # Clear speculative submission
```

**Benefits:**
- Lower perceived latency (start generating response before 100% sure)
- Better for natural conversation flow
- Can still require mouse click for final submission

---

## Multi-Speaker & Cross-Talk Scenarios

### Scenario 1: Parent + Child Talking to DJ-R3X

**Challenge:**
- Both parent and child might speak at same time
- Deepgram hears both voices
- Transcription might blend them together

**Solution Options:**

**Option A: Single-Speaker Mode (Current)**
```
- Transcribe everything as single stream
- Mouse click submission decides who's "speaking"
- Claude context: "Multiple people might be talking, pay attention to who seems to be addressing you"
```

**Option B: Speaker Diarization (Advanced)**
```python
# In DeepgramDirectMicService config
self._connection_params = {
    "diarize": "true",  # Enable speaker separation
    "diarize_version": "2023-09-08",
    # ... other params ...
}

# Deepgram returns speaker labels
# {
#   "speaker": 0,  # Speaker A
#   "transcript": "Hey DJ-R3X!"
# }
# {
#   "speaker": 1,  # Speaker B
#   "transcript": "No, I want to ask!"
# }

# Buffer tracks per-speaker transcripts
self._speaker_buffers = {
    0: deque(maxlen=25),
    1: deque(maxlen=25)
}

# On submission, include speaker info
await self.emit(EventTopics.TRANSCRIPTION_SUBMITTED, {
    "text": combined_text,
    "speaker_breakdown": {
        "speaker_0": "Hey DJ-R3X!",
        "speaker_1": "No, I want to ask!"
    },
    "primary_speaker": 0  # Who spoke most recently
})
```

**Claude Prompt Enhancement:**
```
You are DJ R-3X, and multiple people are in the room:
- Speaker 0 (likely parent): "Hey DJ-R3X!"
- Speaker 1 (likely child): "No, I want to ask!"

Respond to the most relevant speaker, or address both if appropriate.
```

---

### Scenario 2: Child Interrupts DJ-R3X to Correct Themselves

**Situation:**
```
Child: "DJ-R3X, can you play... wait no, I mean..."
DJ-R3X: *starts responding*
Child: "No! I meant the OTHER song!"
```

**Current Problem:**
- DJ-R3X doesn't hear the mid-sentence correction
- Responds to incomplete request

**Solution with Continuous Listening:**
```
1. Child speaks: "DJ-R3X, can you play..."
2. Continuous buffer captures: "DJ-R3X, can you play... wait no, I mean..."
3. Child clicks mouse → Submits: "DJ-R3X, can you play... wait no, I mean the Cantina Band!"
4. Claude sees FULL context (including self-correction)
5. Responds to corrected request
```

**Advanced: Real-Time Interruption**
```
1. Child: "DJ-R3X, can you play Star Wars music?" [clicks]
2. DJ-R3X starts: "Sure! Let me spin some—"
3. Child interrupts: "Wait! I meant the Cantina Band!" [clicks again]
4. System detects interruption:
   - Stops DJ-R3X TTS
   - Submits: "Wait! I meant the Cantina Band!"
5. Claude receives context:
   "User asked: 'play Star Wars music'
    You were about to play a generic track.
    User interrupted: 'Wait! I meant the Cantina Band!'"
6. DJ-R3X: "Oh! Cantina Band, got it! Spinning that now!"
```

---

## Implementation Phases

### Phase 1: Continuous Listening (MVP)
- ✅ Microphone runs continuously in INTERACTIVE mode
- ✅ Transcripts buffer in memory (30-second rolling window)
- ✅ Mouse click submits buffered text to Claude
- ✅ New event: `TRANSCRIPTION_SUBMITTED`
- ⏱️ Estimated: 4-6 hours

**Benefits:**
- Captures full context before submission
- No more "cut off" speech due to mouse timing
- Handles self-corrections naturally

---

### Phase 2: Interruption Handling (Enhanced UX)
- ✅ Track DJ-R3X response state (speaking/idle)
- ✅ Detect mouse click during TTS playback
- ✅ Stop TTS immediately
- ✅ Provide interruption context to Claude
- ✅ New events: `SPEECH_INTERRUPTION_REQUEST`, `SPEECH_SYNTHESIS_INTERRUPTED`
- ⏱️ Estimated: 3-4 hours

**Benefits:**
- Natural conversation flow
- Can interrupt long responses
- DJ-R3X knows what he was saying

---

### Phase 3: Multi-Speaker Support (Optional)
- ✅ Enable Deepgram speaker diarization
- ✅ Track per-speaker buffers
- ✅ Provide speaker breakdown to Claude
- ⏱️ Estimated: 4-6 hours

**Benefits:**
- Handles parent + child interactions
- DJ-R3X can address specific people
- Better context for multi-party conversations

---

### Phase 4: Deepgram Flux Integration (Advanced)
- ✅ Switch to `nova-3-flux` model
- ✅ Configure `eager_eot_threshold`
- ✅ Handle `EagerEndOfTurn` and `TurnResumed` events
- ✅ Optional: Speculative response generation
- ⏱️ Estimated: 2-3 hours

**Benefits:**
- Lower perceived latency
- More natural turn-taking
- Can still require mouse confirmation

---

## Configuration Options

```python
# In .env or config
CONTINUOUS_LISTENING_ENABLED=true  # Enable always-on mic
TRANSCRIPT_BUFFER_SECONDS=30       # How long to buffer (30s default)
AUTO_SUBMIT_ON_SILENCE=false       # Auto-submit after silence (vs mouse click)
ENABLE_SPEAKER_DIARIZATION=false   # Multi-speaker support
ENABLE_INTERRUPTION_HANDLING=true  # Allow interrupting DJ-R3X
FLUX_EAGER_EOT_THRESHOLD=0.7       # Flux end-of-turn confidence (0.0-1.0)
```

---

## Example Interaction Flows

### Flow 1: Normal Continuous Listening

```
[DJ-R3X is idle, mic is running continuously]

User (thinking out loud): "Hmm, what song should I ask for..."
  → Deepgram transcribes, buffers locally
  → NOT sent to Claude yet

User: "Oh yeah! DJ-R3X, can you play Cantina Band?"
  → Still buffering, NOT sent to Claude

User: [Clicks mouse]
  → System submits buffered text: "Hmm, what song should I ask for... Oh yeah! DJ-R3X, can you play Cantina Band?"
  → Claude processes with full context
  → DJ-R3X: "Cantina Band! Classic choice! Spinning it now!"
```

**Benefits:**
- Captures full thought process
- Claude sees "Oh yeah!" indicates final decision
- No awkward cut-offs

---

### Flow 2: Interruption During Response

```
User: "Tell me about your old flying days" [clicks]
  → Claude generates long response

DJ-R3X: "Oh man, flying the Starspeeder 3000 was incredible! I took tourists all over the galaxy—to Hoth, Tatooine, even Coruscant! One time we had this wild turbulence near—"

User: "Wait, did you ever crash?" [clicks while DJ-R3X is speaking]
  → System detects: DJ-R3X is speaking
  → Stops TTS immediately
  → Captures interruption context:
      - DJ-R3X was saying: "... One time we had this wild turbulence near—"
      - User interrupted with: "Wait, did you ever crash?"
  → Claude receives both

DJ-R3X: "Crash?! Ha! Well, there WAS this one time... but let's just say I got everyone home safe! Sort of!"
```

**Benefits:**
- Natural conversation flow
- DJ-R3X acknowledges interruption
- Responds to new topic with context

---

### Flow 3: Multi-Speaker (Parent + Child)

```
[Both parent and child in room, continuous listening active]

Child: "DJ-R3X, I want—"
Parent: "Let me ask, honey"
Child: "No! I wanna ask!"
Parent: "Okay, go ahead"
Child: "DJ-R3X, can you play the really fast song?" [parent clicks mouse to submit]

System captures (with diarization):
  - Speaker 1 (child): "DJ-R3X, I want—" / "No! I wanna ask!" / "DJ-R3X, can you play the really fast song?"
  - Speaker 0 (parent): "Let me ask, honey" / "Okay, go ahead"

Claude receives:
  "Speaker 1 (likely child): 'DJ-R3X, can you play the really fast song?'
   Speaker 0 (likely parent): 'Let me ask, honey' / 'Okay, go ahead'
   Primary speaker: 1 (child)
   Context: Parent was helping child ask a question"

DJ-R3X: "The really fast song? You got it! Let me spin Mad About Me—it's got some serious beats!"
```

**Benefits:**
- Understands family dynamics
- Responds to the child (primary speaker)
- Acknowledges parent's supportive role

---

## Risks & Considerations

### Privacy Concerns
- **Issue**: Mic is always on, capturing everything
- **Mitigation**:
  - Clear visual indicator (LED) when mic is active
  - Buffer clears after submission
  - Only submitted text goes to Claude (not continuous stream)
  - Add "mute" command to disable mic temporarily

### Accidental Submissions
- **Issue**: Mouse click might submit unintended speech
- **Mitigation**:
  - Require click + hold (e.g., 200ms) to confirm submission
  - Show "submitting..." indicator
  - Add "undo" command in first 2 seconds

### Deepgram Costs
- **Issue**: Continuous transcription uses more API quota
- **Mitigation**:
  - Deepgram charges per minute of audio, not per transcript
  - Cost is same whether you transcribe 1 long session or 10 short ones
  - KeepAlive prevents reconnection costs
  - Monitor usage via Deepgram dashboard

### Cross-Talk Accuracy
- **Issue**: Multiple people speaking at once confuses transcription
- **Mitigation**:
  - Speaker diarization helps separate voices
  - Prompt Claude to ask for clarification if confused
  - Visual indicator: "Multiple speakers detected"

---

## Recommended Implementation Order

1. **Start with Phase 1 (Continuous Listening)**
   - Biggest UX improvement
   - Foundation for all other features
   - Test with single speaker first

2. **Add Phase 2 (Interruption Handling)**
   - Natural extension of Phase 1
   - Enables real conversation flow
   - Critical for child interactions

3. **Evaluate Need for Phase 3 (Multi-Speaker)**
   - Depends on how often multiple people use DJ-R3X simultaneously
   - Speaker diarization costs extra processing
   - May not be needed if Phase 1+2 handle most cases

4. **Consider Phase 4 (Flux) as Optimization**
   - Reduces latency for advanced users
   - Not required for basic functionality
   - Good candidate for A/B testing

---

## Questions for Design Decisions

1. **Submission Trigger**: Mouse click (manual) vs. silence detection (auto)?
   - Manual = more control, less accidental submissions
   - Auto = more natural, but might trigger mid-sentence

2. **Interruption Style**: Immediate cutoff vs. graceful fadeout?
   - Immediate = more responsive
   - Fadeout = less jarring, but slower

3. **Buffer Size**: 30 seconds vs. 60 seconds vs. unlimited?
   - Shorter = less memory, more focused context
   - Longer = captures more thinking/conversation

4. **Multi-Speaker Default**: Always enable diarization or opt-in?
   - Always = better for families, but more processing
   - Opt-in = faster for single users

5. **Flux Auto-Submit**: Enable eager end-of-turn or keep manual?
   - Auto = lower latency, feels more "real-time"
   - Manual = more predictable, less surprises

---

## Next Steps

1. **Prototype Phase 1** (Continuous Listening)
   - Modify DeepgramDirectMicService
   - Add transcript buffering
   - Change mouse click to submission trigger
   - Test with simple interactions

2. **User Testing** (with your son!)
   - Does continuous listening feel natural?
   - Is mouse click intuitive for submission?
   - How often do accidental submissions happen?

3. **Iterate Based on Feedback**
   - Adjust buffer size
   - Fine-tune submission trigger
   - Add visual feedback

4. **Document & Deploy**
   - Update architecture docs
   - Add configuration options
   - Monitor Deepgram usage

---

## Conclusion

Your intuition is **100% correct**: with a persistent WebSocket, Deepgram can transcribe continuously, and mouse clicks can control **submission** (not mic start/stop). This enables:

✅ **Better context capture** (full sentences, self-corrections)
✅ **Natural interruptions** (cut off DJ-R3X mid-response)
✅ **Conversation awareness** (DJ-R3X knows what he was saying)
✅ **Multi-speaker support** (parent + child scenarios)

The architecture is **ready** for this—it just needs the behavioral shift from "turn-based" to "continuous with submission gating."
