# DJ R3X LED Behavior Specification

## Overview
This document defines the exact LED behavior requirements for DJ R3X's eye and mouth animations. It serves as the complete specification for refactoring the Arduino LED control system.

---

## System Architecture

### Hardware
- **Eyes**: 14 WS2812B LEDs (2 rings of 7 each)
  - Left eye: LEDs 0-6 (center at 0)
  - Right eye: LEDs 7-13 (center at 7)
- **Mouth**: 8 WS2812B LEDs in V-shape
  - Physical layout: V-shaped with LEDs 0,7 at top corners, 3,4 at bottom center
  - Animation direction: Center-out bloom (1,6 → 0,7 → 2,5 → 3,4)

### Communication
- Serial: 115200 baud over USB
- Buffer: 256 bytes (MEGA 2560 requirement)
- Protocol: Single character patterns, multi-character for colors/amplitude

---

## Core Behavioral States

### 1. IDLE State
**Purpose**: Default resting state when system is not actively engaged

**Eyes**:
- Color: Warm orange (RGB: 255, 120, 0)
- Animation: Gentle breathing (±15% brightness variation over 3.5 seconds)
- Blinking: Random intervals (3-7 seconds between blinks)
  - Blink duration: 200ms total (100ms close, 100ms open)
  - Synchronized: Both eyes blink together

**Mouth**:
- Color: Blue (RGB: 0, 100, 255)
- State: Minimal baseline glow (20/255 brightness)
- Only center LEDs (1,6) dimly lit when silent

---

### 2. ENGAGED State
**Purpose**: System is ready for interaction but not actively processing

**Eyes**:
- Color: Bright cyan (RGB: 0, 255, 255)
- Animation: Very subtle breathing (±10% brightness, barely noticeable)
- NO blinking in this mode
- Should feel "alert" and "ready"

**Mouth**:
- Color: Golden yellow (RGB: 255, 200, 0)
- **CRITICAL**: When silent (amplitude=0), mouth is COMPLETELY BLACK
- This creates dramatic contrast when speech begins
- When speaking, golden yellow blooms from center outward

---

### 3. LISTENING State
**Purpose**: Actively recording user input

**Eyes**:
- Color: Maintains current color (cyan if from ENGAGED)
- Animation: Heartbeat pulse
  - 60 BPM rhythm (1 pulse per second)
  - Quick rise (250ms) followed by slow fall (750ms)
  - 50-100% brightness range

**Mouth**:
- Color: Dark blue (RGB: 0, 50, 200)
- State: Small baseline glow indicating "input mode"

---

### 4. THINKING State
**Purpose**: Processing user input, generating response

**Eyes**:
- Background: Completely black
- Center pupils: Bright white (always visible)
- Animation: Counter-rotating cyan dots
  - One bright cyan dot per eye on outer ring
  - Dots rotate in opposite directions
  - ~1.5 seconds per full rotation
  - Optional: Dim trailing dots for motion blur effect

**Mouth**:
- Color: Purple (RGB: 128, 0, 255)
- State: Dim baseline glow

---

### 5. SPEAKING State
**Purpose**: AI is responding with speech

**Eyes**:
- Color: Maintains current color
- Animation: Gentle outward pulse
  - Simulates "energy" emanating while speaking
  - 70-100% brightness variation
  - 2-second cycle time

**Mouth**:
- Color: Depends on previous state
  - If from ENGAGED: Golden yellow
  - If from IDLE: Blue
- Animation: Real-time amplitude visualization
  - Driven by audio amplitude (0-255 range)
  - Center-out bloom pattern
  - Threshold-based LED activation:
    - 0%: All LEDs black (ENGAGED) or dim baseline (IDLE)
    - 1-20%: Center LEDs (1,6) only
    - 20-35%: Add corner LEDs (0,7)
    - 35-60%: Add lower-middle LEDs (2,5)
    - 60-100%: Add bottom LEDs (3,4)

---

### 6. FLASH State
**Purpose**: Confirmation feedback after speech completes

**Eyes**:
- Two rapid green pulses
- Total duration: 300ms
- Auto-returns to previous pattern and color

**Mouth**:
- Maintains current state

---

## Emotion States (Lower Priority)

### HAPPY
- Eyes: Green with random yellow sparkles

### SAD
- Eyes: Blue with slow breathing

### ANGRY
- Eyes: Red with fast pulsing

---

## Critical Behavioral Rules

### Color Management
1. **Pattern-Driven Colors**: Each pattern change SHOULD set its own default color
2. **No Python Color Override**: Arduino should own the color for each state
3. **Transitions**: Color changes happen WITH pattern changes, not separately

### Mouth Amplitude Rules
1. **Fire-and-Forget**: Mouth commands (Mnnn) don't wait for response
2. **Update Rate**: Maximum 10Hz (100ms between updates)
3. **Reset to Zero**: When speech ends, explicitly send M000
4. **Mode-Specific Baseline**:
   - ENGAGED + silent = BLACK (no LEDs lit)
   - Other modes + silent = Dim glow on center LEDs

### Animation Timing
1. **Non-Blocking**: All animations use millis() timing, never delay()
2. **Single FastLED.show()**: One update per loop iteration
3. **Variable Frame Rates**:
   - Flash/Blinks: 100 FPS (10ms updates)
   - Normal animations: 20 FPS (50ms updates)

---

## State Transition Flow

```
IDLE (orange/blue)
    ↓ [engage command]
ENGAGED (cyan/black mouth) ← Color MUST change here!
    ↓ [click to record]
LISTENING (cyan pulse/dark blue)
    ↓ [stop recording]
THINKING (rotating dots/purple) ← Must show clear rotation!
    ↓ [LLM response ready]
SPEAKING (cyan pulse/golden bloom) ← Mouth responds to amplitude!
    ↓ [speech ends]
FLASH (green confirmation)
    ↓ [auto-return]
ENGAGED (cyan/black mouth) ← Mouth MUST be black when silent!
```

---

## Known Issues to Fix

1. **ENGAGED Color Not Changing**: Currently stays orange instead of changing to cyan
2. **Thinking Animation Invisible**: Dots not showing or too dim
3. **Mouth Not Resetting**: Stays lit after speech instead of going black/dim
4. **Excessive Pulsing**: ENGAGED breathing effect too strong
5. **Python/Arduino Conflict**: Color commands from Python interfering with pattern colors

---

## Testing Requirements

Each state must be testable independently via serial commands:
- Pattern changes should immediately show correct color
- Mouth amplitude sweeps should show staged LED activation
- Transition sequences should flow naturally
- Silent mouth states must be visually distinct between modes

---

## Success Criteria

1. **ENGAGED mode**: Eyes turn cyan immediately, mouth goes black when silent
2. **THINKING mode**: Clear rotating dots visible on black background
3. **SPEAKING mode**: Mouth animates smoothly with audio, returns to baseline when done
4. **State transitions**: Clean, immediate, no color bleeding between states
5. **Overall feel**: Responsive, alive, clear visual feedback for each state

---

This specification defines the complete desired behavior. The implementation should prioritize simplicity and reliability over complex features.