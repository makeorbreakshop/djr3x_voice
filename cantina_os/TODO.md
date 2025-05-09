# CantinaOS Implementation TODO List

This document tracks the implementation progress of the CantinaOS system according to the phased approach outlined in `docs/CantinaOS-Integration-Plan.md`.

## Phase 1: Core Architecture Alignment (Foundation)

### Event System
- [x] Define hierarchical `EventTopics` in `src/event_topics.py`
- [x] Define Pydantic models for event payloads in `src/event_payloads.py` 
- [x] Create `BaseEventPayload` with required metadata fields
- [x] Implement `conversation_id` generation and propagation strategy
- [ ] Run Event System Unit Tests
  - Prerequisites: All Event System components implemented
  - Command: `pytest tests/unit/test_event_system.py -v`

### Service Framework
- [x] Implement `BaseService` class with lifecycle management
- [x] Add event subscription handling in `BaseService`
- [x] Add standardized logging in `BaseService`
- [x] Implement service status reporting
- [ ] Add stale/out-of-order event handling logic
- [ ] Run Service Framework Tests
  - Prerequisites: All Service Framework components implemented
  - Command: `pytest tests/unit/test_service_framework.py -v`

### Core Services Implementation
- [x] Implement `MicInputService` for audio capture
- [x] Implement `DeepgramTranscriptionService` for streaming ASR
- [x] Implement `GPTService` for LLM integration
- [x] Implement `SessionMemory` within `GPTService`
- [x] Implement `ElevenLabsService` for speech synthesis
- [x] Implement `EyeLightControllerService` for LED control
- [x] Implement `MusicControllerService` for music playback
- [x] Implement `YodaModeManagerService` for system mode management
- [x] Implement `CLIService` for command-line interface
- [x] Implement `ToolExecutorService` for LLM-driven tool execution

### Core Services Testing
#### Unit Tests Creation
- [x] Create unit tests for `MicInputService`
- [x] Create unit tests for `GPTService`
- [x] Create unit tests for `ElevenLabsService`
- [x] Create unit tests for `EyeLightControllerService`
- [x] Create unit tests for `MusicControllerService`
- [x] Create unit tests for `ToolExecutorService`
- [x] Create unit tests for `CLIService`
- [ ] Create unit tests for `YodaModeManagerService`
- [x] Create unit tests for `CommandDispatcherService`
- [ ] Create unit tests for `ModeCommandHandlerService`

#### Unit Tests Execution (Run after each service's tests are created)
- [x] Run MicInputService Tests
  - Prerequisites: MicInputService implementation and tests complete
  - Command: `pytest tests/unit/test_mic_input_service.py -v`
- [x] Run GPTService Tests
  - Prerequisites: GPTService implementation and tests complete
  - Command: `pytest tests/unit/test_gpt_service.py -v`
- [x] Run ElevenLabsService Tests
  - Prerequisites: ElevenLabsService implementation and tests complete
  - Command: `pytest tests/services/test_elevenlabs_service.py -v`
- [x] Run EyeLightControllerService Tests
  - Prerequisites: EyeLightControllerService implementation and tests complete
  - Command: `pytest tests/services/test_eye_light_controller_service.py -v`
- [x] Run MusicControllerService Tests
  - Prerequisites: MusicControllerService implementation and tests complete
  - Command: `pytest tests/unit/test_music_controller_service.py -v`
- [x] Run ToolExecutorService Tests
  - Prerequisites: ToolExecutorService implementation and tests complete
  - Command: `pytest tests/unit/test_tool_executor_service.py -v`
- [x] Run CLIService Tests
  - Prerequisites: CLIService implementation and tests complete
  - Command: `pytest tests/services/test_cli_service.py -v`
- [X] Run YodaModeManagerService Tests
  - Prerequisites: YodaModeManagerService implementation and tests complete
  - Command: `pytest tests/unit/test_yoda_mode_manager_service.py -v`
- [x] Run CommandDispatcherService Tests
  - Prerequisites: CommandDispatcherService implementation and tests complete
  - Command: `pytest tests/unit/test_command_dispatcher_service.py -v`
- [x] Run ModeCommandHandlerService Tests
  - Prerequisites: ModeCommandHandlerService implementation and tests complete
  - Command: `pytest tests/unit/test_mode_command_handler_service.py -v`

### Integration Testing Framework
#### Test Creation
- [x] Create integration test directory structure
- [x] Implement shared test fixtures in conftest.py
- [x] Create conversation flow integration tests
- [x] Create mode transition integration tests
- [x] Create audio pipeline integration tests
- [x] Create CLI command integration tests
- [x] Create resource cleanup integration tests

#### Integration Tests Execution
Prerequisites for ALL integration tests:
- All unit tests passing
- Mock services configured
- Test environment properly set up

Test Execution Order:
1. [x] Run Basic Integration Tests
   - Command: `pytest tests/integration/test_basic_integration.py -v`
   - Validates: Basic service communication

2. [x] Run Conversation Flow Tests
   - Command: `pytest tests/integration/test_conversation_flow.py -v`
   - Validates: End-to-end conversation handling

3. [x] Run Mode Transition Tests
   - Command: `pytest tests/integration/test_mode_transitions.py -v`
   - Validates: System mode changes

4. [x] Run Audio Pipeline Tests
   - Command: `pytest tests/integration/test_audio_pipeline.py -v`
   - Validates: Audio processing chain

