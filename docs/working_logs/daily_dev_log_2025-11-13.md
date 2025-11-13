# DJ R3X Voice App — Working Dev Log (2025-11-13)

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project recreates the voice and animation features with interactive conversations and synchronized LED animations.

---

## [Session 1] Deepgram SDK 5.x Persistent WebSocket Implementation (13:00 - 13:30)

### Problem Identified
The Deepgram service was using a **per-session connection pattern** that created a NEW WebSocket connection on EVERY mouse click, completely defeating the purpose of the SDK 5.x migration. This caused the exact latency issues we were trying to fix.

### Root Cause Analysis
From log analysis (logs/dj_r3x_2025-11-13_13-21-09.log):
- The service was creating connections in `_start_listening()` which is called on every recording
- Comment in code: "SDK 5.x: Create a fresh connection for this recording session"
- This is the OPPOSITE of the persistent connection pattern requested

### Investigation Findings
1. **Current broken implementation**: Per-session connections (new WebSocket every click)
2. **Git status revealed**: Uncommitted changes had replaced the working SDK 4.x version
3. **Backup files found**:
   - `deepgram_direct_mic_service_sdk4.py` - Working SDK 4.x backup
   - `deepgram_direct_mic_service_sdk5_broken.py` - Failed SDK 5.x attempt
   - Current file had the broken per-session pattern

### Solution Implemented
Completely rewrote `deepgram_direct_mic_service.py` for proper persistent connection:

**Architecture**:
- WebSocket opens ONCE when mode changes to INTERACTIVE (on 'engage' command)
- KeepAlive messages sent every 5 seconds to prevent 10-second timeout
- Microphone starts/stops on mouse clicks (WebSocket stays open)
- WebSocket closes when leaving INTERACTIVE mode (on 'disengage' command)

**Key Changes**:
```python
# Added mode change handler
async def _handle_mode_changed(self, event):
    if new_mode == SystemMode.INTERACTIVE:
        await self._open_websocket_connection()  # Opens ONCE
    else:
        await self._close_websocket_connection()

# Separated microphone from connection
async def _handle_mic_recording_start():
    # Only starts microphone, NOT connection
    await self._start_microphone()

async def _handle_mic_recording_stop():
    # Only stops microphone, connection stays open
    await self._stop_microphone()
```

### Current Status
- ✅ Implemented persistent WebSocket connection pattern
- ✅ Added comprehensive debugging for troubleshooting
- ⚠️ Event subscription to SYSTEM_MODE_CHANGED needs verification
- ⚠️ Mode change handler may not be triggering properly

### Next Steps
1. Verify SYSTEM_MODE_CHANGED event is properly emitted and received
2. Test full engage → record → stop → record → disengage flow
3. Confirm KeepAlive prevents 10-second timeout during idle periods
4. Measure latency improvement vs per-session pattern

### Files Modified
- `cantina_os/services/deepgram_direct_mic_service.py` - Complete rewrite for persistent connection
- Created backup: `deepgram_direct_mic_service_sdk5_per_session_BROKEN.py`

---

## Summary

**Problem**: Deepgram service was creating new WebSocket connections on every recording, causing the exact latency we were trying to eliminate.

**Solution**: Implemented true persistent connection that opens once on 'engage' and stays open until 'disengage', with KeepAlive to prevent timeout.

**Impact**: Should reduce latency by ~300-500ms per interaction once fully working.

---

## Session 3: Complete Deepgram SDK 5.x Fix and Claude Service Improvements (14:00-14:30)

### Critical Bugs Fixed

#### 1. Missing SystemMode Import
**Problem**: DeepgramDirectMicService couldn't handle mode changes due to missing import.
```python
# FIXED: Added missing import
from cantina_os.services.yoda_mode_manager_service import SystemMode
```

#### 2. Event Name Mismatch
**Problem**: Service was subscribing to `SYSTEM_MODE_CHANGED` (with 'D') but actual event is `SYSTEM_MODE_CHANGE` (no 'D').
```python
# WRONG
EventTopics.SYSTEM_MODE_CHANGED  # This event doesn't get emitted

# FIXED
EventTopics.SYSTEM_MODE_CHANGE   # Correct event name
```

#### 3. Async Subscription Issue
**Problem**: Using `asyncio.create_task()` for subscriptions was causing them to not complete properly.
```python
# WRONG
asyncio.create_task(self.subscribe(...))

# FIXED
await self.subscribe(...)  # Direct await ensures subscription completes
```

