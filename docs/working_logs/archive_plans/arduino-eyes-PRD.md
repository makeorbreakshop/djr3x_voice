Product Requirements Document (PRD)

Project: DJ Rex Arduino Eyes Integration
Owner: Brandon Cullum
Draft Date: March 19, 2024
Last Updated: March 20, 2024 - Implementation Progress Update

1. Purpose & Vision

Integrate Arduino-controlled LED matrix eyes with the DJ Rex voice system to create synchronized visual feedback during voice interactions. This enhancement will add non-verbal expression to complement the voice interactions, making the experience more engaging and lifelike.

2. Problem Statement

Current voice-only interaction lacks:
- Visual feedback during different interaction states
- Physical embodiment of DJ Rex's personality
- Real-time synchronization between speech and expression

3. Goals & Success Metrics

Goal | Metric | Status
-----|--------|--------
Seamless integration with voice system | No noticeable delay between voice and eye states (<100ms) | ✅ Achieved (<100ms response time in testing)
Reliable serial communication | Zero connection drops during normal operation | ✅ Initial implementation complete
Expressive eye animations | At least 5 distinct eye expressions for different states | ✅ Implemented 5 states (IDLE, LISTENING, PROCESSING, SPEAKING, ERROR)
Power efficiency | LED matrices maintain consistent brightness on USB power | ✅ Implemented with configurable brightness levels

4. Scope

4.1 In-Scope (MVP)
- ✅ Serial communication between Python and Arduino (115200 baud)
- ✅ Basic eye expressions (normal, talking, listening, thinking)
- ✅ Brightness control and basic animations
- ✅ Error handling for connection issues
- 🔄 Integration with existing voice interaction flow (In Progress)

4.2 Out-of-Scope (Future Phases)
- Complex emotional expressions
- Machine learning-based eye movements
- External power management
- Wireless communication
- Multiple animation patterns per state

5. User Stories

Persona | User Story | Status
--------|------------|--------
Voice User | "As a user, I want to see DJ Rex's eyes respond when I'm talking so I know it's listening to me" | ✅ Implemented
Developer | "As a developer, I want easy-to-use commands to control eye expressions during different interaction states" | ✅ Implemented via Python EyeState enum
Maintainer | "As a maintainer, I want to be able to debug connection issues and eye states easily" | ✅ Implemented with serial debugging

6. Current Implementation Details

A. Hardware Configuration
- Arduino Mega 2560 R3
- 2x 8x8 LED Matrix displays
- Pin Configuration:
  - DIN = 51
  - CLK = 52
  - CS = 53
- Visible Area: 3x3 matrix per eye (center LEDs only)

B. State Management System
Implemented States:
- IDLE: Full 3x3 grid illuminated
- LISTENING: Full 3x3 grid illuminated
- PROCESSING: Rotating animation in center column
- SPEAKING: Multiple animation patterns:
  - Pattern 0: Vertical bouncing animation (default)
  - Pattern 1: Pulsing pattern (full grid ↔ center column)
  - Pattern 2: Expanding square animation
  - Pattern 3: Wave pattern animation
- ERROR: Blinking X pattern
- TEST: All LEDs ON (for hardware testing)

C. Communication Protocol
- Serial Connection: 115200 baud
- Command Format: 
  - Basic states: "IDLE", "LISTENING", "PROCESSING", "ERROR", "TEST"
  - Speaking variations: "SPEAKING:1", "SPEAKING:2", "SPEAKING:3"
- Acknowledgment: Each command echoed with confirmation
- Error Handling: Implemented for connection issues

D. Testing Tools
- test_eyes_serial.py:
  - Comprehensive state testing
  - Serial connection management
  - Error handling
  - State transition verification

7. Technical Architecture

Current Implementation:
```python
# Python Side (test_eyes_serial.py)
class EyeState(Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"
```

```cpp
// Arduino Side (eyes.ino)
- LedControl library for LED matrix management
- State-based animation system
- 50ms update interval for animations
- Configurable brightness levels
```

8. Eye States & Animations (Current Implementation)

