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

### 2025-05-20: VLC Core Audio Property Listener Error Fix
- **Issue**: VLC repeatedly logging "AudioObjectAddPropertyListener failed" errors after music stops
- **Solution**: Enhanced VLC initialization with proper args, improved cleanup timing, suppressed verbose logging
- **Impact**: Eliminated console spam, cleaner application shutdown
- **Technical**: Added VLC instance args (--quiet, --no-video, --verbose 0), async cleanup delays, environment variable suppression

### 2025-05-27: CLI Command Payload Format Inconsistency
- **Issue**: Multi-word CLI commands like "list music" failing with "Unknown music command" error
- **Solution**: Standardized CommandDispatcher payload transformation to use consistent command/args structure
- **Impact**: Fixed recurring CLI command issues with consistent payload format
- **Technical**: Updated _transform_payload_for_service() for backward compatibility

### 2025-05-27: BrainService Startup Race Condition Fix
- **Issue**: BrainService failing to start with MEMORY_VALUE error during initialization
- **Solution**: Fixed async subscription setup by awaiting all subscription tasks before proceeding
- **Impact**: Eliminated recurring service startup race conditions
- **Technical**: Added await asyncio.gather(*subscription_tasks) to ensure subscriptions complete

### 2025-05-27: System-wide Import Path Standardization
- **Issue**: System failing to start due to EventTopics import errors in core framework
- **Solution**: Comprehensive update of all import statements to use core.event_topics consistently
- **Impact**: Fixed system startup failures and eliminated import inconsistencies
- **Technical**: Updated 20+ files across core framework, services, and tests

### 2025-05-27: CLI Command System Standardization Complete
- **Issue**: Multiple services using inconsistent command handling patterns causing CLI failures
- **Solution**: Implemented compound command decorator pattern across all services with auto-registration
- **Impact**: All CLI commands now work reliably with consistent error handling and validation
- **Technical**: Created @compound_command decorators, standardized payload handling, eliminated service-specific logic

### 2025-05-27: DJ Mode Functionality Complete
- **Issue**: DJ mode non-functional due to subscription race conditions and model import mismatches
- **Solution**: Fixed BrainService event subscriptions, corrected MusicTrack imports, added missing GPT handlers
- **Impact**: DJ mode now fully operational with end-to-end functionality working perfectly
- **Technical**: Required proper async subscription synchronization and event schema alignment

### 2025-05-29: DJ Mode Core Infrastructure Stabilization
- **Issue**: DJ Mode activation broken after recent changes - BrainService and MusicController payload field mismatches
- **Solution**: Standardized Pydantic model usage with DJModeChangedPayload and fixed commentary system imports
- **Impact**: DJ mode activation and music playback restored to working state
- **Technical**: Fixed payload field naming (is_active vs dj_mode_active) and Pydantic compatibility issues

### 2025-05-29: DJ Mode Track Metadata Validation Complete
- **Issue**: Commentary caching loop failing with Pydantic validation errors due to missing artist fields
- **Solution**: Implemented smart filename parsing with "Artist - Title" format detection and fallback defaults
- **Impact**: Track transitions with generated commentary now functional, robust handling of diverse file naming
- **Technical**: Added _parse_track_metadata() method, standardized TrackDataPayload creation across all events

### 2025-05-29: Comprehensive DJ Mode Pydantic Validation Fixes
- **Issue**: Multiple validation failures throughout DJ mode event flow preventing operation
- **Solution**: Fixed missing timestamp fields, resolved event schema conflicts, updated model serialization
- **Impact**: Commentary generation, speech caching, and timeline execution now working end-to-end
- **Technical**: Added timestamps to GPTService responses, standardized event model imports, fixed Pydantic v2 compatibility

### 2025-05-29: ElevenLabs API Integration and Timeline Execution Fixes
- **Issue**: Speech generation failing with outdated API calls, timeline plans not executing due to missing imports
- **Solution**: Updated ElevenLabs non-streaming API to modern SDK, fixed TimelineExecutorService imports and plan parsing
- **Impact**: Real audio generation for cached speech, timeline plans can now be parsed and executed
- **Technical**: Replaced elevenlabs.generate() with eleven_client.text_to_speech.convert(), fixed PlanPayload structure handling

### 2025-05-29: CantinaOS Architectural Migration Complete
- **Issue**: Two competing memory services causing coordination chaos and architecture standard violations
- **Solution**: Removed duplicate root-level services, migrated to comprehensive CantinaOS structure
- **Impact**: Single source of truth for DJ mode state, eliminated service conflicts, architecture compliance achieved
- **Technical**: Completed migration documented in condensed dev log (2025-05-07), proper service organization under /cantina_os/cantina_os/services/