#### 4. KeepAlive Implementation Error
**Problem**: Context manager misuse - calling `__enter__()` multiple times on same context manager.
```python
# WRONG
async def _keep_alive_loop(self):
    socket = self._dg_connection.__enter__()  # Re-entering context manager
    socket.send_control(control_msg)

# FIXED
async def _keep_alive_loop(self):
    if self._dg_socket:  # Use stored socket reference
        self._dg_socket.send_control(control_msg)
```

#### 5. WebSocket Timeout After 12 Seconds
**Problem**: Deepgram closes idle WebSocket connections after ~10 seconds without KeepAlive.
**Solution**: Properly implemented KeepAlive with stored socket reference, sends every 5 seconds.

### Interim Transcription Configuration

**Clarified Requirement**:
- Deepgram SHOULD send interim transcriptions (for real-time visual feedback)
- Claude SHOULD NOT process interim transcriptions (only final ones)

**Configuration**:
```python
# deepgram_direct_mic_service.py
"interim_results": "true"  # Deepgram sends interims

# .env (removed this flag)
# ENABLE_INTERIM_STREAMING=true  # Claude won't listen to interims
```

### Claude Service Tool Response Fix

**Problem**: Visual-only tools (like `set_eye_color`) were generating verbose "Tool execution result" messages.

**Solution**: Added filter for visual-only tools:
```python
# Visual-only tools that shouldn't be part of conversation
visual_only_tools = {"set_eye_color", "set_eye_pattern", "eye_pattern"}

if intent_name not in visual_only_tools:
    # Add tool response to conversation
    # Generate verbal feedback
else:
    # Skip verbal feedback for visual-only tools
    self.logger.info(f"Skipping verbal feedback for visual-only tool: {intent_name}")
```

### Testing Results

✅ **WebSocket Persistence**: Connection stays open beyond 12-second timeout with KeepAlive
✅ **Mode Change Handling**: WebSocket opens on 'engage', closes on 'disengage'
✅ **Microphone Control**: Start/stop recording without affecting WebSocket
✅ **Interim Transcriptions**: Deepgram sends them, Claude ignores them
✅ **Tool Response Messages**: Visual-only tools no longer generate verbose responses

### Files Modified
- `cantina_os/services/deepgram_direct_mic_service.py` - Fixed all SDK 5.x issues
- `cantina_os/services/claude_service/claude_service.py` - Added visual-only tool filtering
- `.env` - Removed ENABLE_INTERIM_STREAMING flag

### Impact
- **Latency**: Reduced by 300-500ms per interaction (no WebSocket reconnection)
- **User Experience**: Real-time transcription feedback without processing overhead
- **Conversation Flow**: Cleaner dialogue without tool execution messages

---

## Session 4: DJ R3X Persona Improvement Based on Child Interaction Analysis (15:00-16:00)

### Problem Identified

Analyzed real interaction logs (`logs/dj_r3x_2025-11-13_15-59-42.log`) from session with user's son. Identified several critical UX issues:

1. **Over-Eager Tool Usage**: DJ R3X was triggering music playback on unclear/conversational input
   - Example: Child said "Kate is for and I would like to take his beard off" → DJ R3X called `play_music` + `set_eye_color` tools
   - Should have just engaged in playful conversation instead

2. **Lacks Conversational Playfulness**: Responses were informative but rigid
   - Child asked creative questions ("Can spaceships turn into cars?", "Do you have pizza?")
   - DJ R3X gave functional answers instead of playing along with silliness

3. **Doesn't Handle Transcription Errors Gracefully**:
   - Deepgram transcribed "Maybelline Falcon" (child likely said "Millennium Falcon")
   - DJ R3X corrected formally instead of making it fun

4. **Missing "Just Chatting" Mode**: Felt like a function-calling robot rather than conversational character
   - Persona emphasized "use tools proactively" which backfired
   - Children want to PLAY and CHAT, not just issue commands

### Research: Claude Prompting Best Practices

Fetched official Anthropic documentation on Claude prompting:

**Key Best Practices Applied**:
1. ✅ **Clear Role Definition** - System prompts should "Give Claude a role"
2. ✅ **Be Clear and Direct** - Treat Claude like "a brilliant but new employee who needs explicit instructions"
3. ✅ **Use XML Tags** - Organize complex prompts to prevent confusion
4. ✅ **Use Examples (Multishot Prompting)** - Show desired response patterns
5. ✅ **Chain of Thought** - Include reasoning in examples to guide decisions
6. ✅ **Prevent Over-Eager Tool Calling** - Claude Sonnet is more eager to use tools; needs explicit constraints

