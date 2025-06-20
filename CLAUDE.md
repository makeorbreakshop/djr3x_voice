# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: Development Workflow

**ALWAYS follow this workflow for every task:**

### 1. EXPLORE First
- Read relevant files before making changes
- Search the codebase for similar patterns
- Understand existing conventions and architecture
- **For CantinaOS services**: ALWAYS consult architecture documents first (see CantinaOS Service Development section)
- Check test coverage for the area you're modifying

### 2. PLAN Before Coding  
- Create a detailed plan of attack
- Break complex tasks into smaller steps
- Identify which files need changes
- Consider edge cases and error handling
- Use TodoWrite tool for multi-step tasks

### 3. CODE Iteratively
- Follow existing code conventions exactly
- Write tests first when adding new features (TDD)
- Make small, incremental changes
- Verify each change works before proceeding
- Run linters and type checkers after changes

### 4. VERIFY Your Work
- Run all relevant tests
- Test the feature manually if applicable
- Check for regressions in related functionality
- Ensure code follows project standards

## Common Commands

### Setup and Installation
```bash
# Setup portable dj-r3x command
./setup-dj-r3x-command.sh

# CantinaOS development setup
cd cantina_os
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt

# Dashboard development setup
cd dj-r3x-dashboard
npm install

# Bridge setup
cd dj-r3x-bridge
pip install -r requirements.txt
```

### Running the Application
```bash
# Recommended: Full System with Dashboard
./start-dashboard.sh            # Start CantinaOS + Web Dashboard
./check-dashboard-health.sh     # Verify all services connected
./stop-dashboard.sh             # Stop all services

# CantinaOS Only
dj-r3x                          # Via installer
cd cantina_os && python -m cantina_os.main  # Direct

# Run in test mode (no API keys required)
cd cantina_os && python -m cantina_os.main --test

# Run with demo mode
cd cantina_os && python -m cantina_os.main --demo

# Legacy version
python3 run_rex.py
```

### Testing
```bash
# CantinaOS (comprehensive test suite)
cd cantina_os
pytest                           # All tests
pytest tests/unit/              # Unit tests only
pytest tests/integration/       # Integration tests
pytest --cov=cantina_os         # With coverage
pytest -m performance           # Performance tests
pytest --last-failed            # Re-run failed tests

# Dashboard (React/TypeScript test suite)
cd dj-r3x-dashboard
npm run test                     # All tests with Vitest
npm run test:watch               # Watch mode for development
npm run test:coverage            # Coverage reports
npm run test:ui                  # Visual test interface

# Legacy
pytest                          # Root level tests
```

### Development Tools
```bash
# Code quality (CantinaOS)
cd cantina_os
black cantina_os/               # Format code
isort cantina_os/               # Sort imports
ruff cantina_os/                # Lint
mypy cantina_os/                # Type check

# Dashboard development
cd dj-r3x-dashboard
npm run dev                     # Start development server
npm run build                   # Build for production
npm run lint                    # Lint TypeScript/React
npm run schemas:generate        # Generate TypeScript types from Pydantic
npm run schemas:watch          # Watch mode for schema generation

# Bridge standalone
cd dj-r3x-bridge
python main.py                  # Runs on http://127.0.0.1:8000
```

## Architecture Overview

### Project Structure
```
dj-r3x-voice/
├── cantina_os/              # Modern event-driven voice assistant
│   ├── cantina_os/         # Main package
│   │   ├── core/          # Event bus, topics, payloads
│   │   ├── services/      # Microservices (voice, music, LED, etc.)
│   │   ├── models/        # Shared data models
│   │   └── main.py        # Entry point
│   ├── tests/             # Unit and integration tests
│   └── docs/              # Architecture documentation
├── dj-r3x-dashboard/        # Next.js web dashboard
│   ├── components/        # React components
│   ├── hooks/            # Custom React hooks
│   └── types/            # TypeScript definitions
├── dj-r3x-bridge/          # FastAPI WebSocket bridge
│   └── main.py           # Bridge service
├── arduino/                # LED control firmware
│   └── rex_eyes/         # Arduino sketch
└── docs/                   # Project documentation
    └── working_logs/     # Daily development logs
```

### CantinaOS (Modern Implementation)
**Location**: `cantina_os/cantina_os/`
**Pattern**: ROS-inspired event-driven microservices
**Entry Point**: `cantina_os/main.py`

#### Core Components
- **Event Bus**: `core/event_bus.py` - Hierarchical pub/sub system
- **Services**: `services/` - 15+ microservices inheriting from `BaseService`
- **Event Topics**: `core/event_topics.py` - Organized as `/system/*`, `/voice/*`, `/music/*`
- **Payloads**: `core/event_payloads.py` - Pydantic models for type safety

