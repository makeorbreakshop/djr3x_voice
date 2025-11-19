# Wake Word Detection & Rolling Buffer Research Findings

**Date:** November 19, 2025
**Research Focus:** Best practices for wake word detection with pre-roll audio buffering

---

## Executive Summary

Research into wake word detection systems reveals several critical best practices for implementing robust, low-latency systems with pre-roll audio buffering. Key findings:

1. **`collections.deque` with `maxlen`** is the industry-standard approach for audio circular buffers in Python
2. **Frame-based processing** (80-250ms chunks) optimizes latency vs. efficiency trade-off
3. **Rolling buffer with np.roll()** is common but `deque` is simpler and more efficient for most use cases
4. **Pre-roll buffering** is essential for capturing full phrases that include the wake word
5. **Queue-based separation** of audio capture from processing prevents blocking

---

## 1. Rolling Buffer Implementation: Best Practices

### Recommended Approach: `collections.deque` with `maxlen`

**Why `deque`?**
- **O(1) performance** for append and pop operations (critical for real-time audio)
- **Built-in circular behavior** when `maxlen` is specified
- **Automatic overflow handling** - oldest samples dropped when buffer is full
- **Thread-safe** for basic operations (important for audio callback threads)
- **Simpler code** compared to manual `np.roll()` implementations

**Alternative: NumPy `np.roll()`**
- Better for very large numeric arrays (>10,000 samples)
- More complex to implement correctly
- Common in ML-focused implementations
- Example: `data = np.roll(data, -chunk_samples)` to shift buffer left

**Comparison:**
```python
# ✅ RECOMMENDED: deque approach (simple, efficient)
from collections import deque

audio_buffer = deque(maxlen=32000)  # 2 seconds @ 16kHz
audio_buffer.extend(new_audio_chunk)  # Auto-drops oldest samples

# ❌ ALTERNATIVE: np.roll() approach (more complex)
import numpy as np

data = np.zeros(32000)
chunk_samples = 16000
data = np.roll(data, -chunk_samples)  # Shift left
data[-chunk_samples:] = new_chunk  # Append new data
```

**Recommendation for DJ R3X:**
Use `collections.deque` with `maxlen` for simplicity and performance. Our use case (2-second buffer @ 16kHz = 32,000 samples) is well within the sweet spot for `deque`.

---

## 2. Audio Processing Parameters

### Industry Standard Settings

Based on analysis of multiple production systems (Porcupine, openWakeWord, Argo-Robot):

| Parameter | Recommended Value | Reasoning |
|-----------|------------------|-----------|
| Sample Rate | **16,000 Hz** | Standard for speech processing, matches Deepgram/Porcupine |
| Channels | **1 (mono)** | Wake word detection doesn't benefit from stereo |
| Bit Depth | **16-bit PCM** | Industry standard, balances quality and size |
| Frame Duration | **80-250ms** | Porcupine uses 512 samples (32ms), openWakeWord uses 80ms |
| Pre-roll Buffer | **1.5-3 seconds** | Captures full wake word phrase + command |

**DJ R3X Current Config:**
- ✅ Sample rate: 16,000 Hz (matches recommendation)
- ✅ Channels: 1 (mono)
- ✅ Encoding: linear16 PCM
- 🔨 **To Add:** Pre-roll buffer: 2 seconds (32,000 samples)

### Frame Size Trade-offs

**Smaller frames (80-100ms):**
- ✅ Lower latency (faster detection)
- ❌ More CPU overhead (more frequent callbacks)
- ✅ Better for real-time systems

**Larger frames (250ms+):**
- ✅ Better efficiency (fewer callbacks)
- ❌ Higher latency (detection takes longer)
- ❌ Not ideal for conversational AI

**Porcupine Recommendation:**
- Uses **512 samples** (32ms @ 16kHz)
- Optimized for their neural network architecture
- We must match this exactly for Porcupine compatibility

---

## 3. Pre-Roll Buffer Strategy