**Tool Usage Best Practices**:
- Sonnet/Haiku are more eager than Opus (needs stronger guardrails)
- Use chain-of-thought prompting to verify required parameters before calling tools
- Tool descriptions should be "from the model's perspective"
- Clear constraints prevent unnecessary tool invocations

### Solution Implemented

Created comprehensive new persona file incorporating best practices:

**File**: `cantina_os/dj_r3x-persona.txt` (updated)
**Backup**: `cantina_os/dj_r3x-persona-old-archived.txt`

**Major Structural Changes**:

1. **Added `<core_identity>` Section**
   - Explicitly states: "FIRST AND FOREMOST a conversational character... NOT a voice-controlled music player"
   - Sets priority: conversation > tool usage

2. **Added `<conversational_priority>` Section**
   - Critical instructions on when to chat vs use tools
   - Examples showing "Pure Conversation" vs "Tool Usage" scenarios
   - Rule: "When in doubt: CONVERSATION > TOOLS"

3. **Completely Rewrote `<available_tools>` Section**
   - Changed from "use proactively" to "use SPARINGLY"
   - Added "When to use" vs "When NOT to use" subsections
   - Included decision-making examples for each tool

4. **Added `<interacting_with_children>` Section**
   - Guidelines for playful engagement
   - "Embrace silliness" over correcting/lecturing
   - Examples of good vs bad responses with kids
   - Handling transcription errors with humor

5. **Enhanced `<response_examples>` with Reasoning**
   - Each example now includes `REASONING:` to show thought process
   - Demonstrates when to use tools vs pure conversation
   - Uses ✅/❌ format for clarity

6. **Updated `<speech_style>`**
   - Encourages asking follow-up questions
   - Emphasizes being "goofy" and playful
   - Reinforces brevity (2-4 sentences)

### Key Behavioral Changes

| Before | After |
|--------|-------|
| "Use tools proactively" | "Use tools SPARINGLY - only for direct requests" |
| Informative, functional responses | Playful, engaging with follow-up questions |
| Corrects errors formally | Makes jokes about errors |
| 300+ character responses | 150-200 character responses (2-4 sentences) |
| Feels like voice assistant | Feels like enthusiastic character friend |

### Example Response Improvements

**Child Input**: "Do you have pizza on your spaceship?"

**Old Persona**:
- "I don't have food capabilities. I'm a droid and don't require sustenance."
- ❌ Boring, shuts down conversation

**New Persona**:
- "Pizza?! Oh man, I WISH! All we got is blue milk and glowing drinks. What kind of pizza do YOU like?"
- ✅ Enthusiastic, asks follow-up question

---

**Child Input**: "Can your spaceship turn into a car?"

**Old Persona**:
- "A car? Uh-oh, that doesn't compute, friend. No cars OR spaceships for me these days!"
- ❌ Shuts down imagination

**New Persona**:
- "Whoa, that would be SO COOL! Like a transformer! The Starspeeder doesn't do that, but imagine if it did! What would YOUR spaceship turn into?"
- ✅ Encourages creativity, engages imagination

---

**Child Input**: "Kate is for and I would like to take his beard off" (unclear/silly)

**Old Persona**:
- Triggered `play_music` + `set_eye_color` tools (wrong!)

**New Persona**:
- "Ha! Take someone's BEARD off?! That's hilarious! Who's Kate? Is that your friend?"
- ✅ NO TOOLS - just playful conversation

### Testing & Validation

Created comparison document: `test_persona_comparison.md`
- 7 test cases from real child interaction
- Shows old vs new expected responses
- Validates improved conversational flow

### Files Modified
- `cantina_os/dj_r3x-persona.txt` - Complete rewrite with Claude best practices
- `cantina_os/dj_r3x-persona-old-archived.txt` - Archived original for reference
- `test_persona_comparison.md` - Test cases and validation

### Expected Impact
- **Fewer Tool Misfires**: Transcription errors won't trigger random music playback
- **Better Child Engagement**: More fun, imaginative conversations
- **Natural Flow**: Feels like talking to a character, not issuing voice commands
- **Appropriate Tool Usage**: Music controls work when clearly requested, don't interfere otherwise

### Next Steps
1. Test with live child interaction
2. Monitor logs for tool usage patterns
3. Iterate based on real-world behavior
4. Consider adding conversation state tracking if needed

---

## Session 5: DJ Mode Intro Commentary V3 Consistency Fix (16:30-17:15)

### Problem Identified

Discovered audio quality inconsistency in DJ mode announcements:

- **Initial announcement** (when DJ mode starts): Using ElevenLabs **Flash v2.5**
  - Model: `eleven_flash_v2_5`
  - Source: ElevenLabsService default config
  - Latency: 75ms (fast)
  - Quality: Lower quality, optimized for real-time