### 2025-05-29: DJ Mode Critical Coordination and ID Synchronization
- **Issue**: Timeline execution timeouts due to event ID mismatches between CachedSpeechService and TimelineExecutor
- **Solution**: Fixed playback ID synchronization, implemented proper event emission with consistent UUIDs
- **Impact**: Timeline plans now execute successfully, smooth transitions between tracks working
- **Technical**: Modified CachedSpeechService._play_audio() to use request payload IDs, fixed completion event tracking

### 2025-05-29: DJ Mode Professional Audio Enhancements
- **Issue**: Poor audio balance and timing in DJ transitions affecting user experience
- **Solution**: Implemented parallel step execution with concurrent speech and crossfade, enhanced audio mixing
- **Impact**: Professional-quality DJ transitions with uninterrupted music flow and balanced audio levels
- **Technical**: Added ParallelSteps model, 60% music ducking, 1.8x commentary volume boost, 1500ms fade durations

### 2025-05-29: BlockingIOError and MemoryService Coordination Fixes
- **Issue**: httpx debug logging bypassing queued system causing BlockingIOError, track selection mismatches between services
- **Solution**: Disabled httpx debug logging, implemented proper MemoryService coordination as designed in DJ_Mode_Plan.md
- **Impact**: Eliminated async logging errors, fixed track commentary mismatches, architectural compliance
- **Technical**: httpx logger controls, MemoryService-based track coordination, proper service boundaries

### 2025-05-29: DJ Mode Automatic Transitions and Final Polish
- **Issue**: Manual DJ control working but automatic transitions failing due to missing timestamp validation
- **Solution**: Added timestamp fields to TRACK_ENDING_SOON events, implemented default_factory for future-proofing
- **Impact**: Complete DJ mode functionality with both manual and automatic transitions working
- **Technical**: Two-line elegant fix - immediate timestamp addition and EventPayload base class enhancement

### 2025-05-30: DJ Mode Commentary Coordination System Overhaul

Issue: DJ commentary system had disconnected caching and timeline execution, causing missing intro commentary and failed transitions
Solution: Implemented three-phase architectural fix using MemoryService as central coordination hub
Impact: Complete DJ mode commentary system working with proper intro playback and smooth transitions
Technical: Enhanced MemoryService with cache state tracking, refactored BrainService to use centralized coordination, added comprehensive error recovery

### 2025-05-30: Unified Timeline Architecture for All Commentary Types

Issue: Initial commentary bypassed timeline system (no ducking), violating DJ_Mode_Plan.md specification
Solution: Implemented unified timeline coordination for both streaming (intro) and cached (transition) commentary
Impact: All DJ commentary now uses professional audio ducking and volume coordination
Technical: Enhanced ElevenLabsService with TTS_GENERATE_REQUEST coordination, unified BrainService plan creation, fixed timeline executor routing

### 2025-05-30: Audio Ducking System Fixes and Crossfade Bug Resolution

Issue: Music not ducking during initial commentary, crossfade overriding ducked state causing volume jumps
Solution: Fixed event topic mismatches and crossfade volume calculations to respect ducking state
Impact: Professional DJ audio experience with consistent 50% ducking and smooth transitions
Technical: Added TRACK_PLAYING events to MusicController, fixed crossfade to respect current ducking volume

### 2025-05-30: Unified Timeline Routing for All Speech Interactions

Issue: Normal engage mode conversations bypassed ducking system, causing speech over full-volume music
Solution: Routed all speech interactions through timeline coordination system for consistent ducking
Impact: Professional audio coordination for both DJ mode and normal conversations
Technical: Modified ElevenLabsService to create timeline plans for all speech, unified architecture across interaction modes

### 2025-05-30: VLC Core Audio Error Suppression

Issue: VLC flooding console with "AudioObjectAddPropertyListener failed" errors on macOS after music stops
Solution: Multi-layered VLC configuration with system-level logging suppression and enhanced cleanup
Impact: Clean application shutdown without console spam while maintaining full audio functionality
Technical: Enhanced VLC initialization args, environment variable suppression, improved async cleanup timing

Key Architectural Achievements:

Centralized State Management: MemoryService now serves as single source of truth for DJ mode coordination
Unified Audio Coordination: All speech (DJ and normal) uses timeline system for consistent ducking
Professional Audio Experience: 50% ducking, smooth crossfades, no volume jumps during transitions
Robust Error Recovery: Comprehensive fallback mechanisms for failed commentary and transitions
Clean System Operation: Eliminated VLC error spam while preserving functionality

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