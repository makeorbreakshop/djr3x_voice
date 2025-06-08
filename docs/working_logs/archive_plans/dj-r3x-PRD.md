# DJ-R3X Voice & Lights System
## Product Requirements Document

### 1. Overview

DJ-R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project aims to recreate the voice and animation features of DJ-R3X, allowing for interactive conversations with realistic voice synthesis and synchronized LED animations.

### 2. Objectives

- Create a voice assistant that responds in DJ-R3X's style and voice
- Implement synchronized LED animations for eye/mouth expressions
- Coordinate audio playback with visual animations
- Establish a foundation for future servo control and expanded functionality

### 3. Core Architecture

The system will follow an event-driven architecture with the following core components:

1. **Event Bus (EB)** - Central communication hub for all components
2. **Voice Manager (VM)** - Handles speech recognition, AI processing, and voice synthesis
3. **LED Manager (LM)** - Controls LED animations on eyes and mouth
4. **Music Manager (MM)** - Manages background music and audio ducking

All components will communicate through the Event Bus, enabling loose coupling and independent development.

### 4. Detailed Component Specifications

#### 4.1 Event Bus

The Event Bus will use pyee.AsyncIOEventEmitter and provide methods for:
- Publishing events with type-safe payloads
- Subscribing to events with callback functions
- Logging all events for debugging
- Measuring timing between related events

**Standard Events:**
- `voice.started {duration: float, emotion: string}` - Indicates speech output has started
- `voice.beat {level: 0-255}` - Audio level for animation synchronization (~50 fps)
- `voice.finished {}` - Indicates speech output has completed
- `music.trackStarted {title: string, bpm: number}` - New track information
- `system.error {source: string, message: string}` - Error reporting

#### 4.2 Voice Manager

Responsibilities:
- Speech recognition using Whisper
- AI response generation using GPT-4o
- Voice synthesis using ElevenLabs
- Publishing voice events for other components

Key Features:
- Push-to-talk activation
- Audio level analysis for synchronization
- Emotion detection for expression matching

#### 4.3 LED Manager

Responsibilities:
- Serial communication with Arduino
- Animation selection based on voice state
- Synchronizing LED intensity with audio levels

Key Features:
- Multiple eye expression patterns
- Mouth movement synchronized with speech
- Idle animations when not speaking
- Error recovery for connection issues

#### 4.4 Music Manager

Responsibilities:
- Background music playback
- Volume control for speech ducking
- Track information management

Key Features:
- Automatic volume reduction during speech
- Smooth volume transitions
- Track selection based on context

### 5. Hardware Requirements

#### Arduino Mega
- Controls LED matrices for eyes
- Receives commands via USB serial
- Handles animation patterns locally
- Pin configuration:
  - D51: DIN for LED matrices
  - D52: CLK for LED matrices
  - D53: CS for LED matrices
  - 5V: Power for LEDs (2A+)
  - GND: Common ground

#### Computer
- Runs Python main process
- Handles voice processing
- Manages the event bus
- Connects to Arduino via USB

### 6. Implementation Plan

#### Phase 1: Event Bus and Architecture (Days 1-3)
1. Create `bus.py` with EventBus class wrapping pyee.AsyncIOEventEmitter
2. Create simple `main.py` that initializes the event bus and starts all components
3. Implement a basic test script that publishes mock events and verifies subscribers receive them
4. Add event logging and metrics (event counts, timing)

**Deliverable:** Working event bus with demo publishers and subscribers

#### Phase 2: Manager Implementation (Days 4-10)
5. Create `led_manager.py` that subscribes to voice events and controls Arduino
6. Create `voice_manager.py` that handles speech processing and publishes events
7. Create `music_manager.py` for background music and volume control
8. Update Arduino code to match new command protocol if needed

**Deliverable:** Individual managers working with mock events

#### Phase 3: Testing and Integration (Days 11-15)
9. Implement mock publisher for testing components in isolation
10. Create integration tests to verify event flow end-to-end
11. Measure and optimize latency between events
12. Add error handling and recovery for all components

**Deliverable:** End-to-end working system with basic functionality

#### Phase 4: Polish and Optimization (Days 16-20)
13. Add configuration file for event bus and component settings
14. Optimize animation timing and synchronization
15. Add CLI monitoring tools for real-time event viewing
16. Document the architecture and component interactions