#### Key Services
- `deepgram_direct_mic_service.py` - Voice input via Deepgram
- `gpt_service.py` - LLM responses via OpenAI
- `elevenlabs_service.py` - Speech synthesis 
- `music_controller_service.py` - Background music with ducking
- `eye_light_controller_service.py` - Arduino LED control
- `brain_service.py` - Conversation orchestration
- `dj_mode_service.py` - DJ commentary and transitions

#### Service Creation Pattern
Services inherit from `BaseService` and implement:
```python
class MyService(BaseService):
    async def _start(self) -> None:
        # Subscribe to events (override _start, not start)
        self.event_bus.subscribe('/topic', self.handle_event)
    
    async def _stop(self) -> None:
        # Clean up resources (override _stop, not stop)
        pass
    
    async def handle_event(self, payload: EventPayload) -> None:
        # Process event and optionally publish response
        pass
```

### Web Dashboard Architecture
**Location**: `dj-r3x-dashboard/` (Next.js frontend) + `dj-r3x-bridge/` (FastAPI backend)
**Pattern**: Real-time web interface with Socket.io bridge to CantinaOS
**Entry Point**: `./start-dashboard.sh`

#### Dashboard Components
- **Next.js Frontend**: React-based dashboard with 6 tabs (Monitor, Voice, Music, DJ Mode, Show, System)
- **FastAPI Bridge**: WebSocket bridge service connecting web frontend to CantinaOS event bus
- **Socket.io**: Real-time bidirectional communication for live monitoring and control
- **Star Wars UI**: Custom Tailwind theme with holographic terminal aesthetic
- **Pydantic Integration**: Auto-generated TypeScript schemas from Python models for type safety

#### Key Features
- Real-time service status monitoring
- Voice transcription and response display
- Music library browser with playback controls
- DJ mode controls with transition monitoring
- System logs and performance metrics

## CantinaOS Service Development Guidelines

**CRITICAL: When creating, modifying, or working with CantinaOS services, you MUST consult these architecture documents in order:**

### Required Reading Order
1. **`cantina_os/docs/CANTINA_OS_SYSTEM_ARCHITECTURE.md`** - Complete system overview, event-driven architecture, service registry, event bus topology, and integration patterns
2. **`cantina_os/docs/ARCHITECTURE_STANDARDS.md`** - Detailed standards for service structure, event handling, error handling, async programming, configuration management, and comprehensive checklists
3. **`cantina_os/docs/SERVICE_CREATION_GUIDELINES.md`** - Step-by-step service creation process, common pitfalls, example implementations, and final verification steps

### Why This Matters
- **Event System Consistency**: All services must follow the same event bus patterns and payload structures
- **Error Prevention**: The architecture documents contain lessons learned from previous service integration failures
- **Standards Compliance**: Services must inherit from BaseService, implement proper lifecycle methods, and follow naming conventions
- **Integration Success**: Proper event topic usage, subscription patterns, and status reporting are essential for dashboard connectivity

### When to Consult These Documents
- **Before** creating any new service
- **Before** modifying existing service event handling  
- **Before** adding new event topics or payloads
- **When** debugging service startup or communication issues
- **When** implementing hardware integration or threading
- **When** working on command registration or CLI integration

### Quick Reference Checklist
- [ ] Does my service inherit from BaseService?
- [ ] Am I using EventTopics enum for all event names?
- [ ] Am I emitting SERVICE_STATUS_UPDATE events properly?
- [ ] Are my event handlers using proper error handling?
- [ ] Am I following the async/await patterns correctly?
- [ ] Does my service clean up resources in _stop()?
- [ ] Am I overriding _start() and _stop() methods (not start/stop)?
- [ ] Are my Pydantic payloads properly defined for web dashboard integration?

**Failure to follow these architecture documents leads to integration failures, service connection issues, and dashboard problems.**

## Hardware Integration

### Arduino LED Control
- **Firmware**: `arduino/rex_eyes/rex_eyes.ino`
- **Protocol**: Serial communication at 115200 baud
- **Hardware**: Arduino Mega 2560 + 2x MAX7219 LED matrices
- **Control**: Both architectures use same serial protocol

### Audio Setup
- **Input**: System microphone via Deepgram/Whisper
- **Output**: System speakers via VLC/sounddevice
- **Music**: VLC required for background playback with ducking

## Environment Variables

Create a `.env` file in the root directory:
```bash
# API Keys
OPENAI_API_KEY=your_openai_api_key
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID=your_elevenlabs_voice_id_here

# OpenAI Model Configuration
OPENAI_MODEL=gpt-4o

# Operation Modes
TEXT_ONLY_MODE=false
DISABLE_AUDIO_PROCESSING=false

# Arduino Configuration (optional)
LED_SERIAL_PORT=/dev/ttyACM0  # Your Arduino port
LED_BAUD_RATE=115200

# Personality Configuration
DJ_R3X_PERSONA="You are DJ R3X, a droid DJ from Star Wars..."
```

## Development Best Practices

