# CantinaOS Service Registry

This document provides a comprehensive catalog of all services in the CantinaOS system. Use this as your primary navigation aid when working with the codebase.

## Purpose

This registry serves as:
- **Service Discovery**: Quick lookup of service capabilities and interfaces
- **Architecture Reference**: Understanding service relationships and dependencies
- **Development Guide**: Finding the right service for a feature or integration
- **Debugging Aid**: Tracing event flows and service interactions

## How to Use This Registry

1. **Quick Reference**: Use the summary table below for fast service lookup
2. **Detailed Analysis**: Read individual service sections for complete understanding
3. **Event Flow Tracing**: Follow event topic references to understand inter-service communication
4. **Architecture Planning**: Use dependency information for system design decisions

---

## Service Summary Table

| Service Name | File Path | Purpose | Primary Events (In/Out) | Key Dependencies |
|--------------|-----------|---------|-------------------------|------------------|
| **BaseService** | `services/base.py` | Abstract service foundation | `SERVICE_STATUS_UPDATE` | Event Bus |
| **BrainService** | `services/brain_service.py` | DJ mode orchestration & command routing | `DJ_COMMAND` → `PLAN_READY` | MemoryService, GPTService |
| **VoiceManagerService** | `services/voice_manager_service.py` | Voice interaction coordination | `SYSTEM_MODE_CHANGE` → `SERVICE_STATE_CHANGED` | Mode system |
| **LoggingService** | `services/logging_service/` | Centralized log aggregation | All logs → `DASHBOARD_LOG` | File system |
| **MemoryService** | `services/memory_service/` | System state & conversation memory | `MEMORY_GET/SET` → `MEMORY_UPDATED` | File persistence |
| **TimelineExecutorService** | `services/timeline_executor_service/` | Plan execution & step coordination | `PLAN_READY` → `PLAN_ENDED` | Multiple services |
| **GPTService** | `services/gpt_service.py` | OpenAI integration & intent detection | `TRANSCRIPTION_FINAL` → `LLM_RESPONSE` | OpenAI API |
| **ElevenLabsService** | `services/elevenlabs_service.py` | Text-to-speech synthesis | `LLM_RESPONSE` → `SPEECH_SYNTHESIS_*` | ElevenLabs API |
| **MicInputService** | `services/mic_input_service.py` | Microphone input handling | Audio → `AUDIO_CHUNK` | System audio |
| **DeepgramDirectMicService** | `services/deepgram_direct_mic_service.py` | Real-time speech recognition | Audio → `TRANSCRIPTION_FINAL` | Deepgram API |
| **DeepgramTranscriptionService** | `services/deepgram_transcription_service.py` | Speech-to-text processing | `AUDIO_CHUNK` → `TRANSCRIPTION_TEXT` | Deepgram API |
| **MusicControllerService** | `services/music_controller_service.py` | Music playback & library management | `MUSIC_COMMAND` → `TRACK_PLAYING` | VLC player |
| **CachedSpeechService** | `services/cached_speech_service.py` | Pre-generated speech caching | `SPEECH_CACHE_REQUEST` → `SPEECH_CACHE_READY` | File system |
| **EyeLightControllerService** | `services/eye_light_controller_service.py` | Arduino LED matrix control | `EYE_COMMAND` → `LED_COMMAND_SUCCESS` | Arduino serial |
| **SimpleEyeAdapter** | `services/simple_eye_adapter.py` | Eye animation patterns | `LLM_SENTIMENT_ANALYZED` → `EYE_COMMAND` | EyeLightController |
| **CLIService** | `services/cli_service.py` | Command-line interface | User input → `CLI_COMMAND` | Terminal |
| **CommandDispatcherService** | `services/command_dispatcher_service.py` | CLI command routing | `CLI_COMMAND` → service-specific events | All services |
| **IntentRouterService** | `services/intent_router_service.py` | Intent execution routing | `INTENT_DETECTED` → `INTENT_EXECUTION_RESULT` | ToolExecutor |
| **ToolExecutorService** | `services/tool_executor_service.py` | Function call execution | `TOOL_EXECUTION_REQUEST` → `TOOL_EXECUTION_RESULT` | Tool functions |
| **WebBridgeService** | `services/web_bridge_service.py` | Web dashboard connectivity | All events ↔ WebSocket | Socket.io |
| **ModeChangeSound** | `services/mode_change_sound_service.py` | Mode transition audio feedback | `SYSTEM_MODE_CHANGE` → Audio playback | System audio |
| **ModeCommandHandler** | `services/mode_command_handler_service.py` | System mode management | `SYSTEM_SET_MODE_REQUEST` → `SYSTEM_MODE_CHANGE` | Mode system |
| **MouseInputService** | `services/mouse_input_service.py` | Mouse-based recording control | Mouse events → `VOICE_LISTENING_*` | System input |
| **YodaModeManager** | `services/yoda_mode_manager_service.py` | Special mode management | `SYSTEM_MODE_CHANGE` → Mode-specific logic | Mode system |
| **DebugService** | `services/debug_service.py` | Development debugging tools | `DEBUG_COMMAND` → `DEBUG_LOG` | System introspection |

