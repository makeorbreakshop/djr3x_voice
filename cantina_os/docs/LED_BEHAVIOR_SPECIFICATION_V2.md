# DJ R3X LED Behavior Specification V2
*Refined specification focusing on layered architecture and clean state management*

## Design Philosophy
**Layered Animation System**: Think of the LED system as having three independent layers that can be combined:
1. **Base State Layer**: The fundamental pattern (IDLE, ENGAGED, etc.)
2. **Effect Layer**: Overlays like blinking, pulsing, breathing
3. **Response Layer**: Real-time responses to inputs (mouth amplitude, flash confirmations)

This separation allows clean state management while maintaining flexibility for future enhancements.

---

## Core Behavioral States (Base Layer)

### 1. IDLE State
**Purpose**: Default resting state, robot is "sleeping" or at rest

**Eyes**:
- Base Color: Warm orange (RGB: 255, 120, 0)
- Breathing: Gentle ±15% brightness over 3.5 seconds
- Blinking: **8-15 seconds between blinks** (more natural, less frequent)
  - Blink duration: 200ms (100ms close, 100ms open)
  - Both eyes synchronized

**Mouth**:
- Color: **Very dark blue** (RGB: 0, 30, 100) - darker than before
- State: **Extremely subtle glow** (10/255 brightness max)
- Only center LEDs (1,6) with barely visible presence
- *Feeling: Like a sleeping breath indicator*

---

### 2. ENGAGED State
**Purpose**: Alert and ready for interaction

**Eyes**:
- Base Color: **Bright cyan** (RGB: 0, 255, 255) - MUST switch from orange
- Breathing: **Same as IDLE** (±15% over 3.5 seconds) for continuity
- NO blinking - maintains alert presence

**Mouth**:
- **COMPLETELY BLACK when silent** - zero light output
- Color when speaking: Golden yellow (RGB: 255, 200, 0)
- *Dramatic contrast when speech begins*

---

### 3. LISTENING State
**Purpose**: Actively recording user input

**Eyes**:
- Color: Maintains cyan from ENGAGED
- Animation: **Dynamic pulse** (more visible than before)
  - Stronger variation: 30-100% brightness
  - Heartbeat pattern: Quick rise (200ms), slow fall (800ms)
  - 60 BPM rhythm

**Mouth**:
- **NO CHANGE** - stays black/dark depending on previous state
- *Mouth is passive during listening*

---

### 4. THINKING State
**Purpose**: Processing input, generating response

**Eyes**:
- Background: Black
- Center pupils: **Bright white** (RGB: 255, 255, 255)
- Rotating dots: **Bright cyan** (RGB: 0, 255, 255)
  - One dot per eye on outer ring
  - Counter-rotating (opposite directions)
  - 1.5 seconds per rotation

**Mouth**:
- **NO CHANGE** - remains in previous state
- *Mouth is passive during thinking*

---

### 5. SPEAKING State
**Purpose**: AI is delivering response

**Eyes**:
- Color: Maintains current (cyan if from ENGAGED)
- Animation: Gentle pulse (70-100% brightness, 2-second cycle)

**Mouth - CRITICAL REFINEMENTS**:
- **THIS IS THE ONLY STATE WHERE MOUTH IS ACTIVE**
- Amplitude range: 0-255 from Python
- **Better dynamics needed**:
  ```
  Amplitude 0:     BLACK (complete silence)
  Amplitude 1-50:  Only center LEDs, very dim
  Amplitude 51-100: Center bright, corners starting
  Amplitude 101-150: Center + corners bright, middle starting
  Amplitude 151-200: Center + corners + middle bright
  Amplitude 201-255: All LEDs proportionally bright
  ```
- **Apply logarithmic scaling**: Lower amplitudes need more sensitivity
- **When speech ends**: Immediately return to BLACK

---

### 6. FLASH State
**Purpose**: Confirmation after speech

**Eyes**:
- Two green pulses (300ms total)
- Auto-returns to ENGAGED

**Mouth**:
- Stays BLACK (no activity)

---

## Architecture Recommendations

### 1. State Machine Design
```
StateMachine {
  BaseState currentState;     // IDLE, ENGAGED, LISTENING, etc.
  EffectFlags activeEffects;  // BREATHING, BLINKING, PULSING
  ResponseData responses;      // mouthAmplitude, flashTrigger
}
```

### 2. Clean Command Protocol
**Single Responsibility Commands**:
- `S[state]` - Set base state (SI=idle, SE=engaged, SL=listening, etc.)
- `M[nnn]` - Mouth amplitude (0-255)
- `F` - Trigger flash
- `R` - Reset to defaults

