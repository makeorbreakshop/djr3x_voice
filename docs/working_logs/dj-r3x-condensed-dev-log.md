# DJ R3X Voice App — Condensed Dev Log

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project recreates the voice and animation features with interactive conversations and synchronized LED animations.

**Core Components:**
- **Event Bus (EB)** - Central communication hub
- **Voice Manager (VM)** - Speech recognition, AI processing, voice synthesis
- **LED Manager (LM)** - LED animations for eyes and mouth
- **Music Manager (MM)** - Background music and audio ducking

## 🏗 Architecture Evolution
| Date | Change | Reason |
|------|--------|--------|
| 2023-Initial | Event-driven architecture | Enable loose coupling |
| 2023-Initial | Voice pipeline (Whisper → GPT → ElevenLabs) | High-quality speech processing |
| 2025-05-06 | Streaming architecture (Deepgram → GPT-4o → ElevenLabs) | Reduce latency |
| 2025-05-13 | DeepgramDirectMicService | Simplify audio pipeline |

## 🔎 Key Technical Insights
- Audio RMS for LED sync (~50fps)
- Mouth animation latency < 100ms
- Asyncio best practices: explicit task cancellation, non-blocking I/O, thread bridges
- Event-driven architecture requires consistent async/await patterns
- Hardware interfaces need careful thread-to-asyncio bridging

## 💡 Current Features
- Voice interaction with Deepgram streaming transcription
- GPT-4o conversations with memory
- ElevenLabs streaming TTS (~300ms TTFS)
- Arduino LED eye animations
- Multi-modal input (voice, text, mouse click)
- Music playback with audio ducking
- CLI command interface

## 🕒 Development Log

### 2023: Initial Development
- Established event-driven architecture with pyee.AsyncIOEventEmitter
- Implemented voice pipeline (Whisper/GPT/ElevenLabs)
- Created LED animation system with Arduino
- Added background music with volume ducking

### 2025-05-06: Core System Improvements
- **Issue**: Event loop reference issues in push-to-talk mode
- **Solution**: Enhanced EventBus for mixed sync/async handlers, centralized LED settings
- **Impact**: Fixed event routing and state transitions
- **Technical**: Added platform-specific audio playback support

### 2025-05-06: LED System Updates
- **Issue**: Complex JSON communication causing timeouts
- **Solution**: Upgraded to ArduinoJson v7.4.1, enhanced protocol with timeout protection
- **Impact**: Eliminated "Invalid JSON" warnings, more reliable communication

### 2025-05-08: System Architecture - Mode System
- **Issue**: Need for explicit state management and better debugging
- **Solution**: Added mode system (STARTUP → IDLE → AMBIENT/INTERACTIVE)
- **Impact**: Clear opt-in to voice interaction, improved state control
- **Technical**: CLI commands for mode transitions, EventBus coordination

### 2025-05-06: Voice Pipeline Upgrade
- **Issue**: High latency (3-5s) with batch processing
- **Solution**: Streaming architecture (Mic → Deepgram → GPT-4o → ElevenLabs)
- **Impact**: Reduced latency to 1-1.5s
- **Technical**: Fixed SDK compatibility issues, integrated streaming components

### 2025-05-06: Event Bus Architecture Investigation
- **Issue**: "RuntimeError: no running event loop" in push-to-talk mode
- **Solution**: Proper thread-to-asyncio bridging with run_coroutine_threadsafe
- **Impact**: Stable cross-thread communication
- **Key Learning**: Event bus design sound, but requires careful async handling

### 2025-05-06: Push-to-Talk Event Loop Fix
- **Issue**: Task got Future attached to different loop
- **Solution**: Pass running event loop explicitly from main.py to VoiceManager
- **Impact**: Fixed keyboard listener thread integration
- **Key Learning**: Always capture running loop with asyncio.get_running_loop()

### 2025-05-06: Voice Interaction Pipeline Fixes
- **Issue**: Audio playback failures on macOS, model name storage
- **Solution**: Platform-specific audio fallbacks, proper config handling
- **Impact**: Reliable voice interaction across platforms
- **Technical**: Added afplay/aplay fallbacks, enhanced logging

### 2025-05-06: LED JSON Communication Fix
- **Issue**: "Invalid JSON" and "Unexpected acknowledgment" warnings
- **Solution**: Updated Arduino sketch with ArduinoJson v7.4.1, structured acknowledgments
- **Impact**: Clean communication protocol, reduced debug output

