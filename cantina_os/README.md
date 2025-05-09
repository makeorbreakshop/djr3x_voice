# CantinaOS Implementation

## Overview

CantinaOS is a robust, event-driven system architecture for DJ R3X, implementing ROS-inspired design principles for enhanced scalability, maintainability, and testability. This implementation provides a solid foundation for both current functionality and future expansion.

## Key Features

- **Event-Driven Architecture**: All inter-service communication happens through a centralized event bus using standardized topics and payloads
- **Decoupled Services**: Each component operates independently, communicating only through events
- **Type Safety**: Pydantic models ensure type safety and validation for all event payloads
- **Robust Error Handling**: Comprehensive error handling and status reporting across all services
- **Standardized Logging**: Contextual logging with consistent formatting and severity levels
- **Health Monitoring**: Built-in service health monitoring and status reporting
- **Extensible Design**: Easy to add new services and capabilities while maintaining system stability

## Implementation Status

This is a new implementation following the architecture described in `docs/CantinaOS-Integration-Plan.md`.

For current implementation status and upcoming tasks, see:
- [TODO.md](./TODO.md) - Detailed implementation tasks and progress
- [docs/dj-r3x-dev-log.md](../docs/dj-r3x-dev-log.md) - Development log with updates

## Project Structure

```
cantina_os/
├── cantina_os/
│   ├── __init__.py
│   ├── main.py               # Application entry point
│   ├── event_topics.py       # Hierarchical event topic definitions
│   ├── event_payloads.py     # Pydantic models for event payloads
│   ├── event_bus.py          # Event bus implementation
│   ├── base_service.py       # Base class for all services
│   └── services/             # Individual service implementations
├── tests/                    # Test suite
├── docs/                     # Documentation
├── requirements.txt          # Project dependencies
└── README.md                 # This file
```

## System Modes

CantinaOS operates in three primary modes:

- **IDLE Mode**: Default state where the system is powered but not actively engaging with users
- **AMBIENT Mode**: Background show mode where DJ R3X performs ambient animations
- **INTERACTIVE Mode**: Full interaction mode where voice commands and conversation are enabled

## CLI Interaction

The system provides a command-line interface for control and testing:

### CLI Commands

- **System Control Commands**:
  - `engage` (shortcut: `e`) - Switch to INTERACTIVE mode
  - `disengage` (shortcut: `d`) - Return to IDLE mode
  - `ambient` (shortcut: `a`) - Switch to AMBIENT mode
  - `status` (shortcut: `s`) - Display current system status
  - `reset` (shortcut: `r`) - Reset system to IDLE mode
  - `quit` (shortcuts: `q` or `exit`) - Exit the program

- **Music Control Commands**:
  - `list music` (shortcut: `l`) - List available music
  - `play music <n>` - Play specified music
  - `stop music` - Stop music playback
  
- **Help Command**:
  - `help` (shortcut: `h`) - Display available commands

### Running the CLI

To run the CantinaOS with CLI access:
```bash
python -m cantina_os.main
```

For testing command injection without CLI input:
```bash
python cli_test.py
```

## Voice Interaction

Voice interaction is primarily managed through the system mode transitions and voice processing pipeline:

1. **Voice Processing Pipeline**:
   - Audio capture through `MicInputService`
   - Speech recognition via `DeepgramTranscriptionService`
   - Natural language processing in `GPTService`
   - Response generation using `ElevenLabsService`
   - Eye/LED animations from `EyeLightControllerService`

2. **Mode-Based Behavior**:
   - In IDLE mode: Voice processing is disabled
   - In AMBIENT mode: Limited voice commands are processed
   - In INTERACTIVE mode: Full conversation and command capability

3. **Voice Commands**:
   - Commands are processed through the same `CommandDispatcherService` as CLI commands
   - Voice commands are converted to text and routed to appropriate handlers

## Core Services

- **MicInputService**: Raw audio capture from microphone
- **DeepgramTranscriptionService**: Real-time streaming ASR via Deepgram
- **GPTService**: LLM interactions and conversation management
- **ElevenLabsService**: Text-to-speech synthesis
- **EyeLightControllerService**: LED animation control
- **MusicControllerService**: Music playback and ducking
- **YodaModeManagerService**: System mode management
- **CLIService**: Command-line interface
- **CommandDispatcherService**: Routes commands to appropriate handlers
- **ModeCommandHandlerService**: Handles mode-specific commands
- **ModeChangeSoundService**: Audio feedback for mode changes

## Setup Instructions

1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Copy the example environment file and configure:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and configuration
   ```

4. Configure environment variables in `.env`:
   - `DEEPGRAM_API_KEY`: API key for Deepgram speech recognition
   - `OPENAI_API_KEY`: API key for OpenAI GPT models
   - `ELEVENLABS_API_KEY`: API key for ElevenLabs voice synthesis
   - `AUDIO_SAMPLE_RATE`: Sample rate for audio input (default: 16000)
   - `AUDIO_CHANNELS`: Number of audio channels (default: 1)
   - `GPT_MODEL`: GPT model to use (default: gpt-4o)
   - `ELEVENLABS_VOICE_ID`: Voice ID for synthesis (default: 21m00Tcm4TlvDq8ikWAM)
   - `MOCK_LED_CONTROLLER`: Whether to use mock LED controller (true/false)
   - `ARDUINO_SERIAL_PORT`: Serial port for Arduino LED controller
   - `AUDIO_PLAYBACK_METHOD`: Method for audio playback (auto/sounddevice/system)

5. Run tests to verify setup:
   ```bash
   pytest
   ```

6. Start the system:
   ```bash
   python -m cantina_os.main
   ```

## Event System

The system uses a hierarchical event topic structure for communication:
- `/system/*` - System management events
- `/service/*` - Service status and lifecycle events
- `/cli/*` - CLI events
- `/mode/*` - Mode management events
- `/voice/*` - Voice processing events
- `/music/*` - Music control events
- `/led/*` - LED control events
- `/audio/*` - Audio processing events
- `/speech/*` - Speech synthesis events
- `/llm/*` - Language model events
- `/tools/*` - Tool execution events
- `/transcription/*` - Speech recognition events

## Development Guidelines

1. **Code Style**:
   - Use Black for code formatting
   - Use isort for import sorting
   - Use mypy for type checking
   - Use ruff for linting

2. **Event Handling**:
   - All inter-service communication must use the event bus
   - Use appropriate event topics from `event_topics.py`
   - Use typed payloads from `event_payloads.py`
   - Handle errors gracefully and emit appropriate status events

3. **Service Development**:
   - Inherit from `BaseService`
   - Implement `_initialize()` and `_cleanup()` methods
   - Set up event subscriptions in `_setup_subscriptions()`
   - Use contextual logging via `self.logger`
   - Emit status updates using `_emit_status()`

4. **Testing**:
   - Write unit tests for all new services
   - Use pytest fixtures for common setup
   - Mock external services and hardware
   - Test error handling and edge cases

## License

[Insert License Information] 