---

## Core Services

### BaseService (`services/base.py`)

**Architecture Role**: Foundation class for all CantinaOS services

**Key Features**:
- Standardized service lifecycle (`_start()`, `_stop()`)
- Event bus integration with subscription management
- Service status reporting and health monitoring
- Graceful error handling and recovery patterns

**Event Interface**:
- **Emits**: `SERVICE_STATUS_UPDATE` - Service health and state changes
- **Subscribes**: None (base functionality)

**Dependencies**: Event Bus core system

**Usage Pattern**:
```python
class MyService(BaseService):
    async def _start(self) -> None:
        # Override _start, not start
        await self.subscribe(EventTopics.SOME_EVENT, self.handler)
    
    async def _stop(self) -> None:
        # Override _stop, not stop
        await self.cleanup_resources()
```

---

## Core Orchestration Services

### BrainService (`services/brain_service.py`)

**Architecture Role**: Central coordination hub for DJ mode, conversation orchestration, and high-level system logic

**Key Features**:
- **DJ Mode Management**: Track selection, transition planning, commentary coordination
- **Command Routing**: CLI command dispatch using decorators (`@compound_command`)
- **Timeline Planning**: Creates complex multi-step plans for DJ transitions
- **Commentary Caching**: Coordinates speech pre-generation for seamless transitions
- **State Management**: Integrates with MemoryService for persistent state

**Event Interface**:
- **Subscribes**: `DJ_COMMAND`, `DJ_MODE_CHANGED`, `MUSIC_LIBRARY_UPDATED`, `TRACK_ENDING_SOON`, `GPT_COMMENTARY_RESPONSE`, `SPEECH_CACHE_READY`
- **Emits**: `PLAN_READY`, `DJ_COMMENTARY_REQUEST`, `MUSIC_COMMAND`, `CLI_RESPONSE`

**Command Registration**:
- `dj start` - Activate DJ mode with track selection
- `dj stop` - Deactivate DJ mode and stop music
- `dj next` - Force transition to next track
- `dj queue <track>` - Queue specific track (planned)

**Dependencies**: MemoryService, GPTService, MusicController, CachedSpeechService, TimelineExecutor

**Critical Patterns**:
- Uses `@compound_command` decorators for CLI integration
- Implements commentary caching loop for seamless transitions
- Creates `DjTransitionPlanPayload` objects for timeline execution
- Coordinates music ducking during speech synthesis

### VoiceManagerService (`services/voice_manager_service.py`)

**Architecture Role**: High-level voice interaction coordination and state management

**Key Features**:
- Voice interaction enable/disable based on system mode
- Speech recognition service coordination
- Voice input state management
- Mode-aware voice processing control

**Event Interface**:
- **Subscribes**: `SYSTEM_MODE_CHANGE`
- **Emits**: `SERVICE_STATE_CHANGED`, `SERVICE_STATUS_UPDATE`

**Dependencies**: System mode management, voice input services

**State Management**:
- `disabled` - Voice input inactive
- `enabled` - Voice input active and processing

---

## Data & State Services

### LoggingService (`services/logging_service/`)

**Architecture Role**: Centralized logging aggregation with dashboard integration and persistent storage

**Key Features**:
- **Universal Log Capture**: Custom Python logging handler captures all service logs
- **Dashboard Streaming**: Real-time log delivery to web interface
- **Session Management**: Timestamped log files for each system session
- **Smart Deduplication**: Prevents log flooding with intelligent filtering
- **Circuit Breaker**: Emergency protection against excessive logging
- **Async Processing**: Non-blocking log writing with batch processing