### 2025-05-06: Voice Integration Complete
- **Issue**: StreamManager transcriptions not reaching LLM pipeline
- **Solution**: Integrated StreamManager with VoiceManager, added external transcript handling
- **Impact**: Full streaming pipeline functional
- **Technical**: Fixed event routing between components

### 2025-05-06: Streaming Voice Processing Pipeline
- **Issue**: Import errors with Deepgram SDK upgrade
- **Solution**: Updated deepgram-sdk (2.11.0 → 4.0.0), python-vlc update
- **Impact**: Enabled streaming voice processing
- **Key Learning**: Check API docs when upgrading major SDK versions

### 2025-05-06: OpenAI SDK Compatibility Fix
- **Issue**: Client.__init__() unexpected 'proxies' argument
- **Solution**: Monkey patched httpx.Client.__init__ to ignore 'proxies'
- **Impact**: Fixed OpenAI client initialization
- **Technical**: Handled SDK dependency incompatibility

### 2025-05-06: Continuous Listening Mode Enhancement
- **Issue**: Deepgram splitting speech into fragments, interrupting flow
- **Solution**: Smart transcript accumulation with 3-second silence threshold
- **Impact**: More natural speech detection, complete thoughts processed
- **Technical**: Join fragments before processing, detect true end of utterances

### 2025-05-07: CantinaOS Implementation Started
- **Issue**: Need clean architectural implementation
- **Solution**: Started CantinaOS-based architecture in src_cantinaos/
- **Impact**: Cleaner separation of concerns, better testability
- **Technical**: Phased approach with core event system first

### 2025-05-07: CantinaOS Core Implementation
- **Achievement**: Implemented core event system and base service architecture
- **Components**: event_topics.py, event_payloads.py, base_service.py
- **Impact**: Foundation for clean service implementations
- **Technical**: Pydantic models for type safety, hierarchical event topics

### 2025-05-07: Service Implementations Complete
- **Achievement**: Implemented all Phase 1 core services
- **Services**: MicInput, Deepgram, GPT, ElevenLabs, EyeLight, Music, CLI, YodaMode, ToolExecutor
- **Impact**: Complete system functionality in CantinaOS architecture
- **Technical**: Comprehensive test suites for each service

### 2025-05-08: LED Eyes System - Final Protocol
- **Issue**: Complex JSON causing Arduino memory issues
- **Solution**: Simple single-char commands (I/L/T/S for states)
- **Impact**: ~20ms latency, 6KB memory usage, reliable operation
- **Technical**: Hardware using Arduino Mega 2560, dual MAX7219 matrices

### 2025-05-08: Testing Framework Implementation
- **Achievement**: Comprehensive test framework with mock services
- **Components**: Integration tests, performance metrics, event bus utilities
- **Impact**: 90%+ critical path coverage, automated testing
- **Technical**: EventSynchronizer, ResourceMonitor, RetryDecorator helpers

### 2025-05-08: Test Stability Improvements
- **Issue**: Flaky test rate 3.2%, event timing issues
- **Solution**: EventSynchronizer, proper cleanup verification, RetryDecorator
- **Impact**: Reduced flakiness to <1%, stable test suite
- **Key Learning**: Event-based testing needs precise timing management

### 2025-05-08: CommandDispatcherService Tests
- **Achievement**: All 7/7 tests passing with improved error handling
- **Fixes**: Event bus error handling, service lifecycle patterns
- **Impact**: Reliable command processing and routing
- **Technical**: BaseService.emit_error_response for safe error reporting

### 2025-05-09: Event Bus Memory Leak Fixed
- **Issue**: "No wrapper found for handler" warnings during shutdown
- **Solution**: Topic-based handler tracking, proper cleanup verification
- **Impact**: Clean resource cleanup, eliminated memory leaks
- **Technical**: Nested dict tracking, enhanced remove_all_listeners

### 2025-05-09: Mode Transition Tests Complete
- **Achievement**: System mode transitions fully tested and working
- **Components**: STARTUP → IDLE → AMBIENT → INTERACTIVE
- **Impact**: Reliable state management and service coordination
- **Technical**: Event-driven mode management with proper state tracking

### 2025-05-09: Audio Pipeline Integration Tests
- **Achievement**: Complete audio pipeline testing with proper resource management
- **Components**: Music playback, audio ducking, mic processing
- **Impact**: Stable concurrent audio operations
- **Technical**: Grace periods for state transitions, enhanced cleanup