### Test-Driven Development (TDD)
When adding new features:
1. Write failing tests first
2. Implement minimal code to pass tests
3. Refactor while keeping tests green
4. Add edge case tests

### Iteration Strategy
- Work against clear targets (tests, specs, or user requirements)
- Make small commits with clear messages
- Verify each step before moving forward
- Use `pytest --last-failed` to quickly re-run failed tests

### Code Exploration Guidelines
- Use Grep/Glob tools extensively before implementing
- Read neighboring files to understand patterns
- Check imports to understand dependencies
- Review similar components/services as examples

### Before You Start Checklist
- [ ] Have I explored the codebase for similar patterns?
- [ ] Do I understand the existing architecture?
- [ ] For CantinaOS services: Have I read the architecture documents?
- [ ] Have I created a clear plan?
- [ ] Are there existing tests I should follow?
- [ ] What commands will I use to verify my work?

### Additional Best Practices

#### Be Specific and Precise
- When asked to work on a feature, ask for clarification if needed
- Request specific file names or components when instructions are vague
- Use concrete examples from the codebase to confirm understanding

#### Leverage Existing Patterns
- NEVER assume a library is available - check package.json/requirements.txt first
- Always check for existing utility functions before writing new ones
- Follow the exact import patterns used in neighboring files
- Mimic the coding style of the surrounding code exactly

#### Clear Commit Workflow
When committing changes:
1. Review all changes with `git diff`
2. Write descriptive commit messages that explain WHY, not just WHAT
3. Reference issue numbers or feature requests when applicable
4. Keep commits focused - one logical change per commit

#### Visual References
- When provided screenshots or diagrams, refer to them throughout implementation
- Ask for visual clarification when working on UI components
- Test visual changes in the actual interface, not just unit tests

#### Safety and Security
- NEVER commit API keys, secrets, or credentials
- Always use environment variables for sensitive configuration
- Review security implications of any external API calls
- Be cautious with file system operations outside the project directory

## Common Patterns

### Event-Driven Communication
All CantinaOS components communicate via events. Never call services directly.

### Service Dependencies
Services are loosely coupled via events. No direct service-to-service calls.

### Error Handling
Services have isolated error handling with graceful degradation when dependencies fail.

### Resource Cleanup
All services must properly clean up resources in their `_stop()` method.

## Troubleshooting

### Common Issues
- **Service Won't Start**: Check `_start()` method override and proper event subscriptions
- **Dashboard Not Updating**: Verify Pydantic payload schemas match between Python and TypeScript
- **Event Bus Issues**: Ensure using EventTopics enum and proper topic hierarchies
- **Hardware Connection**: Check Arduino serial port and VLC installation for audio
- **React Hydration Errors**: Use client-side rendering for dynamic content with useEffect
- **API Errors**: Verify .env file has correct API keys

### Debug Commands
```bash
# Check if all services are running
./check-dashboard-health.sh

# View CantinaOS logs
cd cantina_os && python -m cantina_os.main --log-level=DEBUG

# Test individual services
cd cantina_os && python -m pytest tests/unit/test_[service_name].py -v

# Check bridge connection
curl http://127.0.0.1:8000/  # Should show bridge status

# Test ElevenLabs API connection
python3 test_elevenlabs_rest.py

# Test with predefined interactions
python3 run_r3x_mvp.py --demo
```

## Context Management and Task Tracking

### Use TodoWrite Tool Effectively
- **Always** use TodoWrite for tasks with 3+ steps
- Mark tasks as `in_progress` when starting work
- Update to `completed` immediately after finishing each task
- Break complex features into granular, trackable items
- This provides visibility into your work progress

### Managing Long Sessions
- Use `/clear` periodically to reset context when switching between unrelated tasks
- Focus on one feature area at a time
- Complete current work before starting new tasks
- Reference specific files rather than asking to "update the feature"

## Work Logging Requirements

**IMPORTANT**: After every major change or feature implementation, you MUST append the current day's dev log file at `docs/working_logs/today/daily_dev_log_YYYY-MM-DD.md` with:

1. **Goal**: What you were trying to accomplish
2. **Changes**: Specific changes made (files, features, etc.)
3. **Impact**: What this enables or improves
4. **Learning**: Key insights or technical discoveries
5. **Result**: Completion status (e.g., "Feature X - **FULLY COMPLETE** ✅")

This ensures proper documentation of development progress and helps maintain project context across sessions.

## Cursor Rules Integration

The project includes Cursor rules in `.cursor/rules/devllog.mdc` which emphasize:
- Always check `docs/dj-r3x-dev-log.md` for past investigations and decisions
- Avoid redundant solutions to known issues
- Update the dev log with new discoveries
- Maintain consistency with established patterns

## Development Priorities

1. **New Features**: Implement in CantinaOS using service pattern
2. **Bug Fixes**: Fix in both implementations if affecting production
3. **Testing**: Always add unit + integration tests for CantinaOS features
4. **Legacy Maintenance**: Only when specifically required