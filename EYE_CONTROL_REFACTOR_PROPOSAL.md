# Eye Control Architecture Refactor Proposal

## Executive Summary

The current eye control system sends atomic commands (color, pattern, brightness) separately from Python to Arduino, leading to race conditions, lost commands, and unpredictable state transitions. This proposal outlines a complete refactor using:

1. **State-based command protocol** (not individual property changes)
2. **Priority queue system** on Arduino
3. **State machine** with base/override layers
4. **Atomic state updates** (single command contains all properties)
5. **Coordinated Python-side dispatch** with throttling

---

## Current Architecture Problems

### Race Conditions
- **Color + Pattern timing**: Two commands sent 10ms apart can arrive out of order
- **Overlapping events**: Mode change + speech event + sentiment can conflict
- **Rapid event spam**: Interim transcriptions trigger 10 pattern changes/sec
- **Pattern reversion**: Temporary overrides (speaking) lost to base state changes

### No Priority System
- All commands treated equally (mode change = interim transcription)
- No way to say "speaking overrides sentiment overrides mode"
- No "return to previous state" after temporary animations

### No Command Queue
- Arduino serial buffer only 64 bytes
- Commands processed immediately (no deferral)
- No atomic updates (3 commands to change color+pattern+brightness)

---

## Proposed Architecture: Layered State Machine

### Concept: Priority Layers

Instead of sending individual commands, Arduino maintains a **layered state** where higher-priority layers override lower ones:

```
┌─────────────────────────────────────┐
│ Layer 3: OVERRIDE (highest)        │  ← Critical alerts, errors
│   - Error patterns                  │
│   - System alerts                   │
├─────────────────────────────────────┤
│ Layer 2: FOREGROUND                 │  ← Interactive events
│   - SPEAKING (during TTS)           │
│   - LISTENING (during STT)          │
│   - THINKING (during LLM)           │
├─────────────────────────────────────┤
│ Layer 1: MOOD (middle)              │  ← Sentiment/emotion
│   - HAPPY (positive sentiment)      │
│   - SAD (negative sentiment)        │
│   - ANGRY, SURPRISED, etc.          │
├─────────────────────────────────────┤
│ Layer 0: BASE (lowest)              │  ← System mode
│   - IDLE (warm yellow, breathing)   │
│   - INTERACTIVE (cyan, engaged)     │
│   - AMBIENT (purple, party mode)    │
└─────────────────────────────────────┘

Active Display = Highest Non-Empty Layer
```

### How It Works

**Example 1: Normal Flow**
```
1. System boots → Layer 0: IDLE (yellow breathing)
2. User clicks mouse → Layer 0: INTERACTIVE (cyan engaged)
3. User speaks → Layer 2: LISTENING (cyan pulsing) ← overrides base
4. LLM processing → Layer 2: THINKING (cyan rotating)
5. TTS speaking → Layer 2: SPEAKING (cyan speaking animation)
6. Speech ends → Layer 2 cleared → back to Layer 0: INTERACTIVE
7. Timeout → Layer 0: IDLE (yellow breathing)
```

**Example 2: Sentiment During Speech**
```
1. Base: INTERACTIVE (cyan)
2. User speaks → Layer 2: LISTENING
3. LLM detects positive sentiment → Layer 1: HAPPY (green breathing)
4. TTS starts → Layer 2: SPEAKING ← overrides mood layer
   Display: GREEN SPEAKING (mood color + foreground pattern)
5. Speech ends → Layer 2 cleared
   Display: GREEN BREATHING (mood layer now visible)
6. Mood timeout (5sec) → Layer 1 cleared
   Display: CYAN ENGAGED (base layer)
```

**Example 3: Error Override**
```
1. Base: INTERACTIVE, Mood: HAPPY, Foreground: SPEAKING
2. Critical error → Layer 3: ERROR (red fast pulse)
   Display: RED FAST PULSE (overrides everything)
3. Error cleared → Layer 3 cleared
   Display: Returns to SPEAKING with HAPPY color
```

---

## New Command Protocol

### State Command Format (JSON over serial)

Instead of: `C00FFFF\n` + `E\n` + `B128\n` (3 commands)