**Deliverable:** Fully functional system with documentation and tools

### 7. Technical Implementation Details

#### 7.1 File Structure
```
src/
 ├─ main.py              # starts asyncio tasks + EB
 ├─ bus.py               # wrapper around pyee
 ├─ voice_manager.py     # Whisper/GPT/11‑Labs
 ├─ led_manager.py       # Arduino communication
 ├─ music_manager.py     # VLC / Spotify
 ├─ config/              # Configuration files
 └─ utils/
      ├─ envelope.py     # audio RMS → 0‑255
      └─ logging.py      # Event logging utilities
```

#### 7.2 Event Flow
```
1. User activates push-to-talk
2. Voice Manager captures and processes speech
3. Voice Manager publishes voice.started event
4. LED Manager starts mouth animation
5. Music Manager reduces music volume
6. Voice Manager streams voice.beat events during speech
7. LED Manager adjusts animation based on beat
8. Voice Manager publishes voice.finished event
9. LED Manager returns to idle animation
10. Music Manager restores volume
```

#### 7.3 Error Handling
- Components should handle their own errors internally
- Critical errors should be published via system.error
- Components should implement reconnection logic
- The main process should monitor component health

### 8. Acceptance Criteria

The system is complete when:

1. User can activate voice input via push-to-talk
2. System responds with DJ-R3X style voice
3. LEDs animate in sync with speech (<100ms lag)
4. Background music ducks during speech
5. System recovers gracefully from errors
6. All components communicate solely through events

### 9. Future Enhancements

After MVP completion, the following enhancements are planned:

1. Move LED Manager to ESP32 for wireless control
2. Add servo control for physical movements
3. Implement wake word detection for hands-free operation
4. Add beat detection for music-synchronized animations
5. Create web dashboard for configuration and monitoring

### 10. Step-by-Step Development Guide

#### Step 1: Set up the Event Bus
```python
# bus.py
from pyee import AsyncIOEventEmitter
import logging
from typing import Any, Callable, Dict, Optional
import asyncio

class EventBus:
    def __init__(self):
        self._emitter = AsyncIOEventEmitter()
        self._logger = logging.getLogger("event_bus")
        
    def subscribe(self, event: str, callback: Callable) -> None:
        self._emitter.on(event, callback)
        self._logger.debug(f"Subscribed to event: {event}")
        
    def publish(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        self._logger.debug(f"Publishing event: {event} with data: {data}")
        asyncio.create_task(self._publish_async(event, data))
        
    async def _publish_async(self, event: str, data: Optional[Dict[str, Any]]) -> None:
        self._emitter.emit(event, data or {})
```

#### Step 2: Create Main Application
```python
# main.py
import asyncio
import logging
from bus import EventBus
from led_manager import LedManager
from voice_manager import VoiceManager
from music_manager import MusicManager

async def main():
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    # Create event bus
    event_bus = EventBus()
    
    # Create managers
    led_manager = LedManager(event_bus)
    voice_manager = VoiceManager(event_bus)
    music_manager = MusicManager(event_bus)
    
    # Start all managers
    await asyncio.gather(
        led_manager.start(),
        voice_manager.start(),
        music_manager.start()
    )

if __name__ == "__main__":
    asyncio.run(main())
```

#### Step 3: Create Mock Event Publisher for Testing
```python
# test_events.py
import asyncio
import time
from bus import EventBus

async def main():
    event_bus = EventBus()
    
    # Simulate voice.started
    print("Publishing voice.started")
    event_bus.publish("voice.started", {"duration": 3.5, "emotion": "excited"})
    
    # Simulate voice.beat events
    for i in range(50):
        # Simulate sine wave pattern for levels
        level = int(127 + 127 * math.sin(i * 0.2))
        event_bus.publish("voice.beat", {"level": level})
        await asyncio.sleep(0.02)  # 50fps
    
    # Simulate voice.finished
    print("Publishing voice.finished")
    event_bus.publish("voice.finished", {})
    
    # Keep running to see effects
    await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
```

Follow these steps to implement each component, focusing on the event interfaces first, then adding the actual functionality incrementally.