**Event Interface**:
- **Emits**: `DASHBOARD_LOG` - Structured log entries for web dashboard
- **Captures**: All Python logging output across the system

**Dependencies**: File system for session storage, WebBridge for dashboard delivery

**Configuration Options**:
- `max_memory_logs`: In-memory buffer size (default: 1000)
- `session_file_path`: Log file directory (default: "./logs")
- `enable_dashboard_streaming`: Real-time dashboard logs (default: true)
- `deduplication_window`: Log dedup timeframe (default: 30s)

**Critical Patterns**:
- Aggressive filtering to prevent feedback loops (WebSocket, Socket.io, own logs)
- Async queue processing for file I/O performance
- Ring buffer for memory-efficient log storage

### MemoryService (`services/memory_service/`)

**Architecture Role**: System working memory with persistent state and event-driven access

**Key Features**:
- **Persistent State**: JSON file storage with automatic save/load
- **Chat History Management**: Conversation context with configurable limits
- **DJ Mode State**: Track history, user preferences, cache coordination
- **Event-Driven Access**: `MEMORY_GET`/`MEMORY_SET` pattern for cross-service communication
- **Commentary Cache Coordination**: DJ mode speech cache state tracking
- **Wait Predicates**: Async waiting for specific state conditions

**Event Interface**:
- **Subscribes**: `TRACK_PLAYING`, `TRACK_STOPPED`, `INTENT_DETECTED`, `SYSTEM_MODE_CHANGE`, `MEMORY_GET`, `MEMORY_SET`
- **Emits**: `MEMORY_UPDATED` - State change notifications

**State Keys**:
- `chat_history` - Conversation message history
- `music_playing` - Current playback state
- `current_track` - Active track metadata
- `dj_mode_active` - DJ mode status
- `dj_track_history` - Recently played tracks
- `dj_commentary_cache_mappings` - Cache request tracking
- `dj_commentary_cache_ready` - Cache readiness status

**Dependencies**: File system for persistence, all services for state coordination

### TimelineExecutorService (`services/timeline_executor_service/`)

**Architecture Role**: Executes complex multi-step plans with precise timing and coordination

**Key Features**:
- **Plan Execution**: Processes `PlanReadyPayload` objects with multiple steps
- **Step Types**: Supports speech, music crossfade, ducking, parallel execution
- **Layer Management**: Separate execution contexts for different plan types
- **Error Recovery**: Handles step failures with graceful degradation
- **Timing Coordination**: Precise timing for audio transitions and effects

**Event Interface**:
- **Subscribes**: `PLAN_READY` - Incoming execution plans
- **Emits**: `PLAN_STARTED`, `STEP_EXECUTED`, `PLAN_ENDED` - Execution lifecycle events

**Step Types Supported**:
- `speak` - Text-to-speech with automatic ducking coordination
- `play_cached_speech` - Pre-generated speech playback
- `music_crossfade` - Track transition with fade timing
- `music_duck` - Lower music volume for speech
- `music_unduck` - Restore music volume
- `parallel_steps` - Concurrent step execution

**Dependencies**: ElevenLabsService (speech), MusicController (audio), CachedSpeechService

---

## AI & Language Services

### GPTService (`services/gpt_service.py`)

**Architecture Role**: OpenAI integration for natural language processing, intent detection, and conversation management

**Key Features**:
- **Conversation Management**: Session memory with token/message limits
- **Tool Calling**: Function call support for intent detection
- **Streaming Responses**: Real-time response generation
- **DJ Commentary**: Specialized commentary generation for DJ mode
- **Intent Detection**: Automatic function calling for voice commands
- **Rate Limiting**: Request throttling for API compliance
- **Verbal Feedback**: Post-action response generation

**Event Interface**:
- **Subscribes**: `TRANSCRIPTION_FINAL`, `VOICE_LISTENING_STOPPED`, `INTENT_EXECUTION_RESULT`, `DJ_COMMENTARY_REQUEST`
- **Emits**: `LLM_RESPONSE`, `INTENT_DETECTED`, `GPT_COMMENTARY_RESPONSE`

**Configuration**:
- `MODEL`: OpenAI model selection (default: "gpt-4.1-mini")
- `MAX_TOKENS`: Context window limit (default: 4000)
- `TEMPERATURE`: Response creativity (default: 0.7)
- `STREAMING`: Enable response streaming (default: true)