Use single atomic command:
```json
{
  "cmd": "SET_STATE",
  "layer": 2,
  "state": {
    "pattern": "SPEAKING",
    "color": {"r": 0, "g": 255, "b": 0},
    "brightness": 128,
    "duration_ms": 0
  }
}
```

### Command Types

#### 1. SET_STATE (primary)
```json
{
  "cmd": "SET_STATE",
  "layer": 0-3,        // Priority layer
  "state": {
    "pattern": "IDLE|LISTENING|THINKING|SPEAKING|ENGAGED|HAPPY|SAD|ANGRY|ERROR",
    "color": {"r": 0-255, "g": 0-255, "b": 0-255},
    "brightness": 0-255,
    "duration_ms": 0   // 0=permanent, >0=auto-clear after N ms
  }
}
```

#### 2. CLEAR_LAYER
```json
{
  "cmd": "CLEAR_LAYER",
  "layer": 1    // Clear mood layer (return to base)
}
```

#### 3. CLEAR_ALL
```json
{
  "cmd": "CLEAR_ALL",
  "reset_to_idle": true
}
```

#### 4. QUERY_STATE (debugging)
```json
{
  "cmd": "QUERY_STATE"
}
// Response: Current active layer + all layer states
```

---

## Arduino Implementation

### Data Structures

```cpp
// Maximum 4 priority layers
#define NUM_LAYERS 4

struct EyeState {
  char pattern;           // 'I', 'L', 'T', 'S', 'E', 'H', 'D', 'A'
  CRGB color;            // RGB color
  uint8_t brightness;    // 0-255
  unsigned long endTime; // millis() when to auto-clear (0=never)
  bool active;           // Is this layer currently set?
};

EyeState layers[NUM_LAYERS];  // Layer 0 = base, 3 = highest priority

// Current active display state (computed from layers)
EyeState currentDisplay;
int activeLayer = -1;  // Which layer is currently being displayed
```

### Command Queue

```cpp
#define QUEUE_SIZE 8

struct Command {
  String json;
  unsigned long receivedAt;
};

class CircularQueue {
private:
  Command queue[QUEUE_SIZE];
  int head = 0;
  int tail = 0;
  int count = 0;

public:
  bool enqueue(String json) {
    if (count >= QUEUE_SIZE) return false;  // Queue full
    queue[tail] = {json, millis()};
    tail = (tail + 1) % QUEUE_SIZE;
    count++;
    return true;
  }

  bool dequeue(String &json) {
    if (count == 0) return false;  // Queue empty
    json = queue[head].json;
    head = (head + 1) % QUEUE_SIZE;
    count--;
    return true;
  }

  int size() { return count; }
};

CircularQueue commandQueue;
```

### State Machine Logic

```cpp
void updateActiveDisplay() {
  // Find highest-priority active layer
  activeLayer = -1;
  for (int i = NUM_LAYERS - 1; i >= 0; i--) {
    if (layers[i].active) {
      activeLayer = i;
      break;
    }
  }

  if (activeLayer >= 0) {
    currentDisplay = layers[activeLayer];
  } else {
    // No active layers, use default IDLE
    currentDisplay = {
      .pattern = 'I',
      .color = CRGB(255, 200, 50),
      .brightness = 128,
      .endTime = 0,
      .active = true
    };
  }

  // Apply current display state to LEDs
  FastLED.setBrightness(currentDisplay.brightness);
  setPattern(currentDisplay.pattern);  // Updates LEDs based on pattern
}

void checkLayerTimeouts() {
  unsigned long now = millis();
  bool changed = false;

  for (int i = 0; i < NUM_LAYERS; i++) {
    if (layers[i].active && layers[i].endTime > 0 && now >= layers[i].endTime) {
      layers[i].active = false;  // Auto-clear expired layer
      changed = true;
    }
  }

  if (changed) {
    updateActiveDisplay();  // Recompute active display
  }
}
```

### Main Loop (Non-Blocking)