### 2025-05-09: CLI Service Rewrite - Pure Asyncio
- **Issue**: Threading conflicts with event-driven architecture
- **Solution**: Replaced threaded input with pure asyncio implementation
- **Impact**: Fixed prompt buffering, eliminated thread boundary issues
- **Technical**: Platform-specific async stdin handling

### 2025-05-09: System Modes Architecture Complete
- **Achievement**: Full system mode implementation with CLI integration
- **Features**: Mode commands, music controls, event-driven transitions
- **Impact**: Clean user experience with proper feedback
- **Technical**: Command shortcuts, status updates, mode-aware behaviors

### 2025-05-09: Star Tours Audio Integration
- **Achievement**: Added startup sounds and mode transition audio
- **Implementation**: ModeChangeSoundService with event-driven playbook
- **Impact**: Enhanced user feedback for system state changes
- **Technical**: Non-blocking audio playback via event bus

### 2025-05-09: Music System Complete
- **Achievement**: Full music controller with mode-aware playback
- **Features**: CLI commands (list/play/stop), audio ducking, VLC integration
- **Impact**: Background ambiance with speech integration
- **Technical**: Dynamic volume control, proper resource cleanup

### 2025-05-09: Text-Based Recording Mode
- **Issue**: Push-to-talk keyboard conflicts
- **Solution**: Added 'rec' command for text input recording simulation
- **Impact**: Reliable testing method and alternative input mode
- **Technical**: Event emission for VOICE_LISTENING_STARTED/STOPPED

### 2025-05-09: Audio Capture Chain Fixed
- **Issue**: MicInputService audio not reaching DeepgramTranscriptionService
- **Solution**: Fixed async subscription pattern with asyncio.create_task()
- **Impact**: Complete audio pipeline from capture to transcription
- **Key Learning**: All event subscriptions must be properly awaited/wrapped

### 2025-05-09: Thread-to-Asyncio Bridge Fix
- **Issue**: "There is no current event loop in thread" in audio callback
- **Solution**: Store event loop reference, use run_coroutine_threadsafe
- **Impact**: Stable hardware integration with event loop
- **Technical**: Proper bridge pattern for hardware interfaces

### 2025-05-09: Service Architecture Fixes
- **Issue**: Competing CLI_RESPONSE messages from multiple services
- **Solution**: Clear service responsibility separation
- **Impact**: Microphone info properly displayed in interactive mode
- **Key Learning**: Single responsibility per service, event-based communication

### 2025-05-12: Architectural Inconsistencies Fixed
- **Issue**: AttributeError crashes due to naming inconsistencies
- **Solution**: Standardized protected attributes (_name), proper initialization
- **Impact**: System starts and operates correctly
- **Technical**: Created ARCHITECTURE_STANDARDS.md, service template

### 2025-05-12: Event Payload Handling Fixed
- **Issue**: Pydantic model event payloads causing timestamp errors
- **Solution**: Auto-conversion in BaseService.emit, standardized payload format
- **Impact**: Reliable event system, fixed help command and CLI operations
- **Key Learning**: Always convert Pydantic models to dictionaries for events

### 2025-05-12: Music Controller Service Integration
- **Issue**: Music commands not working - service not initialized
- **Solution**: Added music_controller to service_order, fixed payload handling
- **Impact**: Full music functionality restored
- **Technical**: Enhanced track discovery, proper error handling

### 2025-05-12: DebugService Implementation
- **Achievement**: Centralized debugging and system observability
- **Features**: Component log levels, command tracing, performance metrics
- **Impact**: Significantly improved debugging capabilities
- **Technical**: Queue-based processing, real-time monitoring

### 2025-05-10: MouseInputService Implementation
- **Achievement**: Click-based recording control with single left-click
- **Integration**: Event-based with MicInputService, LED feedback
- **Impact**: Intuitive recording interface
- **Technical**: Cross-platform pynput, proper thread-to-asyncio bridging

### 2025-05-13: DeepgramDirectMicService Implementation
- **Issue**: Complex audio pipeline with multiple thread boundaries
- **Solution**: Single service using Deepgram SDK's native Microphone class
- **Impact**: Simplified architecture, reduced latency, improved reliability
- **Technical**: Direct streaming, eliminated intermediary services