**Dependencies**: OpenAI API, conversation context files (personas)

**Personas**:
- Main DJ R3X persona for general conversation
- Specialized DJ transition persona for music commentary
- Verbal feedback persona for action responses

### ElevenLabsService (`services/elevenlabs_service.py`)

**Architecture Role**: Text-to-speech synthesis with voice cloning and audio processing

**Key Features**:
- **Voice Synthesis**: High-quality TTS with custom voice models
- **Audio Processing**: Format conversion and optimization
- **Amplitude Tracking**: Real-time audio level monitoring for visual effects
- **Streaming Support**: Progressive audio generation
- **Quality Settings**: Configurable audio quality and format options

**Event Interface**:
- **Subscribes**: `LLM_RESPONSE`, `SPEECH_GENERATION_REQUEST`
- **Emits**: `SPEECH_SYNTHESIS_STARTED`, `SPEECH_SYNTHESIS_ENDED`, `SPEECH_AMPLITUDE`

**Dependencies**: ElevenLabs API, system audio output

---

## Input Services

### MicInputService (`services/mic_input_service.py`)

**Architecture Role**: System microphone access and audio preprocessing

**Key Features**:
- **Audio Capture**: System microphone input with configurable quality
- **Preprocessing**: Audio filtering and enhancement
- **Chunk Processing**: Real-time audio streaming for speech recognition
- **Device Management**: Microphone selection and configuration

**Event Interface**:
- **Emits**: `AUDIO_CHUNK`, `AUDIO_RAW_CHUNK`

**Dependencies**: System audio input devices

### DeepgramDirectMicService (`services/deepgram_direct_mic_service.py`)

**Architecture Role**: Real-time speech recognition with direct Deepgram API integration

**Key Features**:
- **Live Transcription**: Real-time speech-to-text processing
- **WebSocket Streaming**: Direct Deepgram API connection
- **Interim Results**: Progressive transcription updates
- **Audio Processing**: Automatic gain control and noise reduction
- **Connection Management**: Robust WebSocket handling with reconnection

**Event Interface**:
- **Subscribes**: Audio input, recording control events
- **Emits**: `TRANSCRIPTION_INTERIM`, `TRANSCRIPTION_FINAL`, `TRANSCRIPTION_METRICS`

**Dependencies**: Deepgram API, microphone input

### DeepgramTranscriptionService (`services/deepgram_transcription_service.py`)

**Architecture Role**: Batch speech recognition for audio file processing

**Key Features**:
- **File Processing**: Audio file transcription
- **Multiple Formats**: Support for various audio formats
- **Batch Operations**: Efficient handling of multiple files
- **Quality Control**: Transcription confidence scoring

**Event Interface**:
- **Subscribes**: `AUDIO_CHUNK`
- **Emits**: `TRANSCRIPTION_TEXT`

**Dependencies**: Deepgram API, file system

### MouseInputService (`services/mouse_input_service.py`)

**Architecture Role**: Mouse-based recording control for voice input

**Key Features**:
- **Click-to-Record**: Mouse button recording control
- **Visual Feedback**: Recording state indication
- **Global Hotkeys**: System-wide mouse capture
- **State Management**: Recording session tracking

**Event Interface**:
- **Emits**: `VOICE_LISTENING_STARTED`, `VOICE_LISTENING_STOPPED`, `MOUSE_RECORDING_STOPPED`

**Dependencies**: System input capture

---

## Output Services

### MusicControllerService (`services/music_controller_service.py`)

**Architecture Role**: Music playback engine with library management, crossfading, and ducking

**Key Features**:
- **Library Management**: Automatic music file discovery and indexing
- **Playback Control**: Play, pause, stop, seek, volume control
- **Crossfading**: Smooth transitions between tracks
- **Audio Ducking**: Automatic volume reduction during speech
- **Progress Tracking**: Real-time playback position monitoring
- **Format Support**: Multiple audio format compatibility via VLC

**Event Interface**:
- **Subscribes**: `MUSIC_COMMAND`, `SPEECH_SYNTHESIS_STARTED`, `SPEECH_SYNTHESIS_ENDED`
- **Emits**: `TRACK_PLAYING`, `TRACK_STOPPED`, `TRACK_ENDING_SOON`, `MUSIC_LIBRARY_UPDATED`, `MUSIC_PROGRESS`