```cpp
void loop() {
  // 1. Read incoming serial data (non-blocking)
  if (Serial.available() > 0) {
    readIncomingCommand();  // Accumulate JSON, enqueue when complete
  }

  // 2. Process queued commands (1 per loop iteration)
  if (commandQueue.size() > 0) {
    String cmd;
    if (commandQueue.dequeue(cmd)) {
      processCommand(cmd);  // Parse JSON, update layer state
    }
  }

  // 3. Check for layer timeouts (auto-clear temporary states)
  checkLayerTimeouts();

  // 4. Update LED animations (pattern-specific animation logic)
  updateEyeAnimation();  // Runs pattern animation for currentDisplay

  // No delay() - runs as fast as possible (~5000 loops/sec)
}
```

---

## Python-Side Changes

### Unified State Dispatcher

```python
class EyeLightControllerService:
    def __init__(self, ...):
        self._command_queue = asyncio.Queue()  # Internal command queue
        self._last_command_time = {}  # Throttle tracking per layer
        self._throttle_ms = {
            0: 500,   # Base layer: max 1 change per 500ms
            1: 200,   # Mood layer: max 1 change per 200ms
            2: 100,   # Foreground: max 1 change per 100ms
            3: 0      # Override: no throttle
        }

    async def _set_layer_state(
        self,
        layer: int,
        pattern: str,
        color: Tuple[int, int, int],
        brightness: int = 128,
        duration_ms: int = 0
    ):
        """Send atomic state update to specific layer."""

        # Throttle check
        now = time.time() * 1000
        last_time = self._last_command_time.get(layer, 0)
        throttle = self._throttle_ms[layer]

        if throttle > 0 and (now - last_time) < throttle:
            self.logger.debug(f"Throttled layer {layer} command (too soon)")
            return

        self._last_command_time[layer] = now

        # Build JSON command
        cmd = {
            "cmd": "SET_STATE",
            "layer": layer,
            "state": {
                "pattern": pattern,
                "color": {"r": color[0], "g": color[1], "b": color[2]},
                "brightness": brightness,
                "duration_ms": duration_ms
            }
        }

        # Send to Arduino
        await self.adapter.send_json_command(cmd)

    async def _clear_layer(self, layer: int):
        """Clear a specific layer (return to lower priority)."""
        cmd = {"cmd": "CLEAR_LAYER", "layer": layer}
        await self.adapter.send_json_command(cmd)
```

### Event Handler Refactor

```python
async def _handle_mode_change(self, payload: Dict[str, Any]):
    """System mode changed (IDLE, INTERACTIVE, AMBIENT)."""
    new_mode = payload.get("new_mode")

    if new_mode == "IDLE":
        # Layer 0 (BASE): Yellow breathing
        await self._set_layer_state(
            layer=0,
            pattern="IDLE",
            color=(255, 200, 50),
            brightness=128
        )
    elif new_mode == "INTERACTIVE":
        # Layer 0 (BASE): Cyan engaged
        await self._set_layer_state(
            layer=0,
            pattern="ENGAGED",
            color=(0, 255, 255),
            brightness=128
        )

async def _handle_speech_started(self, payload: Dict[str, Any]):
    """TTS speech started - set foreground layer."""
    # Layer 2 (FOREGROUND): Speaking pattern
    # Keep current color (from mood or base layer)
    await self._set_layer_state(
        layer=2,
        pattern="SPEAKING",
        color=self._get_current_color(),  # Inherit from lower layer
        brightness=128,
        duration_ms=0  # Manually cleared by speech_ended
    )

async def _handle_speech_ended(self, payload: Dict[str, Any]):
    """TTS speech ended - clear foreground layer."""
    await self._clear_layer(layer=2)

async def _handle_sentiment(self, payload: Dict[str, Any]):
    """LLM sentiment analyzed - set mood layer."""
    sentiment = payload.get("sentiment", "neutral")

    if sentiment == "positive":
        # Layer 1 (MOOD): Green breathing for 5 seconds
        await self._set_layer_state(
            layer=1,
            pattern="HAPPY",
            color=(0, 255, 0),
            brightness=128,
            duration_ms=5000  # Auto-clear after 5 seconds
        )
```

---

## Benefits of Refactored Architecture

### 1. **No More Race Conditions**
- Single atomic command contains all state (color + pattern + brightness)
- Queue ensures commands processed in order
- Throttling prevents event spam