### Why Pre-Roll is Critical

**Problem:** Wake word detection has inherent latency (50-100ms). By the time detection fires, the user may have already said additional words.

**Example Timeline:**
```
t=0ms:    User starts: "Hey DJ Rex, play..."
t=100ms:  Porcupine detects "Hey DJ Rex" (detection fires)
t=200ms:  User still speaking: "...Cantina Band"
```

**Without pre-roll:**
- WebSocket opens at t=100ms
- Only captures "...Cantina Band"
- Misses "Hey DJ Rex play"

**With pre-roll:**
- Buffer maintains last 2 seconds of audio
- At t=100ms, send buffered audio to Deepgram
- Captures full phrase: "Hey DJ Rex play Cantina Band"

### Recommended Pre-Roll Duration

**Industry Standards:**
- **Argo-Robot:** 2.944 seconds (neural network input window)
- **openWakeWord:** 80ms frames with rolling context
- **Best Practice:** 1.5-3 seconds

**DJ R3X Recommendation:**
- **2.0 seconds** @ 16kHz = 32,000 samples
- Rationale:
  - Captures wake word + 1-2 words of command
  - Not too large (memory efficient)
  - Matches Deepgram's typical phrase duration

### Implementation Pattern

**Queue-Based Architecture** (from Argo-Robot):
```python
import queue
from collections import deque

# Audio buffer (rolling pre-roll)
audio_buffer = deque(maxlen=32000)  # 2 seconds

# Queue for thread-safe communication
audio_queue = queue.Queue()

def audio_callback(in_data, frame_count, time_info, status):
    """PyAudio callback - runs in separate thread."""
    # Add to queue for processing
    audio_queue.put(in_data)

    # Add to rolling buffer for pre-roll
    pcm = struct.unpack_from("h" * frame_count, in_data)
    audio_buffer.extend(pcm)

    return (in_data, pyaudio.paContinue)

# Main loop processes queue without blocking audio
while True:
    audio_chunk = audio_queue.get()
    keyword_index = porcupine.process(audio_chunk)

    if keyword_index >= 0:
        # Wake word detected!
        preroll_audio = list(audio_buffer)  # Copy buffer
        emit_wake_word_detected(preroll_audio)
```