**Music Commands**:
- `play <query>` - Play track matching query
- `stop` - Stop current playback
- `pause` / `resume` - Pause/resume playback
- `crossfade <track>` - Transition to new track
- `duck <level>` - Lower volume for speech
- `unduck` - Restore original volume

**Dependencies**: VLC media player, file system for music library

### CachedSpeechService (`services/cached_speech_service.py`)

**Architecture Role**: Pre-generated speech caching for seamless DJ transitions

**Key Features**:
- **Speech Caching**: Pre-generate and store TTS audio
- **Cache Management**: Intelligent cache lifecycle with cleanup
- **Playback Control**: Direct cached audio playback
- **Format Optimization**: Efficient audio storage formats
- **Request Tracking**: Associate cache entries with request IDs

**Event Interface**:
- **Subscribes**: `SPEECH_CACHE_REQUEST`, `SPEECH_CACHE_PLAYBACK_REQUEST`
- **Emits**: `SPEECH_CACHE_READY`, `SPEECH_CACHE_ERROR`, `SPEECH_CACHE_PLAYBACK_COMPLETED`

**Dependencies**: ElevenLabsService (for generation), file system (for storage)

### EyeLightControllerService (`services/eye_light_controller_service.py`)

**Architecture Role**: Arduino LED matrix control for visual feedback and animations

**Key Features**:
- **LED Matrix Control**: 2x MAX7219 LED matrix management
- **Animation Patterns**: Various eye expressions and movements
- **Real-time Response**: Audio amplitude and sentiment-based animations
- **Serial Communication**: Arduino communication with error handling
- **Pattern Library**: Pre-defined animation sequences

**Event Interface**:
- **Subscribes**: `EYE_COMMAND`, `SPEECH_AMPLITUDE`, `LLM_SENTIMENT_ANALYZED`
- **Emits**: `LED_COMMAND_SUCCESS`, `LED_COMMAND_FAILURE`

**Eye Patterns**:
- `normal` - Standard eye appearance
- `talking` - Animation during speech
- `listening` - Animation during audio input
- `thinking` - Animation during AI processing
- `happy`, `sad`, `angry` - Emotion-based expressions

**Dependencies**: Arduino Mega 2560, MAX7219 LED drivers, serial communication

### SimpleEyeAdapter (`services/simple_eye_adapter.py`)

**Architecture Role**: Simplified interface for eye light control with automatic pattern mapping

**Key Features**:
- **Pattern Translation**: Maps high-level commands to specific LED patterns
- **Sentiment Integration**: Automatic eye expressions based on AI sentiment analysis
- **Animation Coordination**: Synchronizes eye animations with speech and audio
- **Fallback Patterns**: Graceful degradation when hardware unavailable

**Event Interface**:
- **Subscribes**: `LLM_SENTIMENT_ANALYZED`, high-level animation triggers
- **Emits**: `EYE_COMMAND`

**Dependencies**: EyeLightControllerService

---

## Interface & Integration Services

### CLIService (`services/cli_service.py`)

**Architecture Role**: Command-line interface for system control and debugging

**Key Features**:
- **Interactive Console**: Real-time command input and response
- **Command History**: Session command tracking
- **Help System**: Built-in documentation and usage guides
- **Auto-completion**: Command and parameter completion
- **Error Handling**: User-friendly error messages and suggestions

**Event Interface**:
- **Emits**: `CLI_COMMAND` - User command input
- **Subscribes**: `CLI_RESPONSE` - Command execution results

**Dependencies**: Terminal/console interface

### CommandDispatcherService (`services/command_dispatcher_service.py`)

**Architecture Role**: Routes CLI commands to appropriate services using compound command patterns

**Key Features**:
- **Command Parsing**: Structured command and argument parsing
- **Service Routing**: Maps command patterns to specific services
- **Validation**: Command format and parameter validation
- **Response Coordination**: Aggregates and formats command results

**Event Interface**:
- **Subscribes**: `CLI_COMMAND`
- **Emits**: Service-specific command events (e.g., `DJ_COMMAND`, `MUSIC_COMMAND`)

**Command Patterns**:
- `dj <action>` → BrainService
- `music <action>` → MusicController
- `system <action>` → System services
- `debug <action>` → DebugService

**Dependencies**: All command-handling services