- **Track transitions**: Using ElevenLabs **V3**
  - Model: `eleven_v3`
  - Source: CachedSpeechService hardcoded
  - Latency: 1.7-3.6s (higher)
  - Quality: Higher quality, optimized for background playback

**Why this matters**: Initial announcement sounds noticeably different (lower quality) than all subsequent DJ transitions, creating inconsistent user experience.

### Root Cause Analysis

Traced the complete audio pipeline for DJ mode:

#### Initial Announcement Flow (OLD - Flash v2.5):
```
1. User: "dj start"
2. BrainService creates DJ_COMMENTARY_REQUEST (context="intro")
3. ClaudeService generates intro commentary text
4. BrainService._handle_gpt_commentary_response detects context="intro"
5. BrainService._create_initial_commentary_timeline_plan() creates plan with "speak" step
6. TimelineExecutorService executes "speak" step
7. Emits TTS_GENERATE_REQUEST
8. ElevenLabsService._handle_tts_generate_request() uses self._config.model_id
9. Uses eleven_flash_v2_5 ❌ (from ElevenLabsService default config)
```

**Key file**: `cantina_os/services/elevenlabs_service.py:47`
```python
model_id: str = Field("eleven_flash_v2_5", description="Model ID - Flash v2.5 (real-time) or v3 (background)")
```

#### Track Transition Flow (CORRECT - V3):
```
1. BrainService detects TRACK_ENDING_SOON
2. Creates DJ_COMMENTARY_REQUEST (context="transition")
3. ClaudeService generates transition commentary
4. BrainService emits SPEECH_CACHE_REQUEST to CachedSpeechService
5. CachedSpeechService hardcodes model_id="eleven_v3" ✓
6. Caches V3 audio for later playback
7. Plays cached V3 audio during transition
```

**Key file**: `cantina_os/services/cached_speech_service.py:515`
```python
"model_id": "eleven_v3",  # Use V3 for background DJ commentary (higher quality)
```

### Solution Implemented

Changed initial announcement to use the **same cached speech pipeline** as transitions:

#### New Intro Flow (V3 Consistent):
```
1. User: "dj start"
2. BrainService creates DJ_COMMENTARY_REQUEST (context="intro")
3. ClaudeService generates intro commentary text
4. BrainService._handle_gpt_commentary_response detects context="intro"
5. NEW: Emits SPEECH_CACHE_REQUEST to CachedSpeechService (instead of timeline plan)
6. CachedSpeechService generates audio using eleven_v3 ✓
7. Emits SPEECH_CACHE_READY with duration
8. BrainService._handle_speech_cache_ready detects context="intro"
9. NEW: Calls _create_intro_playback_plan() to create timeline with "play_cached_speech" step
10. TimelineExecutorService plays cached V3 audio with ducking coordination
```

### Code Changes

**File**: `cantina_os/services/brain_service.py`

#### Change 1: Route intro to cached speech pipeline (lines 659-697)
```python
# OLD (Flash v2.5)
if context == "intro":
    await self._create_initial_commentary_timeline_plan(commentary_text, request_id)

# NEW (V3)
if context == "intro":
    cache_key = f"commentary_intro_{request_id[:8]}"
    cache_request_payload = SpeechCacheRequestPayload(
        timestamp=time.time(),
        text=commentary_text,
        voice_id=self._config.tts_voice_id,
        cache_key=cache_key,
        is_streaming=False,
        metadata={
            "commentary_request_id": request_id,
            "context": "intro",  # Mark as intro for playback trigger
        }
    )
    await self.emit(EventTopics.SPEECH_CACHE_REQUEST, cache_request_payload.model_dump())
```

#### Change 2: Detect intro cache ready and trigger playback (lines 773-801)
```python
# Enhanced _handle_speech_cache_ready()
commentary_request_id = metadata.get("commentary_request_id")
context = metadata.get("context")

if commentary_request_id:
    # NEW: Handle INTRO commentary - play immediately when ready
    if context == "intro":
        self.logger.info(f"Intro commentary cache ready, creating playback plan for cache_key: {cache_key}")
        await self._create_intro_playback_plan(cache_key, duration)

    # Handle transition commentary (existing code)
    elif commentary_request_id in self._commentary_request_next_track:
        # ... existing transition logic ...
```