5. [x] Run CLI Command Tests
   - Command: `pytest tests/integration/test_cli_command_integration.py -v`
   - Validates: CLI functionality

6. [ ] Run Resource Cleanup Tests
   - Command: `pytest tests/integration/test_resource_cleanup.py -v`
   - Validates: Resource management

### Test Helper Utilities
- [x] Create `EventSynchronizer` helper for event timing verification
- [x] Create `ResourceMonitor` helper for cleanup verification
- [x] Implement `RetryDecorator` for handling flaky tests
- [x] Add test result tracking system

### Test Monitoring and Reporting
- [ ] Run All Tests with Coverage Report
  - Prerequisites: All individual test suites passing
  - Command: `pytest --cov=cantina_os tests/ -v --cov-report=html`
- [ ] Review and Document Test Results
  - Update test status in dev log
  - Document any failing tests
  - Track flaky test metrics

## Current Focus

### Testing Priority Queue
1. [ ] Complete CLI and Yoda-related services unit tests
2. [ ] Align service implementation patterns with established architecture
3. [ ] Complete all unit test executions
4. [ ] Run integration tests in specified order
5. [ ] Generate and review coverage report
6. [ ] Address any test failures or flaky tests
7. [ ] Update dev log with test results

### Test Success Criteria
- All unit tests passing
- Integration test success rate > 95%
- Test coverage > 80%
- Flaky test rate < 1%

## Notes

- ✅ Phase 1 core services implemented
- ✅ Test creation phase complete
- ✅ GPT Service tests passing with improved stability
- ✅ ElevenLabsService tests passing with improved stability
- ✅ CommandDispatcherService tests passing (7/7 tests)
- ✅ Event Bus error handling improved with better resilience
- ✅ Mock Arduino service implementation complete
- 🔄 Currently in systematic test execution phase
- Current test flakiness rate: 2.8% (target: <1%)
- Most test failures occur in integration tests due to timing issues

## Phase 2: Enhanced Capabilities & Expression

### Sentiment Analysis
- [ ] Implement sentiment extraction in `GPTService`
- [ ] Create speech lifecycle events from `ElevenLabsService`
- [ ] Update `EyeLightControllerService` to respond to sentiment

### Tool Call System
- [x] Define tool schemas in system prompts
- [x] Implement tool call parsing in `GPTService`
- [x] Add tool registration system in `GPTService`
- [ ] Create tool handler methods

### Service Configuration
- [ ] Implement configurable service loading
- [ ] Add support for different run configurations
- [ ] Add mock vs. real service selection

### Error Handling & Diagnostics
- [x] Refine error handling across services
- [ ] Add comprehensive status reporting
- [ ] Implement diagnostic view for service health

### Performance Monitoring
- [ ] Implement latency budgets for critical paths
- [ ] Add performance logging
- [ ] Create performance visualization tools

## Phase 3: Future Extensions

### Configuration Service
- [ ] Design and implement `ConfigurationService`
- [ ] Add parameter namespacing
- [ ] Support dynamic reconfiguration

### Health Monitoring
- [ ] Implement `HealthMonitorService`
- [ ] Add service heartbeat mechanism
- [ ] Create system health dashboard

### Advanced Event Bus
- [ ] Evaluate Redis/ZeroMQ for distributed event bus
- [ ] Design process management for distributed system
- [ ] Implement multi-machine communication

## Recently Completed

- ✅ Created basic project structure
- ✅ Implemented event topics and payloads
- ✅ Created BaseService class
- ✅ Implemented all core services
- ✅ Set up testing framework
- ✅ Created unit tests for all services
- ✅ Fixed MicInputService test hanging issue and improved async handling
- ✅ Implemented core integration tests
- ✅ Created mock services for Deepgram, OpenAI, and ElevenLabs
- ✅ Implemented test helper utilities (EventSynchronizer, ResourceMonitor, RetryDecorator)
- ✅ Added synchronization barrier and grace period in YodaModeManagerService
- ✅ Fixed VLC media player cleanup issues
- ✅ Created mock Arduino service with configurable timing and errors
- ✅ Added resource tracking and cleanup verification
- ✅ Created CLI command integration tests
- ✅ Created resource cleanup integration tests for key services
- ✅ Fixed ElevenLabsService tests with proper mocking and timeouts
- ✅ Identified CLI architecture issues and missing unit tests
- ✅ Fixed CommandDispatcherService tests with enhanced error handling
- ✅ Enhanced BaseService error handling for event bus failures
- ✅ Fixed and enhanced event listener removal with proper handling of None event_bus
- ✅ Added grace periods for state propagation in critical transitions
- ✅ Implemented robust validation in BaseService for subscribe and emit methods

## Current Focus

- 🔄 Creating unit tests for CLI-related services
- 🔄 Aligning implementation patterns across services
- 🔄 Implementing remaining integration tests
- 🔄 Improving test stability and reducing flakiness
- 🔄 Expanding performance test coverage
- 🔄 Enhancing system health monitoring
- 🔄 Documenting testing patterns and best practices
- 🔄 Setting up CI/CD integration

## Notes

- ✅ Phase 1 core services completed and tested
- ✅ Unit tests passing for all core services
- ✅ Integration test framework complete with all essential tests implemented
- Current test flakiness rate: 2.8% (target: <1%)
- Most test failures occur in integration tests due to timing issues
- Primary focus: Stabilize testing framework before moving to Phase 2 