**NO COLOR COMMANDS FROM PYTHON** - Arduino owns all colors based on state

### 3. Update Loop Structure
```
loop() {
  processCommands();      // Non-blocking serial read
  updateBaseState();      // Core pattern animation
  applyEffects();        // Breathing, blinking overlays
  processResponses();    // Mouth amplitude, flash
  FastLED.show();        // Single update point
}
```

### 4. Timing Management
```
struct AnimationTimer {
  unsigned long lastUpdate;
  unsigned long interval;
  int step;
};

// Separate timers for each animation layer
AnimationTimer breathingTimer = {0, 50, 0};   // 20 FPS
AnimationTimer blinkTimer = {0, 10, 0};        // 100 FPS when active
AnimationTimer rotationTimer = {0, 30, 0};     // 33 FPS for smooth rotation
```

### 5. Mouth Amplitude Processing
```
// Logarithmic scaling for better dynamics
float scaledAmplitude = sqrt(rawAmplitude / 255.0);

// Threshold-based activation with smooth transitions
if (scaledAmplitude > 0.0) {
  centerBrightness = scaledAmplitude * 255;
}
if (scaledAmplitude > 0.2) {
  cornerBrightness = (scaledAmplitude - 0.2) / 0.8 * 200;
}
// etc...
```

---

## Python-Arduino Communication Strategy

### Clean Separation of Concerns

**Python Responsibilities**:
1. Detect system mode changes (IDLE → ENGAGED)
2. Send state transition commands
3. Stream mouth amplitude during speech
4. Trigger confirmation flash

**Arduino Responsibilities**:
1. Own all color definitions
2. Manage all animations
3. Handle smooth transitions
4. Maintain visual consistency

### Command Flow Example
```
User: "engage"
Python → Arduino: "SE"        (Set state to ENGAGED)
[Arduino changes eyes to cyan, mouth to black]

User: [clicks to record]
Python → Arduino: "SL"        (Set state to LISTENING)
[Arduino starts pulse animation]

User: [stops recording]
Python → Arduino: "ST"        (Set state to THINKING)
[Arduino shows rotating dots]

AI: [starts speaking]
Python → Arduino: "SS"        (Set state to SPEAKING)
Python → Arduino: "M128"      (Continuous amplitude updates at 10Hz)
Python → Arduino: "M200"
Python → Arduino: "M050"
...
Python → Arduino: "M000"      (Speech ends)
Python → Arduino: "F"         (Trigger flash)
[Arduino flashes green, returns to ENGAGED]
```

---

## Implementation Priorities

### Phase 1: Core States (Must Work Perfectly)
1. IDLE with proper orange and subtle blue mouth
2. ENGAGED with cyan eyes and BLACK mouth
3. State transitions with correct colors

### Phase 2: Animations
1. Breathing effect (shared between IDLE/ENGAGED)
2. Thinking rotation
3. Listening pulse

### Phase 3: Responses
1. Mouth amplitude with better dynamics
2. Flash confirmation
3. Blinking at correct intervals

### Phase 4: Polish
1. Smooth transitions between states
2. Effect overlays
3. Future animation additions

---

## Testing Strategy

### Unit Tests (Serial Monitor)
- `SI` → Should show orange eyes, dark blue mouth
- `SE` → Should show cyan eyes, BLACK mouth
- `SL` → Should pulse current color
- `ST` → Should show rotating dots
- `SS` + `M128` → Should show mouth response
- `M000` → Should return mouth to BLACK

### Integration Tests
Run full sequence: `SI` → wait → `SE` → `SL` → `ST` → `SS` → `M` commands → `F`

Each transition should be immediate and correct.

---

## Success Metrics

1. **State Clarity**: Each state is visually distinct
2. **Color Consistency**: Colors change predictably with states
3. **Response Time**: < 50ms from command to visual change
4. **Mouth Dynamics**: Natural speech visualization with good contrast
5. **System Stability**: No flaking, no random behaviors

---

## Common Pitfalls to Avoid

1. **Don't**: Let Python control colors
2. **Don't**: Use blocking delays
3. **Don't**: Mix state logic with animation logic
4. **Don't**: Update LEDs multiple times per loop
5. **Don't**: Store state in animation variables

**Do**: Keep state machine clean and animations separate

---

This specification emphasizes clean architecture with layered animations that can grow in complexity without breaking core functionality.