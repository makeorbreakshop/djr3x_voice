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
- Audio level extraction using RMS envelope calculation works well for synchronizing LED mouth animations with speech (~50fps update rate)
- Latency between voice.beat events and LED animation updates must be kept under 100ms for natural appearance
- Asynchronous programming model (asyncio) enables smooth coordination between components while maintaining responsiveness
- Discovered synchronous libraries (pynput for keyboard input) require proper threadsafe bridges to communicate with asyncio event loop (use asyncio.run_coroutine_threadsafe or loop.call_soon_threadsafe)

## 🐞 Known Bugs / Technical Limitations
- Voice detection latency varies depending on microphone quality and ambient noise
- Serial communication with Arduino introduces slight delay in LED animations
- Audio ducking sometimes creates noticeable transitions in background music
- Push-to-talk keyboard listener runs in a separate thread and can't directly create asyncio tasks without a proper thread-safe bridge
- Audio playback through sounddevice appears to fail silently, requiring investigation into platform-specific audio driver configuration

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