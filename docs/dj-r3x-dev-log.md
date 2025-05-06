# DJ R3X Voice App — Dev Log (Engineering Journal)

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project aims to recreate the voice and animation features of DJ R3X, allowing for interactive conversations with realistic voice synthesis and synchronized LED animations.

The application is built with an event-driven architecture with the following core components:
- **Event Bus (EB)** - Central communication hub for all components
- **Voice Manager (VM)** - Handles speech recognition, AI processing, and voice synthesis
- **LED Manager (LM)** - Controls LED animations on eyes and mouth
- **Music Manager (MM)** - Manages background music and audio ducking

## 🏗 Architecture Evolution Log
| Date | Change | Reason |
|------|--------|--------|
| 2023-Initial | Established event-driven architecture | Enable loose coupling between components for independent development |
| 2023-Initial | Selected pyee.AsyncIOEventEmitter for Event Bus | Provides async event handling within Python's asyncio framework |
| 2023-Initial | Added voice processing pipeline (Whisper → GPT → ElevenLabs) | Provide high-quality speech recognition and synthesis for the character |
| 2023-Initial | Implemented LED control via Arduino Mega | Allows for synchronized mouth/eye animations |
| 2025-05-06 | Identified asyncio/thread bridge issue | Synchronous input (keyboard) needs proper bridge to async event bus |

## 🔎 Code Investigations & Discoveries
- Audio RMS envelope calculation effective for LED mouth synchronization (~50fps)
- Mouth animation latency must stay under 100ms for natural appearance
- Asyncio best practices critical for stability:
  - Tasks require explicit cancellation; boolean flags alone are insufficient
  - Blocking I/O (serial, audio) must use run_in_executor
  - Thread-based inputs (keyboard) need proper bridges via run_coroutine_threadsafe
  - Cross-thread communication requires explicit lifecycle management
- Arduino communication needs robust error handling, retries, and state reconciliation
- Components require explicit cleanup for mode transitions, not just application shutdown

## 🐞 Known Bugs / Technical Limitations
- Voice detection latency varies with microphone quality and ambient noise
- Arduino serial communication introduces slight delay in LED animations
- Audio ducking creates occasionally noticeable transitions in background music
- Audio playback via sounddevice fails silently on some platforms
- Cross-thread communication requires careful event loop reference management

## 💡 Feature Backlog & Design Notes
- Move LED Manager to ESP32 for wireless control
- Add servo control for physical movements
- Implement wake word detection for hands-free operation
- Add beat detection for music-synchronized animations
- Create web dashboard for configuration and monitoring
- Consider migrating from in-process Event Bus to MQTT for distributed architecture

## 🔗 References & Resources
- [Lights & Voice MVP](./lights-voice-mvp.md) - Technical implementation details and event flow
- [Requirements](./requirements.txt) - Python dependencies

## 🕒 Log Entries (Chronological)

### 2023-MM-DD: Initial Architecture Design
- Established event-driven architecture with Event Bus as central communication hub
- Defined core components: Voice Manager, LED Manager, Music Manager
- Created standard event types and payloads for cross-component communication
- Planned file structure for organized code development

### 2023-MM-DD: Voice Processing Pipeline Implementation
- Integrated Whisper for speech recognition
- Connected OpenAI GPT for conversational AI
- Implemented ElevenLabs for character voice synthesis
- Added audio level analysis for mouth animation synchronization

### 2023-MM-DD: LED Animation System
- Established serial communication protocol with Arduino
- Created animation patterns for idle, listening, processing, and speaking states
- Implemented mouth movement synchronized with speech audio levels
- Added error recovery for connection issues

### 2023-MM-DD: Music Manager Development
- Implemented background music playback using python-vlc
- Added volume ducking during speech with smooth transitions
- Created event listeners for voice.speaking_started and voice.speaking_finished 

### 2025-05-06: Event Bus Architecture Investigation
- Investigated error: "RuntimeError: no running event loop" when using push-to-talk mode
- Confirmed event bus architecture is the right approach for coordinating multiple hardware components
- Discovered key issue: synchronous keyboard listener (pynput) cannot directly interact with asyncio event loop
- Need to implement proper "bridge" between synchronous threads and asyncio using thread-safe methods
- Architecture insight: The event bus design is sound for our multi-component system, but requires careful handling of cross-thread communication
- Approach: Use asyncio.run_coroutine_threadsafe() or loop.call_soon_threadsafe() to properly schedule events from synchronous contexts

