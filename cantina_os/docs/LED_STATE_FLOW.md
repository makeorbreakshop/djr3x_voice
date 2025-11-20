# DJ R3X LED State Flow Documentation

## Complete State Machine Walkthrough

### 1. IDLE Mode (Orange Eyes, Blue Mouth)
**Trigger**: System starts or `idle` command
- **Eyes**: Solid bright orange (255, 120, 0) - Pattern 'I'
- **Mouth**: Blue baseline glow (0, 100, 255) at 20/255 brightness
- **Behavior**: Static, peaceful state

### 2. ENGAGED Mode (Cyan Eyes, Golden Mouth)
**Trigger**: `engage` command → INTERACTIVE mode
- **Eyes**: Cyan breathing (0, 255, 255) - Pattern 'E'
- **Mouth**:
  - **Silent**: COMPLETELY BLACK (special case for dramatic effect)
  - **Speaking**: Golden yellow (255, 200, 0) blooms with amplitude
- **Behavior**: Ready for interaction, dramatic mouth effect

### 3. LISTENING Mode (Cyan Eyes, Dark Blue Mouth)
**Trigger**: User clicks mouse or starts speaking
- **Eyes**: Cyan with pulsing rotation - Pattern 'L'
- **Mouth**: Dark blue (0, 50, 200) with small baseline glow
- **Behavior**: Active listening animation

### 4. THINKING Mode (Cyan Eyes, Purple Mouth)
**Trigger**: After user stops speaking (recording ends)
- **Eyes**: Cyan rotating dot animation - Pattern 'T'
- **Mouth**: Purple (128, 0, 255) with small baseline glow
- **Behavior**: Processing user input, 1.5s animation

### 5. SPEAKING Mode (Cyan Eyes, Active Mouth)
**Trigger**: TTS audio starts playing
- **Eyes**: Cyan with gentle pulse - Pattern 'S'
- **Mouth**:
  - In ENGAGED: Golden yellow (255, 200, 0) pulsing with audio amplitude
  - In IDLE: Blue (0, 100, 255) pulsing with audio amplitude
  - Amplitude: M000-M255 commands at 10Hz (fire-and-forget)
- **Behavior**: Mouth blooms from center outward based on amplitude

### 6. FLASH Pattern (Green Confirmation)
**Trigger**: After speech ends or command completion
- **Eyes**: Two rapid green pulses (0.3s total)
- **Mouth**: Maintains current color
- **Behavior**: Auto-returns to previous pattern

## State Transition Flow

```
IDLE (orange/blue)
    ↓ engage command
ENGAGED (cyan/black mouth)
    ↓ click mouse
LISTENING (cyan pulse/dark blue)
    ↓ stop recording
THINKING (cyan rotate/purple)
    ↓ LLM response ready
SPEAKING (cyan pulse/golden bloom)
    ↓ speech ends
FLASH (green pulse) → returns to ENGAGED
    ↓ disengage command
IDLE (orange/blue)
```

## Critical Implementation Details

### Arduino Buffer Configuration (MEGA 2560)
```cpp
// MUST be defined BEFORE Arduino.h include!
#define SERIAL_RX_BUFFER_SIZE 256  // Was 64
#define SERIAL_TX_BUFFER_SIZE 256
```

### Mouth Amplitude Control
- **Fire-and-forget**: No response wait to prevent buffer overflow
- **Update rate**: 10Hz (100ms intervals)
- **Command format**: M000 to M255
- **Python implementation**: `set_mouth_amplitude()` in SimpleEyeAdapter

### Mode-Specific Mouth Behavior
```cpp
// Special ENGAGED mode handling
if (currentPattern == 'E' && mouthAmplitude == 0) {
    // Completely black mouth when silent
    for (int i = 0; i < NUM_MOUTH_LEDS; i++) {
        mouthLeds[i] = CRGB::Black;
    }
    return;
}

// Other modes have baseline glow
int idleBrightness = 0;
if (currentPattern != 'E' && mouthAmplitude == 0) {
    idleBrightness = 20;  // Small glow
}
```

### Mouth LED Animation (Center-Out Bloom)
Physical V-shape layout:
```
LED 0 (top left)     LED 7 (top right)
LED 1                LED 6
LED 2                LED 5
LED 3                LED 4
      └──────────┘ (bottom middle)
```

Animation sequence:
1. **Center LEDs (1, 6)**: Bloom first, brightest
2. **Corner LEDs (0, 7)**: Bloom upward at 30% threshold
3. **Lower-middle (2, 5)**: Bloom downward at 25% threshold
4. **Bottom (3, 4)**: Bloom last at 50% threshold

### Reset Behavior
After DJ R3X finishes speaking:
1. **FLASH pattern** plays (green confirmation)
2. **Returns to ENGAGED** pattern (cyan eyes)
3. **Mouth returns to black** (in ENGAGED mode)
4. **Ready for next interaction**

## Testing Commands

### Arduino Serial Monitor
- `I` - IDLE pattern
- `E` - ENGAGED pattern
- `L` - LISTENING pattern
- `T` - THINKING pattern
- `S` - SPEAKING pattern
- `F` - FLASH pattern
- `M255` - Full mouth open
- `M000` - Mouth closed
- `TALK` - 10-second test animation
- `AUTO` - Buffer test mode

### CantinaOS CLI Commands
- `engage` - Enter interactive mode
- `disengage` or `idle` - Return to idle
- `eye test` - Run eye test sequence
- `eye pattern <pattern>` - Set specific pattern
- `eye status` - Show current state

## Key Fixes Applied

1. **256-byte Serial Buffer**: Prevents command overflow during rapid updates
2. **Fire-and-Forget Mouth Commands**: No response wait prevents blocking
3. **ENGAGED Black Mouth**: Dramatic effect when silent
4. **Mode-Specific Colors**: Each state has distinct visual identity
5. **Single FastLED.show()**: Prevents eye/mouth update conflicts

## Common Issues & Solutions

### Issue: Mouth not responding
- Check serial buffer size is defined BEFORE Arduino.h
- Verify fire-and-forget is enabled (no wait_for_response)
- Confirm 10Hz update rate (not faster)

### Issue: Eyes/mouth colors wrong
- Verify mode transitions in Python service
- Check setPattern() color assignments in Arduino
- Ensure mouthColor updates with pattern changes

### Issue: No black mouth in ENGAGED
- Check special case in updateMouth() for pattern 'E'
- Verify amplitude is actually 0 when silent
- Confirm ENGAGED pattern is set correctly

This complete flow ensures DJ R3X has expressive, mode-aware LED animations that respond naturally to interaction states.