### IntentRouterService (`services/intent_router_service.py`)

**Architecture Role**: Routes detected intents to appropriate execution services

**Key Features**:
- **Intent Mapping**: Maps intent names to execution handlers
- **Parameter Validation**: Ensures intent parameters meet requirements
- **Execution Coordination**: Manages intent execution lifecycle
- **Result Aggregation**: Collects and formats execution results

**Event Interface**:
- **Subscribes**: `INTENT_DETECTED`
- **Emits**: `INTENT_EXECUTION_RESULT`, tool execution events

**Dependencies**: ToolExecutorService, intent handler services

### ToolExecutorService (`services/tool_executor_service.py`)

**Architecture Role**: Executes function calls and tool invocations from AI systems

**Key Features**:
- **Function Registry**: Manages available tool functions
- **Execution Engine**: Safely executes tool calls with parameter validation
- **Result Formatting**: Structures execution results for AI consumption
- **Error Handling**: Graceful handling of execution failures
- **Security**: Sandboxed execution environment

**Event Interface**:
- **Subscribes**: `TOOL_EXECUTION_REQUEST`
- **Emits**: `TOOL_EXECUTION_RESULT`

**Dependencies**: Registered tool functions, parameter validation

### WebBridgeService (`services/web_bridge_service.py`)

**Architecture Role**: WebSocket bridge connecting CantinaOS event bus to web dashboard

**Key Features**:
- **Bidirectional Communication**: Event bus ↔ WebSocket translation
- **Real-time Updates**: Live system monitoring and control
- **Command Translation**: Web UI commands → CantinaOS events
- **State Synchronization**: Dashboard state consistency with system state
- **Connection Management**: Robust WebSocket handling with reconnection

**Event Interface**:
- **Subscribes**: Most system events for dashboard updates
- **Emits**: Web command events (`WEB_VOICE_COMMAND`, `WEB_MUSIC_COMMAND`, etc.)

**Dependencies**: Socket.io, FastAPI bridge service, all monitored services

---

## System Services

### ModeChangeSound (`services/mode_change_sound_service.py`)

**Architecture Role**: Audio feedback for system mode transitions

**Key Features**:
- **Mode Transition Audio**: Distinctive sounds for mode changes
- **Audio Library**: Pre-recorded transition sound effects
- **Volume Control**: Appropriate audio levels for feedback
- **Format Support**: Multiple audio format compatibility

**Event Interface**:
- **Subscribes**: `SYSTEM_MODE_CHANGE`
- **Emits**: Audio playback events

**Dependencies**: System audio output

### ModeCommandHandler (`services/mode_command_handler_service.py`)

**Architecture Role**: Manages system mode transitions and state changes

**Key Features**:
- **Mode Coordination**: Orchestrates system-wide mode changes
- **State Validation**: Ensures valid mode transitions
- **Service Notification**: Informs all services of mode changes
- **Rollback Support**: Handles failed mode transitions

**Event Interface**:
- **Subscribes**: `SYSTEM_SET_MODE_REQUEST`
- **Emits**: `SYSTEM_MODE_CHANGE`, `MODE_TRANSITION_COMPLETE`

**Supported Modes**:
- `standard` - Normal operation mode
- `dj` - DJ mode with music and commentary
- `interactive` - Enhanced voice interaction mode

**Dependencies**: All mode-aware services

### YodaModeManager (`services/yoda_mode_manager_service.py`)

**Architecture Role**: Special mode implementation for enhanced AI interaction

**Key Features**:
- **Enhanced Responses**: Modified AI behavior patterns
- **Special Commands**: Mode-specific command handling
- **Personality Adaptation**: Adjusted AI persona and responses
- **State Management**: Mode-specific state tracking

**Event Interface**:
- **Subscribes**: `SYSTEM_MODE_CHANGE`, mode-specific events
- **Emits**: Enhanced response events

**Dependencies**: GPTService, personality configuration

### DebugService (`services/debug_service.py`)

**Architecture Role**: Development and debugging tools for system introspection

**Key Features**:
- **System Introspection**: Service state and health monitoring
- **Performance Monitoring**: Metrics collection and analysis
- **Event Tracing**: Event flow tracking and debugging
- **Configuration Management**: Runtime configuration changes
- **Log Analysis**: Advanced log filtering and analysis