**Key Benefits:**
1. Audio capture never blocks (runs in callback thread)
2. Processing happens in main thread (doesn't block audio)
3. Pre-roll buffer always has last 2 seconds ready
4. Thread-safe communication via queue

---

## 4. Performance & Efficiency

### Memory Considerations

**Pre-roll buffer size:**
- 2 seconds @ 16kHz mono 16-bit = 32,000 samples × 2 bytes = **64 KB**
- Negligible on modern hardware (Raspberry Pi 3 can handle 15-20 models simultaneously)

**Processing efficiency:**
- `deque.extend()` is O(k) where k = chunk size (~512 samples)
- Porcupine processing is O(1) per frame
- Total CPU: <5% on Raspberry Pi 3 (per openWakeWord benchmarks)

### Latency Targets

**Wake word detection latency:**
- Porcupine processing: **30-50ms**
- Audio callback overhead: **10-20ms**
- Total detection: **50-100ms** ✅

**Pre-roll buffer access:**
- `list(audio_buffer)` copy: **<1ms** for 32,000 samples
- Negligible compared to WebSocket opening (~300ms)

---

## 5. Threading & Synchronization

### Recommended Pattern: Callback + Queue

**From Argo-Robot and openWakeWord:**

**Option 1: PyAudio Callback (Recommended)**
```python
stream = pa.open(
    rate=16000,
    channels=1,
    format=pyaudio.paInt16,
    input=True,
    frames_per_buffer=512,
    stream_callback=audio_callback  # Runs in separate thread
)

stream.start_stream()
```

**Option 2: Blocking Read Loop**
```python
stream = pa.open(
    rate=16000,
    channels=1,
    format=pyaudio.paInt16,
    input=True,
    frames_per_buffer=512
)

while True:
    audio_data = stream.read(512, exception_on_overflow=False)
    # Process in main thread (blocks other operations)
```

**DJ R3X Current Implementation:**
- Uses **blocking read** in separate thread (`_audio_capture_thread`)
- Works fine but less efficient than callback pattern
- **Recommendation:** Keep current approach (don't fix what isn't broken), but consider callback for future optimization

### Thread Safety

**Queue for communication:**
- Use `queue.Queue()` (thread-safe) for callback → main thread communication
- Avoid shared mutable state (except pre-roll buffer, which is append-only in callback)

**Pre-roll buffer thread safety:**
- `deque` operations are mostly thread-safe for our use case
- Only callback writes (extends), only main thread reads (copies on detection)
- No locking needed for this access pattern

---

## 6. Wake Word Detection Best Practices

### Sensitivity Tuning

**From Porcupine documentation:**
- Default sensitivity: **0.5** (balanced)
- Lower (0.3-0.4): Fewer false positives, might miss wake word in noisy environments
- Higher (0.6-0.8): More sensitive, higher false positive rate

**DJ R3X Recommendation:**
- Start with **0.5** (default)
- Test in actual environment (with DJ mode music playing)
- Tune down if too many false positives

### Voice Activity Detection (VAD)

**From openWakeWord:**
- Adding VAD with threshold **significantly reduces false positives** in non-speech noise
- Optional preprocessing (Speex noise suppression) helps with constant background noise

**DJ R3X Consideration:**
- Porcupine has built-in noise robustness (trained on real-world data)
- May not need additional VAD initially
- Monitor false positive rate, add VAD if needed

### Multi-Stage Verification

**From security research:**
- **Temporal masking:** Ignore sub-50ms audio spikes (prevents adversarial attacks)
- **Cloud-based secondary check:** Reduces false accepts by 40%

**DJ R3X Approach:**
- Phase 1: Trust Porcupine detection (local only)
- Phase 2+: Can add Deepgram transcription verification if false positives are an issue
  - e.g., "If wake word detected, verify transcription contains 'DJ Rex'"

---

## 7. Deepgram Integration Patterns

### Wake Word + Deepgram

**Current Deepgram Discussion (GitHub Issue #582):**
- Deepgram **does not offer dedicated wake word detection**
- Recommendation: Use lightweight local model (Porcupine) + Deepgram for full transcription
- Continuous STT would work but is expensive and unnecessary

**Recommended Pattern:**
```
1. Local wake word (Porcupine) → Detects "Hey DJ Rex"
2. Emit wake word event → Trigger mode change
3. Open Deepgram WebSocket → Start streaming
4. Send pre-roll buffer → Deepgram gets full phrase
5. Continue live streaming → Rest of user's utterance
6. Flux VAD → Auto-detects end of turn
```

**DJ R3X Alignment:**
- ✅ This matches our planned architecture exactly
- ✅ Leverages Porcupine's strength (always-on wake word)
- ✅ Leverages Deepgram's strength (accurate transcription + Flux VAD)

---

## 8. Code Examples from Research

### Example 1: Simple deque Pre-Roll Buffer

```python
from collections import deque
import struct

# Create circular buffer for 2 seconds of audio @ 16kHz
preroll_buffer = deque(maxlen=32000)

def process_audio_chunk(audio_bytes, frame_length):
    """Process audio chunk and maintain pre-roll buffer."""
    # Unpack bytes to PCM samples
    pcm_samples = struct.unpack_from("h" * frame_length, audio_bytes)

    # Add to rolling buffer (auto-drops oldest samples when full)
    preroll_buffer.extend(pcm_samples)

    # Process with wake word detector
    keyword_index = porcupine.process(pcm_samples)

    if keyword_index >= 0:
        # Wake word detected! Get pre-roll audio
        preroll_audio = list(preroll_buffer)  # Convert deque to list
        handle_wake_word_detected(preroll_audio)
```

### Example 2: Queue-Based Callback Pattern

```python
import queue
import threading

audio_queue = queue.Queue()

def audio_callback(in_data, frame_count, time_info, status):
    """PyAudio callback - runs in audio thread."""
    # Put audio in queue for main thread
    audio_queue.put(in_data)

    # Add to pre-roll buffer
    pcm = struct.unpack_from("h" * frame_count, in_data)
    preroll_buffer.extend(pcm)

    return (in_data, pyaudio.paContinue)

# Start audio stream with callback
stream = pa.open(
    rate=16000,
    channels=1,
    format=pyaudio.paInt16,
    input=True,
    frames_per_buffer=512,
    stream_callback=audio_callback
)

stream.start_stream()

# Main loop processes without blocking audio
while stream.is_active():
    try:
        audio_chunk = audio_queue.get(timeout=1)
        # Process audio...
    except queue.Empty:
        continue
```

### Example 3: np.roll() Pattern (Alternative)

```python
import numpy as np

# Initialize buffer for 2.944 seconds @ 16kHz (from Argo-Robot)
feed_samples = int(2.944 * 16000)  # 47,104 samples
data = np.zeros(feed_samples, dtype=np.int16)

chunk_samples = 16000  # 1 second chunks

def process_chunk(new_chunk):
    """Roll buffer and append new chunk."""
    global data

    # Shift data left (drop oldest samples)
    data = np.roll(data, -chunk_samples)

    # Append new chunk to end
    data[-chunk_samples:] = new_chunk

    # Now 'data' contains last 2.944 seconds
    # Process for wake word detection...
```

---

## 9. Implementation Recommendations for DJ R3X

Based on research findings, here are specific recommendations for our Phase 1 implementation:

### Architecture Decisions

| Component | Recommendation | Rationale |
|-----------|---------------|-----------|
| **Buffer Type** | `collections.deque(maxlen=32000)` | Simpler, faster, built-in circular behavior |
| **Buffer Size** | 2.0 seconds (32,000 samples) | Captures wake word + command phrase |
| **Processing Pattern** | Keep current threading (blocking read) | Already works, no need to change |
| **Queue** | Optional (not critical for first version) | Can add later if callback pattern needed |
| **Frame Size** | 512 samples (Porcupine requirement) | Matches Porcupine's expected input |

### Code Structure

```python
class PorcupineWakeWordService(BaseService):
    def __init__(self, event_bus, config=None):
        super().__init__("porcupine_wake_word", event_bus)

        # Pre-roll buffer (2 seconds @ 16kHz)
        self._preroll_buffer = deque(maxlen=32000)

        # Porcupine instance
        self._porcupine = None

        # PyAudio
        self._pa = None
        self._audio_stream = None

        # Control flags
        self._detection_active = True
        self._running = False

    async def _start(self):
        """Start wake word detection."""
        # Initialize Porcupine
        self._porcupine = pvporcupine.create(
            access_key=os.getenv("PICOVOICE_ACCESS_KEY"),
            keyword_paths=["cantina_os/wake_word/DJ-Rex_en_mac_v3_0_0.ppn"]
        )

        # Start audio capture thread
        self._running = True
        self._audio_thread = threading.Thread(target=self._audio_loop)
        self._audio_thread.start()

    def _audio_loop(self):
        """Audio processing loop (runs in separate thread)."""
        # Open PyAudio stream
        self._pa = pyaudio.PyAudio()
        self._audio_stream = self._pa.open(
            rate=16000,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self._porcupine.frame_length
        )

        while self._running:
            # Read audio frame
            pcm_bytes = self._audio_stream.read(
                self._porcupine.frame_length,
                exception_on_overflow=False
            )

            # Unpack to PCM samples
            pcm = struct.unpack_from(
                "h" * self._porcupine.frame_length,
                pcm_bytes
            )

            # Add to pre-roll buffer (always, even when paused)
            self._preroll_buffer.extend(pcm)

            # Process only if detection active
            if self._detection_active:
                keyword_index = self._porcupine.process(pcm)

                if keyword_index >= 0:
                    # Wake word detected!
                    self._handle_detection()

    def _handle_detection(self):
        """Handle wake word detection."""
        # Copy pre-roll buffer
        preroll_audio = list(self._preroll_buffer)

        # Emit event
        payload = WakeWordDetectedPayload(
            keyword="dj_rex",
            confidence=1.0,  # Porcupine doesn't provide confidence
            preroll_audio=preroll_audio,
            preroll_duration_ms=2000
        )

        self._event_bus.emit(
            EventTopics.WAKE_WORD_DETECTED,
            payload.model_dump()
        )

        # Pause detection (will resume when mode changes back)
        self._detection_active = False
```

### Integration Points

**1. Subscribe to Mode Changes:**
```python
# In PorcupineWakeWordService._start()
self._event_bus.on(
    EventTopics.SYSTEM_MODE_CHANGE,
    self._handle_mode_change
)

async def _handle_mode_change(self, event_data):
    """Pause/resume detection based on mode."""
    new_mode = event_data.get('new_mode')

    if new_mode == SystemMode.INTERACTIVE:
        # Pause detection (already engaged)
        self._detection_active = False
    else:
        # Resume detection (ready for next wake word)
        self._detection_active = True
```

**2. Send Pre-Roll to Deepgram:**
```python
# In DeepgramDirectMicService
async def _handle_wake_word_detected(self, event_data):
    """Send pre-roll buffer to Deepgram after WebSocket opens."""
    preroll_audio = event_data.get('preroll_audio', [])

    # Wait for WebSocket to open (with timeout)
    await self._wait_for_websocket_ready(timeout_ms=500)

    # Convert samples to bytes
    preroll_bytes = struct.pack("h" * len(preroll_audio), *preroll_audio)

    # Send to Deepgram
    if self._dg_socket:
        self._dg_socket.send_media(preroll_bytes)
        self._logger.info(f"Sent pre-roll: {len(preroll_audio)} samples")
```

---

## 10. Testing Recommendations

### Unit Tests

**Buffer Management:**
```python
def test_preroll_buffer_size():
    """Verify buffer maintains exactly 2 seconds."""
    buffer = deque(maxlen=32000)

    # Add more than maxlen
    for i in range(50000):
        buffer.append(i)

    assert len(buffer) == 32000  # Should cap at maxlen
    assert buffer[0] == 18000  # Oldest sample should be 50000-32000

def test_buffer_fifo_order():
    """Verify FIFO behavior."""
    buffer = deque(maxlen=10)
    buffer.extend([1, 2, 3, 4, 5])
    buffer.extend([6, 7, 8, 9, 10])
    buffer.extend([11, 12])  # Should drop 1, 2

    assert list(buffer) == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
```

**Wake Word Detection:**
```python
def test_wake_word_triggers_event():
    """Verify event is emitted on detection."""
    service = PorcupineWakeWordService(mock_event_bus)

    # Simulate detection
    service._handle_detection()

    # Verify event emitted
    assert mock_event_bus.emit.called
    assert mock_event_bus.emit.call_args[0][0] == EventTopics.WAKE_WORD_DETECTED
```

### Integration Tests

**Pre-Roll Audio Capture:**
```python
async def test_preroll_sent_to_deepgram():
    """Verify pre-roll buffer sent to Deepgram."""
    # Setup services
    wake_word_service = PorcupineWakeWordService(event_bus)
    deepgram_service = DeepgramDirectMicService(event_bus)

    # Simulate wake word detection
    await wake_word_service._handle_detection()

    # Verify Deepgram received pre-roll
    await asyncio.sleep(0.5)  # Wait for async processing
    assert deepgram_service._preroll_received == True
```

### E2E Tests (Real API Keys Required)

**Full Wake Word Flow:**
1. Say "Hey DJ Rex, play Cantina Band" in one breath
2. Verify: Wake word detected within 100ms
3. Verify: Mode changes to INTERACTIVE
4. Verify: Deepgram transcription includes full phrase (not just "play Cantina Band")
5. Verify: Claude receives and processes command

**False Positive Test:**
1. Play background music for 5 minutes
2. Count false wake word detections
3. Target: < 1 false positive per hour

---

## 11. Key Takeaways

### Do's ✅

1. **Use `collections.deque` with `maxlen`** for pre-roll buffer (simple, efficient)
2. **Buffer 2-3 seconds** of audio before wake word detection
3. **Maintain standard audio params** (16kHz, mono, 16-bit PCM)
4. **Keep audio capture separate from processing** (threading or callback)
5. **Pause detection during INTERACTIVE mode** (save CPU, prevent false triggers)
6. **Send pre-roll buffer to Deepgram** after wake word detected
7. **Start with default sensitivity** (0.5), tune based on testing

### Don'ts ❌

1. **Don't use `np.roll()` unless you need NumPy-specific features** (deque is simpler)
2. **Don't block audio capture** with heavy processing
3. **Don't skip pre-roll buffer** (will miss first words of command)
4. **Don't use continuous Deepgram STT** for wake word detection (expensive, unnecessary)
5. **Don't over-tune sensitivity** before testing in real environment
6. **Don't forget to pause/resume detection** based on system mode

### Validation Checklist

Before deploying to DJ R3X:

- [ ] Pre-roll buffer size: 2 seconds (32,000 samples @ 16kHz)
- [ ] Buffer implementation: `deque(maxlen=32000)`
- [ ] Frame size matches Porcupine: 512 samples
- [ ] Audio params: 16kHz, mono, 16-bit PCM
- [ ] Pre-roll sent to Deepgram after WebSocket opens
- [ ] Detection pauses in INTERACTIVE mode
- [ ] False positive rate < 1% in test environment
- [ ] Wake word detection latency < 100ms
- [ ] Full phrase captured in transcription

---

## 12. References

### Research Sources

1. **Picovoice Porcupine** - Official wake word detection library
   - GitHub: https://github.com/Picovoice/porcupine
   - Best-in-class accuracy, lightweight, cross-platform

2. **openWakeWord** - Open-source wake word framework
   - GitHub: https://github.com/dscripka/openWakeWord
   - 80ms frame processing, melspectrogram features

3. **Argo-Robot Wake Word Detection** - Step-by-step implementation guide
   - GitHub: https://github.com/Argo-Robot/wake_word_detection
   - Rolling buffer with `np.roll()`, queue-based architecture

4. **Federico Sarrocco's Guide** - Wake word detection for AI robots
   - URL: https://federicosarrocco.com/blog/wakeword
   - Detailed callback implementation, real-time processing

5. **Deepgram Wake Word Discussion** - GitHub Issue #582
   - URL: https://github.com/orgs/deepgram/discussions/582
   - Confirms: Use local wake word + Deepgram for transcription

6. **Stack Overflow: Efficient Circular Buffer**
   - Best practices for `deque` vs `np.roll()`
   - Performance comparisons for audio streaming

7. **Picovoice Complete Guide to Wake Word** (2025)
   - URL: https://picovoice.ai/blog/complete-guide-to-wake-word/
   - Industry best practices, security considerations

---

## Conclusion

The research strongly validates our planned architecture:

1. ✅ **Porcupine for wake word detection** (local, efficient, accurate)
2. ✅ **`deque` for pre-roll buffer** (2 seconds, simple, fast)
3. ✅ **Deepgram for transcription** (not for wake word)
4. ✅ **Pre-roll buffer sent to Deepgram** (captures full phrase)

No major changes needed to our Phase 1 plan. The implementation recommendations above provide specific code patterns proven in production systems.

**Next Step:** Implement `PorcupineWakeWordService` using patterns documented in Section 9.

---

*Research completed: November 19, 2025*