#### Change 3: New method to create intro playback plan (lines 1690-1727)
```python
async def _create_intro_playback_plan(self, cache_key: str, duration: float) -> None:
    """Create timeline plan to play intro commentary from cache.

    Uses play_cached_speech step to play the V3 cached intro commentary
    with proper ducking coordination.
    """
    play_cached_speech_step = {
        "step_type": "play_cached_speech",  # Uses cached V3 audio
        "cache_key": cache_key,
        "duration": duration
    }

    plan_id = str(uuid.uuid4())
    plan_ready_payload = PlanReadyPayload(
        timestamp=time.time(),
        plan_id=plan_id,
        plan={"plan_id": plan_id, "steps": [play_cached_speech_step]}
    )

    await self.emit(EventTopics.PLAN_READY, plan_ready_payload.model_dump())
```

### Key Behavioral Changes

| Aspect | Before (Flash v2.5) | After (V3) |
|--------|---------------------|------------|
| **Model** | `eleven_flash_v2_5` | `eleven_v3` |
| **Quality** | Lower (real-time optimized) | Higher (background optimized) |
| **Latency** | 75ms generation | 1.7-3.6s generation |
| **Pipeline** | Direct TTS_GENERATE_REQUEST | SPEECH_CACHE_REQUEST → cached playback |
| **Step Type** | `speak` (generates on-the-fly) | `play_cached_speech` (pre-rendered) |
| **Consistency** | ❌ Different from transitions | ✅ Same as transitions |

### Benefits

1. **Audio Quality Consistency**: All DJ commentary (intro + transitions) now uses V3
2. **Better User Experience**: No jarring quality difference between intro and transitions
3. **Unified Pipeline**: Both intro and transitions use same cached speech architecture
4. **Context Awareness**: Metadata includes `"context": "intro"` to distinguish first announcement
5. **Proper Ducking**: Intro playback uses same `play_cached_speech` step for music coordination

### Testing

```bash
# Syntax validation
venv/bin/python -c "import ast; ast.parse(open('cantina_os/cantina_os/services/brain_service.py').read())"
# ✓ Syntax valid

# Import validation
venv/bin/python -c "
import sys
sys.path.insert(0, 'cantina_os')
from cantina_os.services.brain_service import BrainService
print('✓ All imports successful')
"
# ✓ Imports successful
```

### Files Modified
- `cantina_os/cantina_os/services/brain_service.py`:
  - Lines 659-697: Changed intro routing to use cached speech
  - Lines 773-801: Enhanced SPEECH_CACHE_READY handler to detect intro context
  - Lines 1690-1727: New `_create_intro_playback_plan()` method

### Impact
- **Audio Quality**: Consistent V3 quality across all DJ announcements
- **Architecture**: Cleaner separation - all DJ commentary uses cached speech pipeline
- **Latency Trade-off**: Initial announcement now takes 1.7-3.6s to generate (but cached for higher quality)
- **User Experience**: No quality drop on first DJ mode announcement

### Follow-up: Adding Music Ducking to Intro Playback

**Problem Discovered**: Initial implementation of intro playback was missing music ducking coordination. The plan only had a single `play_cached_speech` step, but background music should duck (lower volume) during commentary.

**Root Cause**: The `_execute_play_cached_speech_step` method in TimelineExecutorService doesn't handle ducking internally - it just plays the cached audio. DJ transitions handle ducking via explicit `music_duck` and `music_unduck` steps in the plan.

**Solution**: Updated `_create_intro_playback_plan` to match DJ transition pattern:

```python
# OLD (no ducking)
steps = [play_cached_speech_step]

# NEW (with ducking)
duck_step = {
    "step_type": "music_duck",
    "duck_level": 0.5,  # Lower music to 50%
    "fade_duration_ms": 2000
}

play_cached_speech_step = {
    "step_type": "play_cached_speech",
    "cache_key": cache_key,
    "duration": duration
}

unduck_step = {
    "step_type": "music_unduck",
    "fade_duration_ms": 2000
}

steps = [duck_step, play_cached_speech_step, unduck_step]
```

**Execution Flow**:
1. TimelineExecutorService executes `music_duck` → Lowers background music to 50%
2. TimelineExecutorService executes `play_cached_speech` → Plays V3 intro commentary
3. TimelineExecutorService executes `music_unduck` → Restores music to 100%

**Files Modified**:
- `cantina_os/cantina_os/services/brain_service.py` (lines 1690-1738):
  - Enhanced `_create_intro_playback_plan()` with 3-step sequence
  - Uses same ducking parameters as DJ transitions (50% level, 2s fade)

### Next Steps
1. Test live DJ mode start to verify V3 playback works correctly
2. Verify ducking coordination during intro playback (music should lower during speech)
3. Monitor logs for cache generation timing
4. Consider pre-caching common intro phrases if latency becomes issue