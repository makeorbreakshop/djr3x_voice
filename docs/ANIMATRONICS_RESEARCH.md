# Animatronics Control Research Findings

**Date:** November 21, 2025
**Research Focus:** Extending DJ R3X's LED control architecture to support servo-based animatronics
**Current System:** SimpleEyeAdapter V3 with Arduino state-based LED control

---

## Executive Summary

Research into animatronics control systems reveals that DJ R3X's existing event-driven architecture and Arduino serial communication protocol can be naturally extended to support servo-based mechanical movements. Key findings:

1. **Current LED Protocol is Animatronics-Ready:** The state-based command pattern (SI, SE, SL, etc.) can be extended to include servo position/animation commands
2. **PWM Servo Controllers** (like Adafruit PCA9685) allow controlling 16+ servos via I2C from Arduino
3. **JSON-Based Animation Data** is the industry standard for storing pre-programmed movement sequences
4. **Layered Control Model:** Similar to TimelineExecutorService, animatronics benefit from layered behaviors (idle breathing + active gestures)
5. **Real-Time Puppeteering + Pre-Programmed Sequences** both viable with event bus architecture
6. **Serial Bus Servos** offer simpler wiring than traditional PWM servos for complex builds

---

## 1. Current System Architecture Analysis

### LED Control Pattern (Baseline)

**Current Implementation:**
```
CantinaOS Event Bus
    ↓ SYSTEM_MODE_CHANGED
    ↓ SPEECH_SYNTHESIS_STARTED
    ↓ LLM_SENTIMENT_ANALYZED
EyeLightControllerService
    ↓ SimpleEyeAdapterV3 (serial communication)
Arduino (rex_face_v3_clean.ino)
    → State machine: IDLE, ENGAGED, LISTENING, THINKING, SPEAKING
    → Real-time mouth amplitude updates (Mnnn commands @ 10Hz)
    → FastLED library controls WS2812B LEDs
```

**Key Characteristics:**
- **State-based control:** Python sends high-level states (SI, SE, etc.), Arduino handles animation details
- **Fire-and-forget updates:** Mouth amplitude sent without ACK for low latency
- **Acknowledged state changes:** State commands return `+` or `-` for reliability
- **Layered animations:** Base state + breathing effect + blinking effect + response layer
- **Serial protocol:** Simple ASCII commands over 115200 baud

**Strengths for Animatronics Extension:**
✅ Event-driven architecture already handles timing coordination
✅ State machine pattern maps well to animatronic behaviors
✅ Serial protocol simple to extend with new command types
✅ Arduino-based design scales to multi-board systems
✅ Low latency achieved through fire-and-forget for high-frequency updates

---

## 2. Animatronics Hardware Options

### Option A: PWM Servo Controllers (Most Common)

**Hardware:**
- **Adafruit PCA9685** (16-channel PWM/Servo driver, $15)
  - I2C interface (only 2 pins needed)
  - Up to 62 boards = 992 servos on one I2C bus
  - 12-bit resolution (4096 steps per rotation)
  - External power supply for servos

- **Compatible Servos:**
  - Standard hobby servos: 0-180° rotation, PWM control
  - MG996R metal gear servo (15 kg-cm torque, $8-12)
  - High-precision servos for facial movements ($20-50)

**Example Wiring:**
```
Arduino Mega/Uno
    → SDA/SCL (I2C) → PCA9685 Board #1 (16 servos)
                    → PCA9685 Board #2 (16 servos)
                    → PCA9685 Board #3 (16 servos)
    → Pin 6 → Eye LEDs (existing)
    → Pin 5 → Mouth LEDs (existing)
```

**Pros:**
- Industry standard for animatronics
- Extensive Arduino library support (`Adafruit_PWMServoDriver`)
- Proven in thousands of animatronic projects
- Easy to source globally

**Cons:**
- Each servo needs dedicated signal wire
- More wiring complexity for 10+ servos
- Power management critical (servos draw significant current)

---

### Option B: Serial Bus Servos (Modern Alternative)

**Hardware:**
- **LX-16A Serial Bus Servos** ($15-25 each)
  - Daisy-chain connection (one serial line controls all)
  - Position feedback (can read current servo angle)
  - ID-based addressing (up to 253 servos per bus)
  - Built-in temperature/voltage monitoring

