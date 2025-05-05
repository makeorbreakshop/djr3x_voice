Product Requirements Document (PRD)

Project: DJ Rex Arduino Eyes Integration
Owner: Brandon Cullum
Draft Date: March 19, 2024

1. Purpose & Vision

Integrate Arduino-controlled LED matrix eyes with the DJ Rex voice system to create synchronized visual feedback during voice interactions. This enhancement will add non-verbal expression to complement the voice interactions, making the experience more engaging and lifelike.

2. Problem Statement

Current voice-only interaction lacks:
- Visual feedback during different interaction states
- Physical embodiment of DJ Rex's personality
- Real-time synchronization between speech and expression

3. Goals & Success Metrics

Goal | Metric
-----|--------
Seamless integration with voice system | No noticeable delay between voice and eye states (<100ms)
Reliable serial communication | Zero connection drops during normal operation
Expressive eye animations | At least 5 distinct eye expressions for different states
Power efficiency | LED matrices maintain consistent brightness on USB power

4. Scope

4.1 In-Scope (MVP)
- Serial communication between Next.js and Arduino
- Basic eye expressions (normal, talking, listening, thinking)
- Brightness control and basic animations
- Error handling for connection issues
- Integration with existing voice interaction flow

4.2 Out-of-Scope (Future Phases)
- Complex emotional expressions
- Machine learning-based eye movements
- External power management
- Wireless communication
- Multiple animation patterns per state

5. User Stories

Persona | User Story
--------|------------
Voice User | "As a user, I want to see DJ Rex's eyes respond when I'm talking so I know it's listening to me"
Developer | "As a developer, I want easy-to-use commands to control eye expressions during different interaction states"
Maintainer | "As a maintainer, I want to be able to debug connection issues and eye states easily"

6. Functional Requirements

ID | Requirement | Acceptance Criteria
---|-------------|-------------------
F-1 | Serial Connection | Establish connection at 9600 baud rate with error handling
F-2 | Eye State Management | Switch between states in <100ms
F-3 | Animation Control | Smooth transitions between expressions
F-4 | Voice Integration | Eyes respond to all voice system events
F-5 | Error Recovery | Auto-reconnect on connection loss
F-6 | State Feedback | Log current eye state to application

7. Technical Architecture

Hardware:
- Arduino Mega 2560 R3
- 2x 8x8 LED Matrix displays
- USB connection to host computer

Software Stack:
- Arduino IDE (C++)
- Node.js SerialPort library
- Next.js integration layer
- State management system

Communication Flow:
```
[Next.js App] → SerialPort → [Arduino] → LED Matrices
     ↑                           ↓
State Management            Animation Control
```

8. Eye States & Animations

State | Description | Animation
------|-------------|----------
Normal | Default state | Gentle brightness pulsing
Listening | Active listening | Wider, brighter eyes
Talking | Speech output | Synchronized brightness changes
Thinking | Processing | Scanning pattern
Error | Connection issues | Warning pattern

9. Dependencies & Risks

Risk | Mitigation
-----|------------
USB disconnection | Auto-reconnect + graceful fallback
Serial port conflicts | Proper port management and error handling
Power fluctuations | Brightness limiting and power monitoring
Animation lag | Optimized animation code and state transitions

10. Implementation Phases

Phase 1: Basic Integration
- Set up serial communication
- Implement basic eye states
- Add error handling

Phase 2: Animation Enhancement
- Add smooth transitions
- Implement all core animations
- Voice system integration

Phase 3: Optimization
- Performance tuning
- Power optimization
- Additional expressions

11. Success Criteria

- Stable connection maintained during extended operation
- Eye expressions change within 100ms of voice system events
- No visible flickering or animation glitches
- Graceful handling of connection issues
- Positive user feedback on expressiveness 