### 2025-05-13: Audio Response Pipeline Complete
- **Issue**: GPT responses generated but no audio output
- **Solution**: Added ElevenLabs service to initialization order
- **Impact**: Complete end-to-end voice interaction
- **Technical**: GPT → ElevenLabs → Audio playback chain

### 2025-05-13: Enhanced LLM Response Debugging
- **Achievement**: Real-time console visibility of GPT responses
- **Integration**: DebugService monitoring LLM_RESPONSE events
- **Impact**: Better debugging of voice interaction pipeline
- **Technical**: Conversation tracking, formatted output

### 2025-05-13: Voice Interaction Flow Optimization
- **Issue**: Fragmented transcripts sent to GPT during recording
- **Solution**: Accumulate complete transcripts, send on recording stop
- **Impact**: Coherent AI responses based on complete context
- **Technical**: Mouse click triggers full transcript processing

### 2025-05-13: ElevenLabs Streaming Integration
- **Achievement**: Streaming TTS with ~300ms Time-to-First-Sound
- **Technology**: ElevenLabs streaming API with MPV player
- **Impact**: Dramatically improved interaction responsiveness
- **Technical**: Real-time audio streaming, proper resource cleanup

### 2025-05-13: Enhanced Debugging System
- **Achievement**: Comprehensive system observability and monitoring
- **Features**: Component log control, performance metrics, command tracing
- **Impact**: Faster issue resolution, better system understanding
- **Technical**: Event-based monitoring, non-intrusive design

### 2025-05-13: Arduino Eye Light Integration
- **Achievement**: Optimized LED control with simplified protocol
- **Features**: Auto-detection, multiple patterns (idle/listening/thinking/speaking)
- **Impact**: Reliable visual feedback with ~20ms latency
- **Technical**: Single-char commands, graceful hardware fallback

### 2025-05-14: Command Handling Standardization
- **Issue**: Inconsistent command parsing between services
- **Solution**: Enhanced CommandDispatcherService for compound commands
- **Impact**: Consistent handling across all command types
- **Technical**: Backward compatibility with improved error handling

### 2025-05-14: Arduino Communication Protocol Fix
- **Issue**: Commands rejected by Arduino hardware
- **Solution**: Added newline-terminated commands, improved error recovery
- **Impact**: Reliable Arduino communication with fallback modes
- **Technical**: Multiple connection attempts, better buffer clearing

```markdown
### 2025-05-16: IntentRouter Feature Implementation Complete
- **Issue**: Need to enable conversational responses while executing hardware actions
- **Solution**: OpenAI function calling with IntentRouterService, comprehensive integration testing
- **Impact**: Clean separation between spoken content and machine actions, full test coverage
- **Technical**: End-to-end flow from voice transcript to hardware commands with robust error handling

### 2025-05-16: BaseService Event Handling Fix
- **Issue**: TypeError when awaiting boolean returns from event emissions and subscriptions
- **Solution**: Removed incorrect await on pyee.AsyncIOEventEmitter's emit() and on() methods
- **Impact**: Restored proper event handling functionality across all services
- **Technical**: Aligned with pyee 11.0.1 implementation, these methods are not coroutines

### 2025-05-16: Service Registration and Startup Fixes
- **Issue**: CommandDispatcherService missing from service class map, YodaModeManagerService name mismatch
- **Solution**: Added proper service mappings and corrected service names in main.py
- **Impact**: Resolved system startup errors, restored CLI interface functionality
- **Technical**: Fixed critical initialization issues preventing command processing

### 2025-05-16: MusicController Service Standardization
- **Issue**: Service not following architectural standards, incorrect directory paths
- **Solution**: Implemented Pydantic config model, fixed path resolution to /audio/music
- **Impact**: Proper music file discovery and aligned with architectural standards
- **Technical**: Added multi-level path resolution with debug logging for music files

### 2025-05-16: OpenAI Function Calling API Updates
- **Issue**: Missing required parameter 'tools[0].type' error in function definitions
- **Solution**: Updated command_functions.py format to nest properties under "function" key
- **Impact**: Aligned with OpenAI's current API requirements for tool calling
- **Technical**: Maintained backward compatibility while fixing function format specifications

### 2025-05-16: Tool Call Streaming Enhancement
- **Issue**: Tool calls in streaming chunks not properly accumulated for processing
- **Solution**: Enhanced GPTService stream processing with proper accumulation and JSON validation
- **Impact**: Voice commands now properly trigger both tool calls and intents
- **Technical**: Added chunk accumulation, JSON validation, and immediate processing of complete tool calls

### 2025-05-16: Voice Response Integration Simplification
- **Issue**: GPT streaming complexity causing text responses to be lost during tool calls
- **Solution**: Switched to non-streaming GPT architecture while keeping ElevenLabs streaming
- **Impact**: More reliable text capture for speech synthesis with simplified code
- **Technical**: Cleaner architecture ensuring both text and tool calls in single response

### 2025-05-16: Two-Step Tool Call Implementation
- **Issue**: OpenAI API returns either text OR tool calls, rarely both robustly
- **Solution**: Implemented two-step process with INTENT_EXECUTION_RESULT event for verbal feedback
- **Impact**: Ensures both command execution and natural verbal responses
- **Technical**: First call executes with tool_choice="auto", second generates feedback with tool_choice="none"
```