State | Description | Animation | Status
------|-------------|-----------|--------
IDLE | Default state | Full 3x3 grid illuminated | ✅ Implemented
LISTENING | Active listening | Full 3x3 grid | ✅ Implemented
PROCESSING | Processing | Rotating column animation | ✅ Implemented
SPEAKING | Speech output | Multiple patterns (4 variations) | ✅ Enhanced
ERROR | Connection issues | Blinking X pattern | ✅ Implemented
TEST | Hardware test | All LEDs ON | ✅ Implemented

9. Next Steps

Priority | Task | Status
---------|------|--------
High | Voice system integration | 🔄 In Progress
Medium | Animation refinement | 🔄 Planned
Medium | Performance optimization | 🔄 Planned
Low | Additional eye expressions | 📅 Planned

10. Technical Notes

Current Performance Metrics:
- Serial Communication: Stable at 115200 baud
- Animation Frame Rate: 20fps (50ms delay)
- State Transition Time: <100ms
- Error Recovery: Implemented with graceful fallback

11. Known Limitations

1. Physical Constraints:
   - Limited to 3x3 visible area per eye
   - Center LED crucial for main expression
   - Brightness levels need to be optimized for visible area

2. Technical Constraints:
   - Single serial connection at a time
   - Animation complexity limited by update rate
   - State transitions must be sequential

12. Functional Requirements

ID | Requirement | Acceptance Criteria
---|-------------|-------------------
F-1 | Serial Connection | Establish connection at 9600 baud rate with error handling
F-2 | Eye State Management | Switch between states in <100ms
F-3 | Animation Control | Smooth transitions between expressions
F-4 | Voice Integration | Eyes respond to all voice system events
F-5 | Error Recovery | Auto-reconnect on connection loss
F-6 | State Feedback | Log current eye state to application

13. Technical Architecture

Phase 1 (Current) - Python Core:
Hardware:
- Arduino Mega 2560 R3
- 2x 8x8 LED Matrix displays
  - Note: Only center 9 LEDs (3x3) visible per eye
  - Effective display area: 3x3 matrix per eye
  - Total visible LEDs: 18 (9 per eye)
- USB connection to host computer

Software Components:
1. Core Components:
   - Arduino IDE (C++)
   - Python 3.x
     - pyserial-asyncio for non-blocking serial communication
     - State management system integrated with voice processing
     - Asyncio for non-blocking operations
   - Existing voice processing system

2. New Modules:
   - eyes_manager.py: Main eye state and animation controller
   - config/eyes_settings.py: Eye-specific configuration
   - config/eye_effects.json: Animation and transition configurations
   - test_eye_animations.py: Testing suite for eye animations

Communication Flow:
```
[Python App] → pyserial-asyncio → [Arduino] → LED Matrices
     ↑                               ↓
State Management                Animation Control
     ↑
Voice System Integration
```

3. Integration Points:
   - Event-based state synchronization with voice system
   - Shared configuration management
   - Common debugging and monitoring tools
   - Push-to-talk mode integration

Future Phase - Web Integration:
- Next.js frontend (planned)
- WebSocket communication between Python backend and Next.js
- Browser-based control interface

14. Eye States & Animations

State | Description | Animation | LED Usage
------|-------------|-----------|----------
Normal | Default state | Gentle brightness pulsing | Center 3x3 fade pattern
Listening | Active listening | Wider, brighter eyes | Full 3x3 brightness
Talking | Speech output | Synchronized brightness changes | Dynamic center LED with surrounding pattern
Thinking | Processing | Scanning pattern | Rotating pattern within 3x3 grid
Error | Connection issues | Warning pattern | Alternating diagonal patterns

Animation Constraints:
- Each eye limited to 9 visible LEDs (3x3 matrix)
- Center LED crucial for main expression
- Outer 8 LEDs for expression enhancement
- Brightness levels must be optimized for visible area

15. Dependencies & Risks

Risk | Mitigation
-----|------------
USB disconnection | Auto-reconnect + graceful fallback
Serial port conflicts | Proper port management and error handling
Power fluctuations | Brightness limiting and power monitoring
Animation lag | Optimized animation code and state transitions

16. Implementation Phases