### 2025-05-06: Event Bus Async Handler Fix
- Enhanced EventBus to properly handle mixed sync/async event handlers with task gathering
- Fixed LED animation timing issues by properly awaiting async event handlers 

### 2025-05-06: LED Configuration Update
- Centralized LED settings in `app_settings.py` with macOS-compatible defaults 

### 2025-05-07: Push-to-Talk Event Loop Reference Fix
- Fixed critical error: "Task got Future <_GatheringFuture pending> attached to a different loop"
- Root cause: Event loop reference mismatch between initialization and execution phases
- Problem detail: VoiceManager captures event loop with `asyncio.get_event_loop()` during init, but `asyncio.run()` in main.py creates a new loop
- When keyboard listener thread uses `run_coroutine_threadsafe()`, it references wrong loop
- Solution: Pass the running event loop explicitly from main.py to VoiceManager, ensuring consistent loop references
- Learning: When using asyncio with threaded callbacks, always capture the running loop explicitly with `asyncio.get_running_loop()` and pass it to components requiring cross-thread communication 

### 2025-05-07: Voice Interaction Pipeline Investigation
- Fixed OpenAI integration by storing model name directly in VoiceManager instance instead of config dictionary
- Fixed ElevenLabs integration by properly using VoiceConfig.to_dict() method for API calls
- Identified and resolved audio playback issues on macOS:
  - Verified ElevenLabs audio generation works (90KB+ files generated)
  - Added fallback from sounddevice to system audio commands (afplay/aplay)
  - Enhanced logging throughout voice pipeline for better debugging
- Added platform-specific audio playback support for macOS, Linux, and Windows 

### 2025-05-07: Added Startup Sound
- Added platform-compatible startup sound playback after component initialization for better UX feedback 

### 2025-05-07: LED Communication Protocol Update
- Implemented ArduinoJson v7.4.1 for robust JSON parsing
- Updated Arduino sketch with dynamic JSON allocation and proper error handling
- Added structured acknowledgments for reliable communication
- Next: Test communication reliability with voice state changes 

### 2025-05-07: LED JSON Communication Fix
- Fixed JSON communication between Python and Arduino
- Updated LED Manager to handle multiple response types (debug, parsed, ack)
- Added timeout protection and better error handling
- Reduced Arduino debug output with DEBUG_MODE flag
- Result: Eliminated "Invalid JSON" and "Unexpected acknowledgment" warnings 

### 2025-05-08: System Modes Architecture Design
- Implemented system mode architecture to fix debugging issues and improve interaction:
  - **Modes**: STARTUP → IDLE → AMBIENT SHOW/INTERACTIVE VOICE
- Key benefits: Explicit opt-in to voice interaction, state-based behavior control
- Implementation: Command input thread with asyncio bridge, EventBus for mode transitions
- Components respond to mode changes via event subscriptions

### 2025-05-08: System Modes Architecture Refinement
- Added distinct IDLE mode as default fallback state
- System boot sequence: STARTUP → IDLE (can transition to AMBIENT or INTERACTIVE)
- Commands: `ambient`, `engage`, `disengage` (returns to IDLE)
- Improved LED patterns for each mode and fixed command input display

### 2025-05-08: Voice Interaction and LED State Management Updates
- Fixed VoiceManager interaction loop for speech synthesis/playback
- Known Issue: LED transitions during pattern interruption need improvement 

### 2025-05-09: Music Playback System Design
- Implemented CLI music controls: `list music`, `play music <number/name>`, `stop music`
- Mode-specific behaviors:
  - IDLE: Limited controls; playing transitions to AMBIENT
  - AMBIENT: Full controls, continuous playback
  - INTERACTIVE: Full controls with audio ducking during speech
- Architecture: MusicManager listens for control commands and mode changes
- Next steps: CLI implementation, testing, voice command integration

### 2025-05-09: Asyncio State Transition Fix
- Fixed: Voice/LED persisting after mode changes; Arduino timeouts
- Solutions: Task cancellation, non-blocking I/O, resource cleanup
- Result: Clean transitions between system modes 

### 2025-05-09: Added System Reset Command
- Added `reset` CLI command for emergency system recovery
- Implementation:
  - Cancels all active tasks (voice, LED, music)
  - Cleans up hardware connections (Arduino, audio)
  - Forces transition to IDLE mode
  - Re-initializes core managers if needed
- Benefit: Quick recovery from stuck states without full restart 