### 2. **Predictable State Transitions**
- Layer priority always enforced (speaking > sentiment > mode)
- Temporary overrides automatically revert when cleared
- No "lost" state from overlapping events

### 3. **Ambient Animations Support**
- Base layer can run continuous ambient patterns (rainbow, party mode)
- Foreground layer overrides during interaction
- Returns to ambient when idle

### 4. **Debugging & Observability**
- QUERY_STATE command shows all layer states
- Python can log exactly what Arduino should be displaying
- Clear layer hierarchy makes behavior predictable

### 5. **Future Extensions**
- Add Layer 4 for "accessibility" (high contrast mode)
- Add pattern interpolation (smooth color transitions)
- Add synchronized multi-device support (multiple LED strips)

---

## Migration Strategy

### Phase 1: Arduino Refactor (Week 1)
1. Implement CircularQueue class
2. Add EyeState struct and layer array
3. Add JSON command parsing (using ArduinoJson library)
4. Implement updateActiveDisplay() and checkLayerTimeouts()
5. **Keep legacy single-char commands** for backwards compatibility

### Phase 2: Python Adapter (Week 1)
1. Add send_json_command() method to SimpleEyeAdapter
2. Keep existing set_pattern(), set_color() methods (translate to JSON internally)
3. No changes to EyeLightControllerService initially

### Phase 3: Python Service Refactor (Week 2)
1. Add _set_layer_state() and _clear_layer() methods
2. Refactor event handlers to use layers
3. Add throttling logic
4. Test thoroughly with real interactions

### Phase 4: Remove Legacy Protocol (Week 3)
1. Remove single-char command support from Arduino
2. Remove old set_pattern/set_color methods from adapter
3. Clean up Python service

---

## Alternative: Simpler "Command Bundling" Approach

If full state machine is too complex, a simpler option:

### Bundled Command Protocol
```
{
  "pattern": "S",
  "color": [0, 255, 0],
  "brightness": 128
}
```

**Benefits:**
- Atomic updates (no race conditions)
- Simpler than full state machine
- Still need queue for reliability

**Drawbacks:**
- No priority layers (still have race conditions between events)
- No auto-revert for temporary states
- Python still needs coordination logic

---

## Recommendation

**Go with Full State Machine (Layered Approach)** because:

1. **Solves all current problems**: Race conditions, priority, temporary states
2. **Future-proof**: Supports ambient animations, multi-device sync
3. **Cleaner Python code**: Each event handler maps to ONE layer
4. **Better debugging**: Clear visibility into state hierarchy
5. **Industry standard**: Same pattern used in game engines, UI frameworks

The upfront complexity pays off with much simpler behavior and easier debugging.

---

## Arduino Mega Resources

**Available:**
- **RAM**: 8KB (plenty for 4 layers + 8-command queue)
- **Flash**: 256KB (plenty for ArduinoJson library)
- **Speed**: 16MHz (can process 5000+ loop iterations/sec)
- **Serial Buffer**: 64 bytes (queue handles overflow)

**Libraries Needed:**
- **ArduinoJson** (6.x): ~6KB flash, lightweight JSON parsing
- **FastLED** (already using): LED control

**No RTOS needed** - simple circular queue + state machine is sufficient

---

## Questions to Consider

1. **Do we want ambient animations in IDLE mode?** (e.g., slow rainbow breathing)
2. **Should sentiment colors fade back to mode color?** (5-second auto-clear)
3. **Do we need synchronized LED updates?** (e.g., multiple Arduino boards)
4. **Should we log state transitions?** (useful for debugging AI behavior)
5. **Do we want "accessibility mode"?** (high contrast, slower animations for photosensitivity)

---

## Next Steps

1. **Review this proposal** - decide if full state machine or simpler bundling
2. **Prototype Arduino queue** - test with simple JSON commands
3. **Benchmark performance** - ensure 60fps animation with queue processing
4. **Update dev log** - document decision and timeline
5. **Create implementation tasks** - break down into manageable chunks

---

**Status**: 🟡 Proposal Ready for Review
**Estimated Effort**: 2-3 weeks (phased migration, backward compatible)
**Risk**: Low (can keep legacy protocol during transition)