Phase 1: Python Core Integration
- Implement pyserial communication layer
- Create Python class for eye state management
- Add error handling and reconnection logic
- Integrate with existing voice system
- Add logging and debugging capabilities

Phase 2: Animation & Stability
- Implement all core animations on Arduino
- Add smooth transitions
- Create robust error recovery
- Optimize serial communication

Phase 3: Next.js Integration (Future)
- Design WebSocket communication layer
- Create Next.js frontend interface
- Add web-based controls and monitoring
- Implement real-time status display

17. Success Criteria

- Stable connection maintained during extended operation
- Eye expressions change within 100ms of voice system events
- No visible flickering or animation glitches
- Graceful handling of connection issues
- Positive user feedback on expressiveness

18. Technical Notes

Python Implementation:
- Uses asyncio for non-blocking serial communication
- Implements observer pattern for state changes
- Maintains singleton connection to Arduino
- Uses structured logging for debugging
- Includes comprehensive error handling

Future Web Integration:
- WebSocket server in Python backend
- Next.js frontend for monitoring and control
- Real-time state synchronization
- Browser-based debugging tools

19. Technical Implementation Details

A. State Management
- Primary States:
  - Idle (default)
  - Listening (active input)
  - Processing (thinking)
  - Speaking (output)
  - Error (connection/system issues)
  - Push-to-Talk States (ready, recording)

B. Configuration System
- eyes_settings.py:
  ```python
  - DEBUG_EYE_ANIMATIONS
  - ANIMATION_FRAMERATE
  - DEFAULT_BRIGHTNESS
  - FALLBACK_MODE_ENABLED
  - VISIBLE_MATRIX_SIZE = 3  # 3x3 visible area
  - CENTER_LED_BRIGHTNESS_BOOST = 1.2  # Emphasis on center LED
  ```
- eye_effects.json:
  ```json
  {
    "animations": {
      "idle": { 
        "pattern": "gentle_pulse", 
        "speed": 1.0,
        "visible_area": "3x3",
        "center_led_emphasis": true
      },
      "listening": { 
        "pattern": "wide_alert", 
        "brightness": 0.8,
        "visible_area": "3x3",
        "center_led_emphasis": true
      },
      "processing": { 
        "pattern": "scanning", 
        "speed": 1.2,
        "visible_area": "3x3",
        "use_outer_leds": true
      }
    }
  }
  ```

C. Error Handling & Recovery
1. Serial Communication:
   - Auto-reconnection logic
   - Fallback animation mode on Arduino
   - State recovery after reconnection
2. Animation Failures:
   - Default animation patterns
   - Graceful degradation options
   - Error logging and monitoring

D. Debug & Testing Features
1. Debug Utilities:
   - Timing decorators for animation transitions
   - State change logging
   - Serial communication monitoring
2. Testing Framework:
   - Animation state simulation
   - Timing verification
   - Integration tests with voice system
   - Stress testing for state transitions

E. Voice System Integration
1. Event System:
   ```python
   class EyeStateManager:
       async def on_voice_state_change(self, state: VoiceState)
       async def on_push_to_talk_toggle(self, active: bool)
       async def on_error(self, error: Exception)
   ```
2. State Synchronization:
   - Voice state → Eye state mapping
   - Transition timing coordination
   - Error state propagation

F. Performance Considerations
1. Animation Performance:
   - Target 30fps minimum
   - < 100ms state transition time
   - Efficient serial communication
2. Resource Usage:
   - Minimal CPU impact
   - Memory-efficient animation patterns
   - Background thread management

G. Future Extensibility
1. Web Integration Preparation:
   - WebSocket-ready state management
   - Serializable animation configurations
   - Remote control capabilities
2. Animation Framework:
   - Pluggable animation system
   - Custom pattern support
   - Dynamic animation loading

20. Success Criteria Updates

Technical Success Metrics:
- Serial Communication Reliability: < 1% connection drops
- State Transition Speed: < 100ms average
- Animation Performance: Stable 30fps
- CPU Usage: < 5% additional load
- Memory Usage: < 50MB for eye management
- Error Recovery: < 2s reconnection time 