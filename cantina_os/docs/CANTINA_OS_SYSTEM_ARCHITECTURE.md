# CantinaOS System Architecture

## 1. System Overview

CantinaOS is an event-driven system designed to power the DJ R3X voice application. It provides a modular, extensible architecture for building interactive voice applications with support for hardware integration, audio processing, natural language understanding, and dynamic visual feedback.

### 1.1 Event-Driven Architecture

The core of CantinaOS is built on an event-driven architecture that:

- Decouples service dependencies through asynchronous event-based communication
- Enables independent service development and testing
- Provides a flexible mechanism for adding new capabilities
- Facilitates real-time responsiveness and fault tolerance

The system uses a central event bus that connects all services, allowing them to publish events and subscribe to events from other services. This architecture enables complex interactions without direct coupling between components.

### 1.2 Event Bus Pattern

The event bus implements a publish/subscribe (pub/sub) pattern where:

- Services publish (emit) events to named topics
- Other services subscribe to specific topics of interest
- The event bus routes events to appropriate subscribers
- Events carry typed payloads with standardized metadata

This pattern enables:
- One-to-many communication (one publisher, many subscribers)
- Many-to-one communication (many publishers, one subscriber)
- Anonymous communication (publishers don't know who receives events)
- Runtime discovery (services can dynamically subscribe/unsubscribe)

### 1.3 Key Design Principles

CantinaOS follows these key architectural principles:

- **Service Autonomy**: Each service operates independently with clear boundaries
- **Event Standardization**: All events use structured payload formats
- **Graceful Degradation**: Services can handle partial system failures
- **Lifecycle Management**: Services follow a consistent startup and shutdown pattern
- **Configurability**: Services can be configured at initialization
- **Extensibility**: The system can be extended with new services
- **Error Isolation**: Errors in one service do not crash the entire system

## 2. Service Registry Table

| Service Name | Purpose | Events Subscribed (Inputs) | Events Published (Outputs) | Configuration | Hardware Dependencies |
|--------------|---------|----------------------------|----------------------------|---------------|----------------------|
| DeepgramDirectMicService | Audio capture and transcription | VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED, MIC_RECORDING_START, MIC_RECORDING_STOP | TRANSCRIPTION_INTERIM, TRANSCRIPTION_FINAL, TRANSCRIPTION_ERROR, TRANSCRIPTION_METRICS | DEEPGRAM_API_KEY, model options | Microphone |
| GPTService | Natural language processing | TRANSCRIPTION_FINAL | LLM_RESPONSE, LLM_RESPONSE_CHUNK, LLM_PROCESSING_STARTED, LLM_PROCESSING_ENDED | OPENAI_API_KEY, MODEL, TEMPERATURE, SYSTEM_PROMPT | None |
| ElevenLabsService | Text-to-speech generation | SPEECH_GENERATION_REQUEST, LLM_RESPONSE | SPEECH_SYNTHESIS_STARTED, SPEECH_SYNTHESIS_AMPLITUDE, SPEECH_SYNTHESIS_ENDED | ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID | Audio output |
| EyeLightControllerService | LED pattern control | EYE_COMMAND, CLI_COMMAND, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED, SPEECH_SYNTHESIS_STARTED, SPEECH_SYNTHESIS_ENDED | LED_COMMAND_SUCCESS, LED_COMMAND_FAILURE | serial_port, baud_rate | Arduino (LED controller) |
| MusicControllerService | Background music playback | MUSIC_COMMAND, AUDIO_DUCKING_START, AUDIO_DUCKING_STOP | MUSIC_PLAYBACK_STARTED, MUSIC_PLAYBACK_STOPPED, MUSIC_VOLUME_CHANGED | music_directory | Audio output |
| CommandDispatcherService | Central command routing, validation, and dispatch system | CLI_COMMAND, CLI_HELP_REQUEST, CLI_STATUS_REQUEST | EYE_COMMAND, MUSIC_COMMAND, SYSTEM_MODE_REQUEST, SYSTEM_SHUTDOWN_REQUESTED, CLI_RESPONSE | command_registry | None |
| YodaModeManagerService | System mode orchestration | SYSTEM_SET_MODE_REQUEST, CLI_COMMAND | SYSTEM_MODE_CHANGE, MODE_TRANSITION_STARTED, MODE_TRANSITION_COMPLETE | None | None |
| MouseInputService | Handles mouse input | Mouse events | MIC_RECORDING_START, MIC_RECORDING_STOP | None | Mouse |
| CLIService | Command-line interface | CLI_RESPONSE | CLI_COMMAND | None | Terminal |
| WebBridgeService | Web dashboard integration via FastAPI/Socket.IO with Pydantic validation | SERVICE_STATUS_UPDATE, TRANSCRIPTION_FINAL, TRANSCRIPTION_INTERIM, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED, VOICE_PROCESSING_COMPLETE, SPEECH_SYNTHESIS_COMPLETED, MUSIC_PLAYBACK_STARTED, MUSIC_PLAYBACK_STOPPED, MUSIC_PROGRESS, DJ_MODE_CHANGED, SYSTEM_MODE_CHANGE, DASHBOARD_LOG | SYSTEM_SET_MODE_REQUEST, MUSIC_COMMAND, DJ_COMMAND | web_port, cors_origins, validation_schemas | None |
| LoggingService | Centralized system logging and dashboard log streaming | All system events (as log capture) | DASHBOARD_LOG | log_level, session_file_path, enable_dashboard_streaming | None |
| DebugService | Legacy logging and diagnostics | DEBUG_LOG, Various events | None | log_level | None |

## 3. Event Bus Topology

### 3.1 Core System Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| SYSTEM_STARTUP | CantinaOS | All services | BaseEventPayload | System initialization |
| SYSTEM_SHUTDOWN | CantinaOS, CLIService | All services | BaseEventPayload | Graceful shutdown |
| SYSTEM_SET_MODE_REQUEST | WebBridgeService, CLIService | YodaModeManagerService | SystemModeRequestPayload | Request mode change |
| SYSTEM_MODE_CHANGE | YodaModeManagerService | All services, WebBridgeService | SystemModePayload | Mode transitions |
| SYSTEM_ERROR | Any service | DebugService | BaseEventPayload | System-level errors |

### 3.2 Voice Processing Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| VOICE_LISTENING_STARTED | YodaModeManagerService, MouseInputService | DeepgramDirectMicService, EyeLightControllerService | BaseEventPayload | Start voice capture |
| VOICE_LISTENING_STOPPED | YodaModeManagerService, MouseInputService | DeepgramDirectMicService, EyeLightControllerService | BaseEventPayload | Stop voice capture |
| TRANSCRIPTION_INTERIM | DeepgramDirectMicService | DebugService | TranscriptionTextPayload | Partial transcription |
| TRANSCRIPTION_FINAL | DeepgramDirectMicService | GPTService, DebugService | TranscriptionTextPayload | Complete transcription |
| LLM_RESPONSE | GPTService | ElevenLabsService | LLMResponsePayload | Complete GPT response |
| LLM_RESPONSE_CHUNK | GPTService | DebugService | LLMResponsePayload | Streaming GPT chunk |
| SPEECH_GENERATION_REQUEST | ElevenLabsService | ElevenLabsService | SpeechGenerationRequestPayload | Request TTS generation |
| SPEECH_SYNTHESIS_STARTED | ElevenLabsService | EyeLightControllerService, MusicControllerService | BaseEventPayload | TTS playback starting |
| SPEECH_SYNTHESIS_ENDED | ElevenLabsService | EyeLightControllerService, MusicControllerService | BaseEventPayload | TTS playback completed |

### 3.3 Hardware Control Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| EYE_COMMAND | CommandDispatcherService, GPTService | EyeLightControllerService | EyeCommandPayload | LED eye control |
| LED_COMMAND_SUCCESS | EyeLightControllerService | CLIService | BaseEventPayload | LED command success |
| LED_COMMAND_FAILURE | EyeLightControllerService | CLIService | BaseEventPayload | LED command failure |
| MUSIC_COMMAND | CommandDispatcherService, GPTService | MusicControllerService | MusicCommandPayload | Music control |
| AUDIO_DUCKING_START | ElevenLabsService | MusicControllerService | BaseEventPayload | Reduce music volume |
| AUDIO_DUCKING_STOP | ElevenLabsService | MusicControllerService | BaseEventPayload | Restore music volume |

### 3.3.1 Memory Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| MEMORY_GET | BrainService, Other services | MemoryService | MemoryRequestPayload | Request memory value |
| MEMORY_SET | BrainService, Other services | MemoryService | MemorySetPayload | Set memory value |
| MEMORY_VALUE | MemoryService | BrainService, Other services | MemoryValuePayload | Response to memory request |
| MEMORY_UPDATED | MemoryService | BrainService, Other services | MemoryUpdatePayload | Notify of memory changes |

### 3.3.2 DJ Mode Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| DJ_MODE_CHANGED | BrainService, CommandDispatcherService | BrainService, MusicControllerService, MemoryService | DJModeChangedPayload | Notify of DJ mode state changes |

### 3.4 Command and Control Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| CLI_COMMAND | CLIService, WebBridgeService | CommandDispatcherService | StandardCommandPayload | User command input |
| CLI_RESPONSE | Various services | CLIService | CliResponsePayload | Command response |
| SERVICE_STATUS_UPDATE | All services | DebugService, WebBridgeService, LoggingService | ServiceStatusPayload | Service health status |
| DEBUG_LOG | All services | DebugService | DebugLogPayload | System logging |
| DASHBOARD_LOG | LoggingService | WebBridgeService | DashboardLogPayload | Structured logs for web dashboard |
| SYSTEM_SHUTDOWN_REQUESTED | CommandDispatcherService | CantinaOS | BaseEventPayload | Request system shutdown/restart |

### 3.5 Web Dashboard Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| VOICE_COMMAND | WebBridgeService | YodaModeManagerService (via SYSTEM_SET_MODE_REQUEST) | VoiceCommandSchema (validated) | Voice control from dashboard |
| MUSIC_COMMAND | WebBridgeService | MusicControllerService | MusicCommandSchema (validated) | Music control from dashboard |
| DJ_COMMAND | WebBridgeService | BrainService | DJCommandSchema (validated) | DJ mode control from dashboard |
| SYSTEM_COMMAND | WebBridgeService | YodaModeManagerService | SystemCommandSchema (validated) | System mode changes from dashboard |

### 3.6 Pydantic Validation Events

| Event Topic | Publishers | Subscribers | Payload Structure | Purpose |
|-------------|------------|-------------|-------------------|---------|
| VALIDATION_ERROR | WebBridgeService | LoggingService, DebugService | WebCommandError | Command validation failures |
| COMMAND_VALIDATED | WebBridgeService | Various services | BaseWebResponse | Successful command validation |

## 4. System Flow Diagrams

### 4.1 Voice Interaction Pipeline

```
[Audio Capture] → [Transcription] → [Natural Language Processing] → [Text-to-Speech] → [Audio Output]
```

1. **Audio Capture**: 
   - DeepgramDirectMicService listens for audio input
   - Triggered by VOICE_LISTENING_STARTED event
   - EyeLightControllerService shows "listening" pattern

2. **Transcription**:
   - DeepgramDirectMicService streams audio to Deepgram API
   - Interim results emitted as TRANSCRIPTION_INTERIM
   - Final result emitted as TRANSCRIPTION_FINAL
   - EyeLightControllerService transitions to "thinking" pattern

3. **Natural Language Processing**:
   - GPTService receives TRANSCRIPTION_FINAL
   - Sends text to OpenAI API with conversation context
   - Emits LLM_RESPONSE with processing result
   - Can detect commands and emit specific command events

4. **Text-to-Speech**:
   - ElevenLabsService receives LLM_RESPONSE
   - Generates speech audio from text
   - Emits SPEECH_SYNTHESIS_STARTED
   - EyeLightControllerService shows "speaking" pattern
   - MusicControllerService lowers music volume

5. **Audio Output**:
   - ElevenLabsService plays audio
   - Emits SPEECH_SYNTHESIS_ENDED when complete
   - EyeLightControllerService returns to previous pattern
   - MusicControllerService restores music volume

### 4.2 Mode Transition Flow

```
[STARTUP] → [IDLE] → [AMBIENT] → [INTERACTIVE]
```

1. **STARTUP Mode**:
   - System initializes all services
   - EyeLightControllerService shows "startup" pattern
   - YodaModeManagerService emits SYSTEM_MODE_CHANGE to IDLE

2. **IDLE Mode**:
   - System in low-power listening state
   - EyeLightControllerService shows "idle" pattern
   - Listening for activation commands

3. **AMBIENT Mode**:
   - Background music playing
   - EyeLightControllerService shows ambient patterns
   - Responding to basic commands

4. **INTERACTIVE Mode**:
   - Full conversation capabilities
   - Actively monitoring for voice input
   - All services at full functionality

### 4.3 Unified Command Processing Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ CLI Command │────▶│   Command   │────▶│  Timeline   │────▶│    Music    │
└─────────────┘     │ Dispatcher  │     │  Executor   │     │ Controller  │
                    └─────────────┘     └─────────────┘     └─────────────┘
┌─────────────┐     ┌─────────────┐            ▲            
│    Voice    │────▶│    Brain    │────────────┘            
└─────────────┘     └─────────────┘                         
                           ▲                                
┌─────────────┐           │                                 
│   DJ Mode   │───────────┘                                 
└─────────────┘                                             
```

The system implements a consistent three-tier architecture for all music commands:

1. **Command Entry**: Commands can originate from three sources:
   - CLI commands entered by the user
   - Voice commands processed by the speech recognition system
   - DJ Mode automatic music selection and transitions

2. **Command Transformation & Routing**:
   - CommandDispatcherService transforms commands into standardized payloads
   - Uses service-specific payload transformation for consistent handling
   - Maintains consistent data structures across different entry points

3. **Plan-Based Execution**:
   - ALL music commands flow through TimelineExecutorService
   - Commands are converted to PlanPayload objects with explicit steps
   - Ensures consistent audio ducking and cross-fading

4. **Command Flow Process**:
   - User or system initiates command (CLI, voice, or DJ mode)
   - Command is routed to appropriate service with transformed payload
   - Service creates a plan with specific steps
   - Plan is executed by TimelineExecutor
   - Music Controller handles final playback

This unified architecture ensures:
- Consistent command handling regardless of source
- Proper audio coordination and ducking
- Clear separation of responsibilities
- Standardized data structures throughout the system

Previous CLI-direct paths have been deprecated in favor of this unified approach.

### 4.4 Music and Audio Ducking Flow

```
[Speech Request] → [Lower Music] → [Play Speech] → [Restore Music]
```

1. ElevenLabsService receives speech generation request
2. ElevenLabsService emits AUDIO_DUCKING_START
3. MusicControllerService reduces volume
4. ElevenLabsService plays speech audio
5. ElevenLabsService emits AUDIO_DUCKING_STOP
6. MusicControllerService restores original volume

### 4.5 LED Visual Feedback Flow

```
[System Event] → [Pattern Selection] → [LED Command] → [Arduino Communication]
```

1. EyeLightControllerService subscribes to various system events
2. Events trigger appropriate pattern selection
3. EyeLightControllerService sends commands to Arduino
4. LED pattern displays visual feedback for current system state

### 4.6 Common Failure Points and Solutions

**1. Service Initialization Race Conditions**
- **Issue**: BrainService failing with "MEMORY_VALUE error" 
- **Solution**: Always await event subscriptions before emitting requests

**2. Multi-Word Command Parsing**
- **Issue**: "list music" parsed incorrectly as command="list", args=["music"]
- **Solution**: CommandDispatcher reconstructs multi-word commands

**3. Import Path Errors**
- **Issue**: "No module named 'event_topics'" errors
- **Solution**: Always use full paths: `from core.event_topics import EventTopics`

**4. Web Dashboard Integration Failures**
- **Issue**: Dashboard bypassing CantinaOS event system or using wrong event topics
- **Solution**: Follow WEB_DASHBOARD_STANDARDS.md for proper event topic translation and service compliance

**5. Pydantic Validation Errors**
- **Issue**: Commands failing with validation errors or JSON serialization issues
- **Solution**: Use proper validation mixins and `model_dump(mode='json')` for datetime serialization

**6. Socket.IO Handler Signature Mismatches**
- **Issue**: Validation decorators failing due to incorrect method signatures
- **Solution**: Ensure handlers use proper instance method signature with self parameter


## 5. Service Details

### 5.1 DeepgramDirectMicService

**Initialization Requirements**:
- Deepgram API key
- Audio device configuration
- Transcription model settings

**Key Methods**:
- `_start_listening()`: Begins audio capture and streaming
- `_stop_listening()`: Stops audio capture
- `_on_transcript()`: Processes transcription results
- `_setup_deepgram_handlers()`: Configures API callbacks

**Error Handling**:
- Connection retry mechanism for API failures
- Graceful degradation if microphone is unavailable
- Error reporting via TRANSCRIPTION_ERROR events

**Thread Management**:
- Uses asyncio for non-blocking operations
- Bridges Deepgram callbacks to event system
- Manages background metrics collection task

**Resource Cleanup**:
- Stops microphone on service shutdown
- Closes Deepgram connection
- Cancels all pending tasks

### 5.2 GPTService

**Initialization Requirements**:
- OpenAI API key
- Model configuration
- System prompt (personality)
- Conversation memory settings

**Key Methods**:
- `_process_with_gpt()`: Sends text to OpenAI API
- `_stream_gpt_response()`: Handles streaming responses
- `_emit_llm_response()`: Publishes processed responses
- `register_tool()`: Registers tool capabilities

**Error Handling**:
- API timeout and retry mechanisms
- Rate limiting protection
- Error reporting with fallback responses

**Thread Management**:
- Asynchronous API communication
- Request tracking and throttling
- Response parsing in background tasks

**Resource Cleanup**:
- Closes API sessions
- Saves conversation context if needed
- Cancels pending requests

### 5.3 ElevenLabsService

**Initialization Requirements**:
- ElevenLabs API key
- Voice ID configuration
- Audio playback settings

**Key Methods**:
- `_generate_speech()`: Sends text to ElevenLabs API
- `_play_audio()`: Handles audio playback
- `_handle_llm_response()`: Processes incoming text
- `_emit_amplitude()`: Reports speech amplitude

**Error Handling**:
- API error detection and reporting
- Cached audio fallbacks
- Generation timeout protection

**Thread Management**:
- Separate threads for audio playback
- Asynchronous API communication
- Background amplitude monitoring

**Resource Cleanup**:
- Stops audio playback
- Closes API connections
- Deletes temporary audio files

### 5.4 EyeLightControllerService

**Initialization Requirements**:
- Serial port configuration
- Arduino connection parameters
- Pattern definitions

**Key Methods**:
- `set_pattern()`: Sets LED pattern on hardware
- `set_brightness()`: Controls LED brightness
- `_handle_eye_command()`: Processes eye commands
- `_auto_detect_arduino()`: Finds hardware connection

**Error Handling**:
- Connection retry mechanism
- Mock mode fallback if hardware unavailable
- Command timeout protection

**Thread Management**:
- Async serial communication
- Background pattern scheduling
- Pattern duration timers

**Resource Cleanup**:
- Resets LED hardware to default state
- Closes serial connection
- Cancels pattern timers

### 5.5 MusicControllerService

**Initialization Requirements**:
- Music library path
- Audio device configuration
- Playback settings

**Key Methods**:
- `play_music()`: Starts music playback
- `stop_music()`: Stops current playback
- `set_volume()`: Adjusts playback volume
- `_handle_music_command()`: Processes music commands

**Error Handling**:
- File not found handling
- Audio device fallbacks
- Playback error recovery

**Thread Management**:
- Background playback thread
- Volume fade background tasks
- Music selection worker

**Resource Cleanup**:
- Stops all playback
- Releases audio device
- Saves current state

## 6. Integration Points

### 6.1 External API Integrations

**Deepgram API**:
- Used for real-time speech-to-text
- WebSocket streaming protocol
- Authentication via API key
- Handled by DeepgramDirectMicService

**OpenAI API**:
- Used for natural language processing
- REST API with streaming support
- Authentication via API key
- Handled by GPTService

**ElevenLabs API**:
- Used for text-to-speech generation
- REST API with audio streaming
- Authentication via API key
- Handled by ElevenLabsService

### 6.2 Hardware Interfaces

**Arduino Interface**:
- Serial communication protocol
- JSON-based command format
- Managed by EyeLightControllerService
- Controls LED patterns and animations

**Audio Devices**:
- Input: Microphone capture via PyAudio/Deepgram
- Output: Audio playback via PyAudio
- Managed by DeepgramDirectMicService and ElevenLabsService

### 6.3 User Interfaces

**Command Line Interface**:
- Interactive terminal commands
- Custom command parser
- Help system and auto-completion
- Managed by CLIService

**Mouse Input**:
- Click detection for recording activation
- Managed by MouseInputService
- Translates clicks to voice recording events

**Web Dashboard Interface**:
- Real-time web-based monitoring and control
- Next.js frontend with React components
- Socket.io/WebSocket communication via FastAPI bridge
- Managed by WebBridgeService (backend) and dj-r3x-dashboard (frontend)
- Must follow WEB_DASHBOARD_STANDARDS.md for proper integration
- Requires proper event topic translation and service compliance

## 7. Web Dashboard Architecture

### 7.1 Dashboard Component Overview

The DJ R3X Web Dashboard provides real-time monitoring and control capabilities through a modern web interface that connects to CantinaOS via the WebBridgeService.

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Next.js Web   │◄──►│  WebBridge      │◄──►│   CantinaOS     │
│   Dashboard     │    │  Service        │    │   Event Bus     │
│  (Port 3000)    │    │  (Port 8000)    │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### 7.2 WebBridgeService Architecture

**Purpose**: Bridge between web dashboard and CantinaOS event system
**Location**: `cantina_os/cantina_os/services/web_bridge_service.py`
**Port**: 8000 (FastAPI + Socket.IO)

**Key Components**:
- **FastAPI App**: REST API endpoints for system status and music library
- **Socket.IO Server**: Real-time bidirectional communication
- **Event Bridge**: Translates between web commands and CantinaOS events
- **Client Management**: Tracks connected dashboard clients
- **Event Filtering**: Throttles high-frequency events to prevent spam

**Critical Event Handling**:
- Subscribes to all major CantinaOS events for dashboard updates
- Translates web dashboard commands to proper CantinaOS event topics using Pydantic validation
- Provides real-time service status updates with validated payloads
- Streams system logs via LoggingService integration
- Implements comprehensive command validation pipeline

### 7.3 Dashboard Frontend Architecture

**Technology Stack**: Next.js 13+ with TypeScript, Tailwind CSS, Socket.io-client
**Location**: `dj-r3x-dashboard/src/`
**Port**: 3000 (Next.js dev server)

**Component Structure**:
```
src/
├── app/                 # Next.js 13 app router
├── components/
│   ├── tabs/           # Main dashboard tabs
│   │   ├── MonitorTab.tsx    # System monitoring
│   │   ├── VoiceTab.tsx      # Voice controls
│   │   ├── MusicTab.tsx      # Music playback
│   │   ├── DJTab.tsx         # DJ mode
│   │   ├── ShowTab.tsx       # Performance view
│   │   └── SystemTab.tsx     # System controls
│   └── show/           # Performance/show components
├── contexts/           # React contexts
├── hooks/             # Custom React hooks
└── types/             # TypeScript definitions
```

**Key Features**:
- **Real-time Status**: Live service monitoring and system state
- **Voice Control**: Web-based voice recording and transcription display
- **Music Management**: Playback controls and library browsing
- **DJ Mode**: Automated music mixing and transitions
- **System Control**: Mode switching and configuration
- **Star Wars Theme**: Custom holographic terminal aesthetic

### 7.4 Communication Flow

**Dashboard → CantinaOS** (with Pydantic Validation):
1. User interacts with dashboard component
2. Frontend emits Socket.IO event to WebBridgeService
3. WebBridgeService validates command using Pydantic schemas
4. WebBridgeService translates validated command to appropriate CantinaOS event
5. CantinaOS processes event and responds

**CantinaOS → Dashboard** (with Status Validation):
1. CantinaOS service emits event to event bus
2. WebBridgeService receives event (via subscription)
3. WebBridgeService validates status payload using StatusPayloadValidationMixin
4. WebBridgeService broadcasts validated payload to all connected dashboard clients
5. Dashboard updates UI in real-time with type-safe data

**Validation Pipeline**:
- **Schema Validation**: All commands validated against Pydantic models
- **Field Mapping**: CantinaOS compatibility transformations
- **Error Handling**: Standardized error responses with fallback mechanisms
- **JSON Serialization**: Proper datetime handling with `model_dump(mode='json')`

### 7.5 LoggingService Integration

**Purpose**: Centralized logging with dashboard streaming
**Location**: `cantina_os/cantina_os/services/logging_service/logging_service.py`

**Architecture**:
- **Log Capture**: Custom Python logging handler captures all system logs
- **Structured Format**: Converts logs to structured format with service identification
- **Deduplication**: Smart filtering to prevent log flooding
- **File Persistence**: Session-based log files for debugging
- **Dashboard Streaming**: Real-time log events via DASHBOARD_LOG events
- **Queue Processing**: Async batch processing for performance

**Integration Points**:
- Captures logs from all CantinaOS services
- Emits DASHBOARD_LOG events consumed by WebBridgeService
- Provides searchable, filterable log view in dashboard
- Maintains session-based log files for persistence

### 7.6 Deployment Architecture

**Development Setup**:
```bash
# Terminal 1: Start CantinaOS + WebBridge
cd cantina_os && python -m cantina_os.main

# Terminal 2: Start Dashboard Frontend  
cd dj-r3x-dashboard && npm run dev
```

**Production Setup**:
```bash
# Single command starts both systems
./start-dashboard.sh
```

**Service Dependencies**:
1. CantinaOS starts all core services including WebBridgeService
2. WebBridgeService starts FastAPI server on port 8000
3. Dashboard frontend connects to WebBridge via Socket.IO
4. All communication flows through the event bus architecture

## 8. Architecture Patterns

### 8.1 BaseService Pattern

All services inherit from the BaseService class, which provides:
- Standardized lifecycle management (start/stop)
- Event bus integration
- Contextual logging
- Service status reporting
- Error handling with fallback capabilities

```python
class BaseService:
    async def start(self) -> None:
        # Common initialization
        await self._start()  # Service-specific initialization
        # Report status
        
    async def stop(self) -> None:
        # Common cleanup
        await self._stop()  # Service-specific cleanup
        # Report status
```

### 8.2 Event Subscription Patterns

Services use a consistent pattern for event subscription:
- Subscribe during initialization
- Use async handlers
- Include error handling
- Unsubscribe during shutdown

```python
async def _setup_subscriptions(self) -> None:
    await self.subscribe(
        EventTopics.SOME_EVENT,
        self._handle_some_event
    )
```

### 8.3 Error Handling Strategies

The system employs several error handling strategies:
- **Service Level**: Each service handles its own errors
- **Event Bus Level**: The event bus protects against handler errors
- **System Level**: The main application monitors service health
- **Graceful Degradation**: Services fall back to reduced functionality
- **Retry Logic**: Critical operations include retry mechanisms
- **Error Reporting**: Standardized error event format

### 8.4 Thread-to-asyncio Bridging Patterns

Several services bridge between threaded libraries and asyncio:
- Queue-based communication between threads and asyncio
- Thread pools for blocking operations
- Future/Promise patterns for async results
- Background tasks for long-running operations
- Event loop protection for thread safety

### 8.5 Command Processing Patterns

The system implements a standardized command processing architecture:

- **Standardized Command Payload**: All commands use the StandardCommandPayload format with proper validation
- **Command Registration**: Services register commands declaratively with the CommandDispatcherService
- **Compound Commands**: Support for hierarchical commands (e.g., "eye pattern happy")
- **Command Shortcuts**: Aliasing common commands for ease of use
- **Consistent Error Handling**: Standardized validation and error reporting
- **Clear Responsibility Boundaries**:
  - CLIService: User interaction only
  - CommandDispatcherService: Command routing and validation
  - Service handlers: Domain-specific business logic

This architecture ensures consistent command handling across all services while maintaining clear separation of concerns.

## 9. Conclusion

The CantinaOS architecture provides a flexible, extensible foundation for the DJ R3X voice application. Its event-driven design enables clean separation of concerns while maintaining the rich interactions needed for a responsive voice assistant. The system's modular nature allows for easy addition of new features and capabilities while ensuring reliable operation through comprehensive error handling and resource management.