- **LewanSoul/Hiwonder Bus Servo Boards**
  - Pre-integrated servo controllers with USB/TTL serial
  - Software control (Hiwonder's "ActionGroupControl")

**Example Wiring:**
```
Arduino
    → TX/RX → Servo #1 → Servo #2 → Servo #3 → ... (daisy chain)
                  ↓         ↓         ↓
              (Head)   (Left Arm)  (Right Arm)
```

**Pros:**
- Minimal wiring (one bus for all servos)
- Position feedback enables closed-loop control
- Built-in diagnostics (temperature, load, voltage)
- Easier cable management for large builds

**Cons:**
- Higher cost per servo ($15-25 vs $8-12)
- More complex communication protocol
- Requires serial library (LewanSoul's LX-16A library)
- Less widespread hobbyist adoption

---

### Option C: High-End Harmonic Drive Servos (Disney-Style)

**Hardware:**
- MIL-spec Harmonic Drive actuators (~$1000 each)
- Used in Disney Audio-Animatronics (Avatar Na'vi Shaman, etc.)
- 30+ facial actuators for hyperrealistic expressions
- Proprietary control systems (DACS - Digital Animation Control System)

**Assessment:** **Not recommended** for DJ R3X homebrew project due to cost and complexity.

---

## 3. Control Protocol Design

### Proposed Command Extension

Extend current Arduino protocol to support servo commands while maintaining LED control:

**New Command Types:**

#### A. Single Servo Position (Real-Time Control)
```
Pss:aaaa    Set servo [ss] to angle [aaaa]
            ss: 00-99 (servo ID)
            aaaa: 0000-1800 (angle in 0.1° units, 0-180°)

Example: P05:0900    → Servo 5 to 90.0°
         P12:0000    → Servo 12 to 0.0° (min)
         P12:1800    → Servo 12 to 180.0° (max)
```

#### B. Multi-Servo Update (Synchronized Movement)
```
Gnnn:ss:aaaa,ss:aaaa,...    Group move (up to 16 servos)
            nnn: 000-999 (movement duration in ms)
            ss:aaaa pairs (servo:angle)

Example: G500:05:0900,12:1350,08:0450
         → Move servos 5, 12, 8 to new positions over 500ms
```

#### C. Animation Sequence Playback
```
Annn        Play animation sequence [nnn]
            nnn: 001-999 (animation ID stored in Arduino)

Example: A001    → Play "idle breathing" animation
         A042    → Play "head nod yes" gesture
         A103    → Play "pointing gesture"
```

#### D. Servo Calibration
```
Css:mmmm:xxxx    Calibrate servo [ss] min/max bounds
                 ss: servo ID
                 mmmm: min PWM value (e.g., 0150)
                 xxxx: max PWM value (e.g., 0600)

Example: C05:0150:0600    → Set servo 5 range to 150-600 PWM
```

#### E. Query Servo Position (Serial Bus Servos Only)
```
Qss        Query current position of servo [ss]
           Response: P05:0920\n (servo 5 at 92.0°)
```

**Backward Compatibility:**
All existing LED commands remain unchanged:
- `SI`, `SE`, `SL`, `ST`, `SS`, `SF` (state changes)
- `Mnnn` (mouth amplitude)
- `R` (reset)
- `?` (help)

---

### Protocol Example Session

```
# System starts
Arduino: READY

# Set initial idle state (existing LED system)
Python: SI
Arduino: +

# Add servo movements to "idle breathing"
Python: G3000:01:0900,02:1100,03:0700   # Slow movement over 3 seconds
Arduino: +

# User speaks (LED system responds)
Python: SE    # Engaged state
Arduino: +

Python: SL    # Listening state (LED pulse)
Arduino: +

# LLM processing - add head nod gesture
Python: A042  # Play "nod yes" animation
Arduino: +

# TTS starts - speaking animation (mouth + head movement)
Python: SS    # Speaking state (LED)
Arduino: +

Python: M128  # Mouth amplitude (fire-and-forget)
Python: P01:0950   # Head bob forward slightly (fire-and-forget)
Python: M186
Python: P01:0920   # Head bob back
Python: M052
Python: P01:0900   # Return to center
Python: M000

# Return to idle
Python: SI
Arduino: +
Python: A001  # Resume idle breathing animation
Arduino: +
```

---

## 4. Arduino Code Architecture

### Proposed Structure (Extending rex_face_v3_clean.ino)

**New Components:**

```cpp
// ============================================
// SERVO CONFIGURATION
// ============================================

#include <Adafruit_PWMServoDriver.h>

#define NUM_SERVOS 16
#define SERVO_FREQ 50  // Standard servo PWM frequency

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);  // I2C address

// Servo calibration data (min/max PWM values)
struct ServoConfig {
  uint16_t minPWM;
  uint16_t maxPWM;
  uint16_t currentAngle;  // Stored as angle * 10 (e.g., 900 = 90.0°)
};

ServoConfig servos[NUM_SERVOS];

// ============================================
// ANIMATION SYSTEM
// ============================================

struct AnimationFrame {
  uint16_t duration_ms;  // Time to reach these positions
  uint16_t positions[NUM_SERVOS];  // Angle * 10 for each servo
};

struct Animation {
  uint8_t numFrames;
  bool loop;
  AnimationFrame* frames;
};

// Pre-programmed animations stored in PROGMEM
Animation animations[MAX_ANIMATIONS];

// Current animation state
int currentAnimation = -1;
int currentFrame = 0;
unsigned long animationStartTime = 0;

// ============================================
// COMMAND PROCESSING
// ============================================

void handleServoCommand(char cmdType, String params) {
  switch(cmdType) {
    case 'P':  // Single servo position
      setSingleServo(params);
      break;
    case 'G':  // Group move
      setGroupServos(params);
      break;
    case 'A':  // Play animation
      playAnimation(params.toInt());
      break;
    case 'C':  // Calibrate servo
      calibrateServo(params);
      break;
    case 'Q':  // Query position
      queryServo(params.toInt());
      break;
  }
}

void setSingleServo(String params) {
  // Parse "05:0900" → servo 5, angle 90.0°
  int colonPos = params.indexOf(':');
  int servoId = params.substring(0, colonPos).toInt();
  int angle = params.substring(colonPos + 1).toInt();

  if (servoId >= 0 && servoId < NUM_SERVOS) {
    setServoAngle(servoId, angle);
  }
}

void setServoAngle(int id, int angle) {
  // Convert angle (0-1800) to PWM value (minPWM-maxPWM)
  ServoConfig& servo = servos[id];
  uint16_t pwm = map(angle, 0, 1800, servo.minPWM, servo.maxPWM);
  pwm.setPWM(id, 0, pwm);
  servo.currentAngle = angle;
}

void playAnimation(int animId) {
  if (animId >= 0 && animId < MAX_ANIMATIONS) {
    currentAnimation = animId;
    currentFrame = 0;
    animationStartTime = millis();
  }
}

void updateAnimations() {
  if (currentAnimation < 0) return;

  Animation& anim = animations[currentAnimation];
  AnimationFrame& frame = anim.frames[currentFrame];

  unsigned long elapsed = millis() - animationStartTime;

  if (elapsed >= frame.duration_ms) {
    // Move to next frame
    currentFrame++;

    if (currentFrame >= anim.numFrames) {
      if (anim.loop) {
        currentFrame = 0;
      } else {
        currentAnimation = -1;  // Animation complete
        return;
      }
    }

    // Apply new frame positions
    AnimationFrame& nextFrame = anim.frames[currentFrame];
    for (int i = 0; i < NUM_SERVOS; i++) {
      if (nextFrame.positions[i] != 0xFFFF) {  // 0xFFFF = no change
        setServoAngle(i, nextFrame.positions[i]);
      }
    }

    animationStartTime = millis();
  }
}

// ============================================
// MAIN LOOP ADDITION
// ============================================

void loop() {
  // Existing LED control
  processSerialCommands();
  updateBaseState();
  applyBreathingEffect();
  applyBlinkingEffect();
  updateMouth();
  updateFlash();
  FastLED.show();

  // NEW: Servo animation updates
  updateAnimations();
}
```

**Key Design Choices:**

1. **Layered System:** Servos operate independently from LEDs but share state machine
2. **Pre-Programmed Animations:** Stored in Arduino PROGMEM to reduce serial bandwidth
3. **Real-Time Override:** Single servo commands (P) override animations for puppeteering
4. **Calibration Storage:** Each servo's min/max PWM stored for consistent movement
5. **Non-Blocking:** Animation updates in `loop()` don't block LED rendering

---

## 5. Python Integration (CantinaOS)

### New Service: AnimatronicsControllerService

```python
from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.adapters.animatronic_adapter import AnimatronicAdapter

class AnimatronicsControllerService(BaseService):
    """
    Controls servo-based animatronics in coordination with LED system.

    Subscriptions:
        - SYSTEM_MODE_CHANGED: Trigger idle/active animations
        - SPEECH_SYNTHESIS_STARTED: Head gestures during speech
        - LLM_SENTIMENT_ANALYZED: Emotional gestures (nod, shake head)
        - SPEECH_SYNTHESIS_AMPLITUDE: Head bob sync with speech volume
        - CLI_COMMAND_ANIMATRONIC: Manual servo control

    Emits:
        - ANIMATRONIC_GESTURE_STARTED
        - ANIMATRONIC_GESTURE_COMPLETED
        - ANIMATRONIC_STATE_CHANGED
    """

    def __init__(self, event_bus, config=None):
        super().__init__(service_name="animatronics_controller", event_bus=event_bus)
        self._config = config or {}
        self._adapter = None

        # Gesture library
        self._gestures = {
            "idle_breathing": 1,
            "nod_yes": 42,
            "shake_no": 43,
            "shrug": 44,
            "point_left": 101,
            "point_right": 102,
            "wave_hello": 103,
        }

    async def _start(self):
        # Initialize adapter
        self._adapter = AnimatronicAdapter(
            serial_port=self._config.get("serial_port", "/dev/ttyACM1"),
            baud_rate=self._config.get("baud_rate", 115200)
        )

        await self._adapter.connect()

        # Subscribe to events
        self._event_bus.on(EventTopics.SYSTEM_MODE_CHANGED, self._handle_mode_change)
        self._event_bus.on(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_start)
        self._event_bus.on(EventTopics.LLM_SENTIMENT_ANALYZED, self._handle_sentiment)
        self._event_bus.on(EventTopics.CLI_COMMAND_ANIMATRONIC, self._handle_manual_command)

        # Start idle animation
        await self.play_gesture("idle_breathing")

    async def _handle_mode_change(self, payload):
        """Respond to system mode changes."""
        mode = payload.get("mode")

        if mode == "IDLE":
            await self.play_gesture("idle_breathing")
        elif mode == "INTERACTIVE":
            await self.stop_gesture()  # Prepare for expressive movements

    async def _handle_speech_start(self, payload):
        """Add subtle head movement during speech."""
        # Example: Slight head tilt forward
        await self._adapter.set_single_servo(1, 95.0)  # Servo 1 = head tilt

    async def _handle_sentiment(self, payload):
        """Respond with gesture based on sentiment."""
        sentiment = payload.get("sentiment", "neutral")

        if sentiment == "positive":
            await self.play_gesture("nod_yes")
        elif sentiment == "negative":
            await self.play_gesture("shake_no")
        elif sentiment == "uncertain":
            await self.play_gesture("shrug")

    async def play_gesture(self, gesture_name: str):
        """Play a pre-programmed gesture animation."""
        anim_id = self._gestures.get(gesture_name)
        if anim_id:
            await self._adapter.play_animation(anim_id)
            self._event_bus.emit(EventTopics.ANIMATRONIC_GESTURE_STARTED, {
                "gesture": gesture_name,
                "animation_id": anim_id
            })
```

### New Adapter: AnimatronicAdapter

```python
class AnimatronicAdapter:
    """
    Hardware adapter for servo control (similar to SimpleEyeAdapterV3).
    """

    def __init__(self, serial_port: str, baud_rate: int = 115200):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.connection = None

    async def connect(self) -> bool:
        """Connect to Arduino servo controller."""
        self.connection = serial.Serial(
            port=self.serial_port,
            baudrate=self.baud_rate,
            timeout=1.0
        )
        await asyncio.sleep(2)  # Wait for Arduino reset
        return True

    async def set_single_servo(self, servo_id: int, angle: float) -> bool:
        """Set single servo position (fire-and-forget)."""
        angle_int = int(angle * 10)  # Convert to tenths
        command = f"P{servo_id:02d}:{angle_int:04d}\n"
        self.connection.write(command.encode())
        return True

    async def set_group_servos(self, positions: dict, duration_ms: int = 500) -> bool:
        """
        Set multiple servos simultaneously.

        Args:
            positions: {servo_id: angle} dictionary
            duration_ms: Movement duration in milliseconds
        """
        pairs = []
        for servo_id, angle in positions.items():
            angle_int = int(angle * 10)
            pairs.append(f"{servo_id:02d}:{angle_int:04d}")

        command = f"G{duration_ms:03d}:{','.join(pairs)}\n"
        self.connection.write(command.encode())

        # Wait for ACK
        response = self.connection.readline().decode().strip()
        return response == "+"

    async def play_animation(self, animation_id: int) -> bool:
        """Play pre-programmed animation sequence."""
        command = f"A{animation_id:03d}\n"
        self.connection.write(command.encode())

        response = self.connection.readline().decode().strip()
        return response == "+"

    async def calibrate_servo(self, servo_id: int, min_pwm: int, max_pwm: int) -> bool:
        """Calibrate servo PWM range."""
        command = f"C{servo_id:02d}:{min_pwm:04d}:{max_pwm:04d}\n"
        self.connection.write(command.encode())

        response = self.connection.readline().decode().strip()
        return response == "+"
```

---

## 6. Animation Data Format (JSON)

### Industry Standard: Frame-Based JSON

**Example: Head Nod Gesture**

```json
{
  "animation_id": 42,
  "name": "nod_yes",
  "description": "Head nod affirmative gesture",
  "loop": false,
  "frames": [
    {
      "duration_ms": 300,
      "servos": {
        "1": 90.0,   // Head pitch: neutral
        "2": 90.0    // Head yaw: center
      }
    },
    {
      "duration_ms": 400,
      "servos": {
        "1": 105.0   // Head pitch: down
      }
    },
    {
      "duration_ms": 300,
      "servos": {
        "1": 85.0    // Head pitch: up slightly
      }
    },
    {
      "duration_ms": 400,
      "servos": {
        "1": 105.0   // Head pitch: down again
      }
    },
    {
      "duration_ms": 500,
      "servos": {
        "1": 90.0    // Head pitch: return to neutral
      }
    }
  ]
}
```

**Python → Arduino Compiler**

Pre-compile JSON animations into Arduino C++ arrays:

```python
# tools/compile_animations.py

import json

def compile_animation_to_cpp(json_file: str, output_file: str):
    """Convert JSON animation to Arduino PROGMEM C++ code."""

    with open(json_file, 'r') as f:
        anims = json.load(f)

    with open(output_file, 'w') as out:
        out.write("#include <avr/pgmspace.h>\n\n")

        for anim in anims:
            anim_id = anim['animation_id']
            frames = anim['frames']

            # Write frame data
            out.write(f"const AnimationFrame anim_{anim_id}_frames[] PROGMEM = {{\n")
            for frame in frames:
                duration = frame['duration_ms']
                servos = frame.get('servos', {})

                # Create position array (0xFFFF = no change)
                positions = ["0xFFFF"] * 16
                for servo_id, angle in servos.items():
                    positions[int(servo_id)] = str(int(angle * 10))

                out.write(f"  {{ {duration}, {{ {', '.join(positions)} }} }},\n")
            out.write("};\n\n")

            # Write animation struct
            loop_val = "true" if anim.get('loop', False) else "false"
            out.write(f"const Animation animation_{anim_id} PROGMEM = {{\n")
            out.write(f"  {len(frames)},\n")
            out.write(f"  {loop_val},\n")
            out.write(f"  (AnimationFrame*)anim_{anim_id}_frames\n")
            out.write("};\n\n")

# Usage:
# python tools/compile_animations.py animations.json arduino/animations_data.h
```

Include in Arduino sketch:
```cpp
#include "animations_data.h"

// In setup():
animations[42] = animation_42;  // nod_yes gesture
```

---

## 7. Integration with Existing Systems

### Timeline Coordination

AnimatronicsControllerService integrates with **TimelineExecutorService** for synchronized sequences:

```python
# Example: DJ commentary with gesture
from cantina_os.core.event_payloads import TimelinePlanPayload

plan = TimelinePlanPayload(
    layers={
        "ambient": [
            {"type": "music_fade", "target_volume": 50, "duration_ms": 2000}
        ],
        "foreground": [
            {"type": "animatronic_gesture", "gesture": "point_left", "delay_ms": 0},
            {"type": "speech_playback", "cache_key": "intro_track_42", "delay_ms": 500},
            {"type": "animatronic_gesture", "gesture": "idle_breathing", "delay_ms": 5000}
        ]
    }
)

event_bus.emit(EventTopics.PLAN_READY, plan.model_dump())
```

### Event Bus Topics (New)

```python
# cantina_os/core/event_topics.py

class EventTopics(str, Enum):
    # ... existing topics ...

    # Animatronics
    ANIMATRONIC_GESTURE_STARTED = "/animatronic/gesture/started"
    ANIMATRONIC_GESTURE_COMPLETED = "/animatronic/gesture/completed"
    ANIMATRONIC_STATE_CHANGED = "/animatronic/state/changed"
    ANIMATRONIC_SERVO_MOVED = "/animatronic/servo/moved"
    ANIMATRONIC_CALIBRATION_REQUESTED = "/animatronic/calibration/requested"
    CLI_COMMAND_ANIMATRONIC = "/cli/command/animatronic"
```

### CLI Commands (New)

```python
# cantina_os/services/cli_service.py additions

def _register_animatronic_commands(self):
    self.commands["gesture"] = self._cmd_gesture
    self.commands["servo"] = self._cmd_servo
    self.commands["anim"] = self._cmd_animation

async def _cmd_gesture(self, args):
    """Play a pre-programmed gesture: gesture <name>"""
    if not args:
        self._print("Available gestures: idle_breathing, nod_yes, shake_no, shrug, point_left, point_right, wave_hello")
        return

    gesture_name = args[0]
    self._event_bus.emit(EventTopics.CLI_COMMAND_ANIMATRONIC, {
        "command": "gesture",
        "gesture": gesture_name
    })
    self._print(f"Playing gesture: {gesture_name}")

async def _cmd_servo(self, args):
    """Move single servo: servo <id> <angle>"""
    if len(args) < 2:
        self._print("Usage: servo <id> <angle>")
        return

    servo_id = int(args[0])
    angle = float(args[1])

    self._event_bus.emit(EventTopics.CLI_COMMAND_ANIMATRONIC, {
        "command": "servo",
        "servo_id": servo_id,
        "angle": angle
    })
    self._print(f"Moving servo {servo_id} to {angle}°")
```

---

## 8. Real-World Implementation Examples

### Bottango Integration

**Bottango** is open-source software that provides:
- Visual animation timeline editor (similar to After Effects)
- Real-time puppeteering mode (control via gamepad/MIDI)
- Arduino driver code (pre-built C++ library)
- Serial protocol similar to our proposed design

**Could DJ R3X use Bottango?**

✅ **Yes, as alternative to custom Arduino code:**
- Replace custom animation system with Bottango's driver
- CantinaOS would send Bottango protocol commands instead
- Benefit: Professional animation tools, no Arduino coding needed
- Trade-off: Less integration with LED system, external dependency

**Recommendation:** Start with custom protocol (better integration), consider Bottango if animation authoring becomes bottleneck.

---

### Open Source Reference Projects

1. **GitHub: sonyhome/PWM_Servomotor_Animatronics_with_Adafruit_PCA9685**
   - Complete Arduino code for PCA9685 servo control
   - Menu system for tuning servo ranges
   - Real-time adjustment via serial commands
   - https://github.com/sonyhome/PWM_Servomotor_Animatronics_with_Adafruit_PCA9685

2. **GitHub: timhendriks93/blender-servo-animation**
   - Blender add-on to export animations as servo data
   - Outputs JSON + Arduino C++ header files
   - Frame-based animation format (similar to our proposal)
   - https://github.com/timhendriks93/blender-servo-animation

3. **Adafruit: Animating Animatronics Tutorial**
   - Hardware guide (servos, controllers, power supplies)
   - Bottango setup walkthrough
   - Best practices for mechanical linkages
   - https://learn.adafruit.com/animating-animatronics

---

## 9. Recommended Implementation Roadmap

### Phase 1: Proof of Concept (1-2 weeks)
**Goal:** Single servo responds to voice events

1. **Hardware:**
   - Order Adafruit PCA9685 board ($15)
   - Get 2-3 hobby servos (MG996R or similar, $8 each)
   - Connect to existing Arduino (separate serial port from LEDs, or add second Arduino)

2. **Arduino Code:**
   - Extend `rex_face_v3_clean.ino` with servo library
   - Implement `P` command (single servo position)
   - Test with serial monitor: `P00:0900` → servo moves to 90°

3. **Python Integration:**
   - Create `AnimatronicAdapter` (copy `SimpleEyeAdapterV3` structure)
   - Test from CLI: `servo 0 90` → servo moves

4. **Event Integration:**
   - Subscribe to `SPEECH_SYNTHESIS_STARTED`
   - Move servo when DJ speaks (proof of concept gesture)

**Success Criteria:** Servo moves in response to voice events, no impact on LED performance

---

### Phase 2: Animation System (2-3 weeks)
**Goal:** Pre-programmed gestures triggered by sentiment/mode

1. **Arduino Additions:**
   - Implement animation playback engine
   - Add `A` command (play animation)
   - Create 3 test animations in PROGMEM:
     - A001: Idle breathing (subtle head movement)
     - A042: Nod yes (3-frame sequence)
     - A043: Shake no (4-frame sequence)

2. **Python Service:**
   - Create `AnimatronicsControllerService`
   - Subscribe to `LLM_SENTIMENT_ANALYZED`
   - Map sentiments to gestures:
     - Positive → nod yes
     - Negative → shake no
     - Neutral → continue idle

3. **JSON Animation Format:**
   - Create `animations.json` with 5 gestures
   - Build Python compiler: `compile_animations.py`
   - Generate `animations_data.h` for Arduino

**Success Criteria:** DJ R3X nods/shakes head based on conversation sentiment

---

### Phase 3: Multi-Servo Coordination (3-4 weeks)
**Goal:** Complex gestures with multiple servos (head + arms)

1. **Hardware Expansion:**
   - Add 5-8 more servos (head pitch/yaw/roll, arms, hands)
   - Design mechanical linkages (3D print or cardboard prototype)
   - Power supply upgrade (servos draw 1-2A each under load)

2. **Arduino Updates:**
   - Implement `G` command (group servo move)
   - Test synchronized 4-servo movement
   - Add safety limits (prevent mechanical collisions)

3. **Gesture Library:**
   - Animate 10 gestures:
     - Point left/right/forward
     - Wave hello/goodbye
     - Shrug
     - Thinking pose (hand on chin)
     - Excited gesture (arms up)
     - Dismissive wave

4. **Timeline Integration:**
   - Add animatronic steps to `TimelineExecutorService`
   - Coordinate: music fade → gesture → speech → gesture

**Success Criteria:** DJ R3X performs multi-servo gestures during DJ mode transitions

---

### Phase 4: Puppeteering Mode (Optional, 2-3 weeks)
**Goal:** Real-time manual control for content creation

1. **Input Device:**
   - USB gamepad (Xbox/PS controller, ~$30)
   - Map analog sticks to servos (e.g., left stick = head pitch/yaw)

2. **Python Additions:**
   - Create `GamepadInputService` (using `pygame` or `inputs` library)
   - Emit servo position events at 20Hz
   - Record gesture sequences to JSON

3. **Recording System:**
   - CLI command: `record gesture my_new_wave`
   - Captures servo positions over time
   - Saves to `animations.json`
   - Compiles to Arduino code

**Success Criteria:** Can puppeteer DJ R3X in real-time, record new gestures without coding

---

## 10. Hardware Shopping List

### Minimal Setup (Phase 1)
| Item | Quantity | Cost | Link |
|------|----------|------|------|
| Adafruit PCA9685 | 1 | $15 | adafruit.com/product/815 |
| MG996R Servo | 3 | $24 | amazon.com (search "MG996R") |
| Jumper Wires | 1 set | $8 | Any electronics store |
| **Total** | | **$47** | |

### Full Setup (Phase 3)
| Item | Quantity | Cost | Link |
|------|----------|------|------|
| Adafruit PCA9685 | 1 | $15 | adafruit.com/product/815 |
| MG996R Servo (standard) | 8 | $64 | amazon.com |
| Micro Servo 9g (fingers) | 4 | $16 | amazon.com (search "SG90") |
| 5V 10A Power Supply | 1 | $25 | amazon.com |
| Arduino Mega 2560 | 1 | $35 | arduino.cc or clone on amazon |
| USB Gamepad (optional) | 1 | $30 | Xbox/PS controller |
| **Total** | | **$185** | (without gamepad: $155) |

### High-End Setup (Phase 3+)
| Item | Quantity | Cost | Notes |
|------|----------|------|-------|
| LX-16A Serial Bus Servo | 12 | $240 | Serial bus, position feedback |
| 7.4V 5A Power Supply | 1 | $30 | Higher voltage for bus servos |
| Custom 3D printed parts | - | $50-200 | Mechanical linkages |
| **Total** | | **$320-$470** | |

---

## 11. Key Considerations & Challenges

### Power Management
**Challenge:** Servos draw 1-2A each under load (180+ watts for 12 servos)
**Solution:**
- Separate power supply for servos (5V 10A minimum)
- Arduino/LEDs on separate supply (USB or 5V 2A)
- Common ground between supplies critical
- Consider staggered movements to reduce peak current

### Mechanical Design
**Challenge:** Servo movements need physical linkages to robot body
**Solution:**
- Start simple: Direct-mount servos (head on pan-tilt bracket)
- Prototype with cardboard before 3D printing
- Use servo horns and ball-link connections for joints
- Study existing animatronic builds (YouTube: "DIY animatronic head")

### Latency & Synchronization
**Challenge:** Servo movements slower than LEDs (100-500ms typical)
**Solution:**
- Pre-trigger gestures before speech (lookahead like CachedSpeechService)
- Use `G` command group moves for synchronized multi-servo
- Adjust TimelineExecutorService delays to account for servo slew rate

### Safety & Limits
**Challenge:** Servos can damage mechanisms if over-rotated
**Solution:**
- Software limits in Arduino (min/max angles per servo)
- Mechanical hard stops in design
- Calibration command (`C`) to set safe ranges
- Emergency stop command (`R`) returns all servos to neutral

### Arduino Memory Limits
**Challenge:** Animation data consumes SRAM/Flash
**Solution:**
- Store animations in PROGMEM (Flash) not SRAM
- Arduino Mega 2560: 8KB SRAM, 256KB Flash (plenty for 50+ animations)
- If needed: SD card for animation storage (slower load time)

---

## 12. Comparison: Animatronics vs. LED Control

| Aspect | LED Control (Current) | Animatronics Control (Proposed) |
|--------|----------------------|--------------------------------|
| **Latency** | <10ms (instant) | 100-500ms (mechanical) |
| **Update Rate** | 60 FPS (smooth) | 20-50 FPS (servo PWM) |
| **State Complexity** | 6 states | 6 states + 50+ gestures |
| **Command Protocol** | ASCII (SI, SE, Mnnn) | Extended ASCII (P, G, A, C) |
| **Hardware Cost** | $30 (Arduino + LEDs) | $155 (servos + controller) |
| **Power Draw** | 2A (LEDs) | 12A (servos + LEDs) |
| **Programming Complexity** | Medium | High (kinematics, safety) |
| **Expressive Range** | Color/brightness | Physical movement |
| **CantinaOS Integration** | Existing | New service needed |

---

## 13. Alternative Approach: Hybrid System

Instead of full animatronics, consider **augmented LED system** for lower complexity:

**Option: Motorized Eye Mechanism**
- 2 servos: Pan (left/right), Tilt (up/down)
- LED rings mounted on servo platform
- Eyes "look at" person during conversation
- Much simpler than full-body animatronics
- Cost: +$40 (2 servos, pan-tilt bracket)
- Already supported by VisionService (face detection provides gaze target)

**Implementation:**
```python
# In AnimatronicsControllerService
async def _handle_person_detected(self, payload):
    """Track person with eyes."""
    face_x = payload.get("face_x")  # 0.0 (left) to 1.0 (right)
    face_y = payload.get("face_y")  # 0.0 (top) to 1.0 (bottom)

    # Map face position to servo angles
    pan_angle = 90 + (face_x - 0.5) * 60  # ±30° from center
    tilt_angle = 90 + (face_y - 0.5) * 40  # ±20° from center

    await self._adapter.set_group_servos({
        0: pan_angle,   # Eye pan servo
        1: tilt_angle   # Eye tilt servo
    }, duration_ms=300)
```

**Benefits:**
- Minimal mechanical complexity
- High impact (eyes following people is creepy/cool)
- Reuses existing VisionService face detection
- Can be Phase 1 instead of single servo test

---

## 14. Conclusion & Recommendation

### Feasibility: HIGH ✅

DJ R3X's architecture is **well-suited for animatronics extension**:
- Event-driven system naturally coordinates motion + sound + lights
- Arduino serial protocol easily extended
- State machine pattern maps to physical gestures
- TimelineExecutorService already designed for multi-layer coordination

### Recommended Path: **Incremental Hybrid Approach**

1. **Start with Motorized Eyes** (Phase 1)
   - 2 servos, pan-tilt bracket, face tracking
   - Low complexity, high impact
   - Tests full protocol + Python service integration

2. **Add Head Gestures** (Phase 2)
   - 2-3 additional servos (head pitch/yaw)
   - Nod yes/no based on sentiment
   - Pre-programmed animations via JSON

3. **Expand to Arms** (Phase 3, optional)
   - 4-6 servos for shoulder/elbow/wrist
   - Pointing gestures during DJ transitions
   - Requires mechanical design (3D printing)

4. **Puppeteering Mode** (Phase 4, optional)
   - Gamepad control for content creation
   - Record library of custom gestures
   - Advanced users only

### Key Success Factors

1. **Maintain Event Bus Architecture:** Don't let animatronics break the clean service pattern
2. **Start Small:** Single servo proof-of-concept before buying 12 servos
3. **Mechanical Design Critical:** Budget time for physical build, not just code
4. **Power Supply Separate:** Servos on dedicated supply, LEDs/Arduino separate
5. **Safety First:** Software limits, emergency stop, mechanical hard stops

### Estimated Timeline

- **Phase 1 (Motorized Eyes):** 2-3 weeks (hardware + code + testing)
- **Phase 2 (Head Gestures):** 2-3 weeks (animation system + sentiment mapping)
- **Phase 3 (Arm Gestures):** 4-6 weeks (mechanical design + multi-servo coordination)

**Total:** 8-12 weeks for fully expressive animatronic DJ R3X with arms + head + eye tracking

---

## 15. Next Steps

To proceed with animatronics integration:

1. **Decision Point:** Approve Phase 1 scope (motorized eyes vs. single servo test)
2. **Hardware Order:** Purchase PCA9685, servos, power supply
3. **Arduino Extension:** Modify `rex_face_v3_clean.ino` with servo commands
4. **Python Service:** Create `AnimatronicsControllerService` skeleton
5. **Integration Test:** Verify servo moves on voice events without LED impact

**Ready to proceed?** Let me know which phase you'd like to start with and I can generate:
- Complete Arduino code with servo support
- Python service implementation
- CLI command additions
- Hardware wiring diagrams
- Test procedures

---

## References

- Adafruit PCA9685 Datasheet: https://cdn-shop.adafruit.com/datasheets/PCA9685.pdf
- Adafruit Animatronics Guide: https://learn.adafruit.com/animating-animatronics
- Arduino Servo Library: https://www.arduino.cc/en/Reference/Servo
- Bottango Software: https://www.bottango.com
- PWM Servo Control Examples: https://github.com/sonyhome/PWM_Servomotor_Animatronics_with_Adafruit_PCA9685
- Blender Animation Export: https://github.com/timhendriks93/blender-servo-animation
- LewanSoul LX-16A Serial Bus Servos: https://www.hiwonder.com/products/lx-16a

---

**Document Version:** 1.0
**Last Updated:** November 21, 2025
**Author:** Research synthesis based on industry practices and DJ R3X architecture analysis