**Event Interface**:
- **Subscribes**: `DEBUG_COMMAND`, system events for monitoring
- **Emits**: `DEBUG_LOG`, `DEBUG_PERFORMANCE`, performance metrics

**Debug Commands**:
- `status` - System health overview
- `events` - Event flow monitoring
- `performance` - Performance metrics
- `config` - Configuration inspection/modification

**Dependencies**: All services for monitoring, system introspection APIs

---

## Event Flow Diagrams

### DJ Mode Activation Flow
```
CLI Input "dj start"
  ↓
CLIService → CLI_COMMAND
  ↓
CommandDispatcher → DJ_COMMAND
  ↓
BrainService → DJ_MODE_CHANGED
  ↓
MemoryService (state update)
  ↓
BrainService → MUSIC_COMMAND
  ↓
MusicController → TRACK_PLAYING
```

### Voice Interaction Flow
```
Microphone Input
  ↓
DeepgramDirectMic → TRANSCRIPTION_FINAL
  ↓
GPTService → LLM_RESPONSE + INTENT_DETECTED
  ↓
ElevenLabsService ← LLM_RESPONSE
IntentRouter ← INTENT_DETECTED
  ↓                    ↓
SPEECH_SYNTHESIS      INTENT_EXECUTION_RESULT
  ↓                    ↓
Audio Output         GPTService → LLM_RESPONSE (feedback)
```

### DJ Commentary Caching Flow
```
BrainService (track ending soon)
  ↓
DJ_COMMENTARY_REQUEST
  ↓
GPTService → GPT_COMMENTARY_RESPONSE
  ↓
BrainService → SPEECH_CACHE_REQUEST
  ↓
CachedSpeechService → SPEECH_CACHE_READY
  ↓
BrainService → PLAN_READY (with cached speech)
  ↓
TimelineExecutor → Seamless transition
```

---

## Architecture Cross-References

**Related Documentation**:
- [`CANTINA_OS_SYSTEM_ARCHITECTURE.md`](./CANTINA_OS_SYSTEM_ARCHITECTURE.md) - Complete system overview
- [`ARCHITECTURE_STANDARDS.md`](./ARCHITECTURE_STANDARDS.md) - Service development standards  
- [`SERVICE_CREATION_GUIDELINES.md`](./SERVICE_CREATION_GUIDELINES.md) - Creating new services

**Key Architecture Patterns**:
- **Event-Driven Communication**: All services communicate via event bus
- **Async Lifecycle**: Services use `_start()`/`_stop()` override pattern
- **Graceful Degradation**: Services handle dependency failures gracefully
- **Resource Cleanup**: Proper cleanup in `_stop()` methods prevents resource leaks
- **Status Reporting**: All services emit `SERVICE_STATUS_UPDATE` events

**Service Creation Checklist**:
- [ ] Inherit from `BaseService`
- [ ] Implement `_start()` and `_stop()` methods (not `start()`/`stop()`)
- [ ] Use `EventTopics` enum for all event names
- [ ] Emit `SERVICE_STATUS_UPDATE` events for health monitoring
- [ ] Handle errors gracefully with proper logging
- [ ] Clean up resources in `_stop()` method
- [ ] Use Pydantic models for event payloads
- [ ] Follow async/await patterns correctly
- [ ] Register CLI commands using decorators if applicable
- [ ] Update this registry when creating new services

---

## Development Notes

**Service Integration Tips**:
1. **Event Topics**: Always use `EventTopics` enum values, never string literals
2. **Payload Validation**: Use Pydantic models for type safety and validation
3. **Error Handling**: Implement try/catch around event handlers to prevent service crashes
4. **Memory Management**: Store large data in MemoryService, not service instance variables
5. **Testing**: Create unit tests that mock event bus interactions

**Common Pitfalls**:
- Overriding `start()`/`stop()` instead of `_start()`/`_stop()`
- Creating circular event dependencies between services
- Not cleaning up resources in `_stop()` method
- Using string literals for event topics instead of `EventTopics` enum
- Blocking the event loop with synchronous operations

**Performance Considerations**:
- Use async operations for I/O-bound tasks
- Implement batching for high-frequency events
- Use MemoryService for shared state instead of service-to-service communication
- Consider caching strategies for expensive operations

This registry should be updated whenever:
- New services are added to the system
- Service interfaces change significantly
- Major architectural changes occur
- New event topics are introduced