### 2025-05-19: Comprehensive Command Registration System Overhaul
- **Issue**: Recurring command registration and payload validation problems with multi-word commands
- **Solution**: Enhanced registration with service-specific payload transformation
- **Impact**: DJ commands now work with proper payload formats matching service expectations
- **Technical**: New register_command method includes service name for proper routing

### 2025-05-19: Backward Compatibility Layer for Command System
- **Issue**: Need to support both old and new command registration patterns during transition
- **Solution**: Added compatibility code to handle both dict and string service_info formats
- **Impact**: Existing command registrations continue working during migration
- **Technical**: Conditional logic preserves functionality with legacy format registrations

### 2025-05-19: Track Model Standardization for DJ Mode
- **Issue**: Data structure inconsistency in BrainService with "'str' object has no attribute 'name'"
- **Solution**: Standardized track models using Pydantic with consistent interfaces
- **Impact**: Consistent track handling across voice commands, CLI commands, and DJ mode
- **Technical**: Unified data structures with proper type validation throughout system

### 2025-05-19: Streamlined Command Flow Architecture
- **Issue**: Multiple inconsistent command paths causing bugs and audio ducking issues
- **Solution**: Implemented consistent three-tier architecture for all music commands
- **Impact**: All commands now follow predictable flow through TimelineExecutorService
- **Technical**: Plan-based execution ensures proper audio ducking for all command sources

### 2025-05-19: DJ Mode GPT Commentary Integration
- **Issue**: Missing GPT integration for DJ mode transitions and commentary
- **Solution**: Added integration between BrainService and GPTService with custom prompts
- **Impact**: DJ transitions now use GPT to generate authentic DJ R3X commentary
- **Technical**: Commentary varies based on transition type (initial, normal, skip)

### 2025-05-19: CLI Command Handling Improvements
- **Issue**: CLI commands failing due to payload format mismatches
- **Solution**: Fixed event subscriptions and enhanced payload processing
- **Impact**: Restored full CLI functionality with consistent error handling
- **Technical**: Added detailed payload logging for better troubleshooting

### 2025-05-19: CachedSpeechService Audio Flow Fixes
- **Issue**: Service failing to initialize and handle DJ mode transitions
- **Solution**: Implemented proper event handler registration and subscription setup
- **Impact**: Smoother DJ transitions with proper audio caching and playback
- **Technical**: Enhanced audio flow coordination between services

### 2025-05-19: Persona File Resolution Fix
- **Issue**: DJ R3X persona files not being found during service initialization
- **Solution**: Improved file path resolution and added enhanced logging
- **Impact**: Proper personality for DJ mode and verbal responses
- **Technical**: Better path configuration and file accessibility diagnostics




## 🐞 Known Issues & Future Work
- Consider wake word detection for hands-free operation
- Implement conversation persistence
- Add MQTT architecture for IoT expansion
- Web dashboard for system monitoring
- Performance optimization for extended operation

## 📊 Current Performance Metrics
- Voice interaction latency: < 2s end-to-end
- Speech synthesis TTFS: ~300ms
- LED animation response: ~20ms
- System memory usage: < 500MB
- Test coverage: 90%+ critical paths

## 🔗 Key References
- [Architecture Standards](./ARCHITECTURE_STANDARDS.md)
- [Service Template](./service_template.py)
- [Testing Framework](./tests/)
- [DebugService Documentation](./DebugService_BUGLOG.md)