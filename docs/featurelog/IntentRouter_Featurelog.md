 a# 🎛️ IntentRouter Feature Log

## 🎯 Feature Overview

The **IntentRouter** feature enables DJ R3X to both converse naturally with users AND execute commands based on their voice requests. This creates a more fluid and intuitive interaction model where users can speak naturally, and the system both responds conversationally while also performing the requested actions.

## 🧠 Core Concept

The key insight driving this feature is using OpenAI's function calling capability to **cleanly separate**:

1. **What DJ R3X says** (natural language response sent to text-to-speech)
2. **What DJ R3X does** (structured intent data used to control hardware)

This approach prevents machine-readable JSON or function data from being spoken aloud by the TTS system, while still allowing the LLM to both generate a natural conversational response AND identify actionable intents.

## 🔄 Architecture Flow

```
Voice Input → Transcription → GPTService (with function calling) → Two parallel outputs:
   ├─→ LLM_RESPONSE (natural language) → ElevenLabsService (speech synthesis)
   └─→ INTENT_DETECTED (structured intent) → IntentRouterService → Hardware Commands
```

## 🧩 Key Components

1. **OpenAI Function Definitions** (new)
   - Structured schemas for actions DJ R3X can perform
   - Implemented with Pydantic for type safety
   - Centralized in a dedicated module (`command_functions.py`)

2. **GPTService Enhancements** (updated)
   - Uses function calling to extract structured intents 
   - Separates conversational response from function calls
   - Emits `INTENT_DETECTED` events when functions are called

3. **IntentRouterService** (new)
   - Routes intents to appropriate hardware commands
   - Maps intent parameters to command-specific formats
   - Provides a unified interface for all voice-triggered actions

4. **Event Bus Extensions** (updated)
   - New `INTENT_DETECTED` event topic
   - New `IntentPayload` model for structured intent data
   - Leverages existing command infrastructure

## 📋 Implementation Plan

A detailed implementation checklist is available in [IntentRouter_TODO.md](IntentRouter_TODO.md), covering:

- Event bus updates
- Function definitions
- GPTService enhancements
- IntentRouterService implementation
- Testing strategy
- Documentation updates

## 🌐 External Documentation

### OpenAI Function Calling / Tools

- [OpenAI Tools Overview](https://platform.openai.com/docs/guides/function-calling)
- [OpenAI API Reference - Tools](https://platform.openai.com/docs/api-reference/chat/create#chat-create-tools)
- [OpenAI Cookbook - Function Calling Examples](https://cookbook.openai.com/examples/function_calling_with_an_llm)

### Pydantic (for Type-Safe Function Definitions)

- [Pydantic v2 Documentation](https://docs.pydantic.dev/latest/)
- [JSON Schema Generation](https://docs.pydantic.dev/latest/usage/json_schema/)
- [Field Constraints](https://docs.pydantic.dev/latest/api/fields/)

## 🎯 User Stories

1. As a guest, I want to say "play the Cantina song" and have DJ R3X respond conversationally ("You got it! This classic never gets old!") while also starting the music playback.

2. As a guest, I want to ask DJ R3X to "change your eyes to blue" and have it both acknowledge my request verbally and change the LED eye color pattern.

3. As a guest, I want the voice interaction to feel natural, with no technical jargon or JSON being spoken aloud.

## 🎯 Acceptance Criteria

- Voice commands correctly trigger both verbal responses and action execution
- No technical/structured data appears in the spoken response
- All existing CLI commands continue to work
- Commands can be processed in both streaming and non-streaming response modes
- System gracefully handles ambiguous requests and invalid parameters

## 📝 Future Enhancements (Post-MVP)

- Support for more complex multi-step commands
- Intent confidence scoring and threshold-based execution
- More sophisticated parameter validation
- YodaModeManager integration for context-aware behaviors
- Integration with a broader set of hardware controls
- Command suggestion when intent is ambiguous 

## 📝 Implementation Log

### 2023-11-27: Initial Implementation Complete

#### 🔄 Architecture Implementation

The IntentRouter feature has been successfully implemented with the following architecture:

1. **Command Functions Module**:
   - Created `cantina_os/llm/command_functions.py` to centralize function definitions
   - Implemented Pydantic models for three core functions:
     - `play_music(track: str)` - For playing music tracks
     - `stop_music()` - For stopping music playback
     - `set_eye_color(color: str, pattern: Optional[str], intensity: Optional[float])` - For controlling eye LEDs
   - Added utility functions for generating OpenAI-compatible function schemas
   - Created proper type hints and documentation
   - Used Pydantic field validation for parameters (e.g., intensity range 0.0-1.0)

2. **Event Bus Extensions**:
   - Added `INTENT_DETECTED` to `EventTopics` in `event_topics.py`
   - Implemented `IntentPayload` in `event_payloads.py` with:
     - `intent_name: str` - Function name that was called
     - `parameters: Dict[str, Any]` - Validated parameters
     - `confidence: Optional[float]` - For future confidence scoring
     - `original_text: str` - The assistant's response text
     - `conversation_id: Optional[str]` - For context tracking

3. **GPTService Enhancements**:
   - Added automatic registration of command functions during initialization
   - Added tool calling capability to API requests with `tool_choice="auto"`
   - Implemented parameter validation using Pydantic models
   - Enhanced streaming response handling to properly accumulate tool calls
   - Added `_process_tool_calls()` method to extract and emit intents
   - Ensured clean separation between conversational response and tool calls

4. **IntentRouterService Implementation**:
   - Created new service with standard lifecycle methods
   - Added subscription to `INTENT_DETECTED` events
   - Implemented handlers for:
     - `_handle_play_music_intent`
     - `_handle_stop_music_intent`
     - `_handle_set_eye_color_intent`
   - Added robust error handling and parameter validation
   - Added comprehensive logging throughout

5. **Main Application Integration**:
   - Added `IntentRouterService` to service registry in `services/__init__.py`
   - Added service to initialization order in `main.py`
   - Positioned service properly in the initialization sequence (after GPT but before hardware services)

#### 🧪 Testing

All features were manually verified to be working as expected:

- Voice command "play music" successfully:
  - Generates a conversational response with no technical content
  - Emits an INTENT_DETECTED event with the appropriate parameters
  - Triggers the corresponding hardware command via the router
- The system maintains clean separation between conversational responses and actions
- All existing CLI commands continue to work without interference

#### 🔜 Next Steps

The following items remain to be completed:

1. **Testing**: Implement formal unit and integration tests
2. **Documentation**: Update development logs with implementation details
3. **User Documentation**: Create user-facing documentation for supported voice commands
4. **Future Enhancements**: Consider implementing the planned post-MVP enhancements 

### 2025-05-14: Testing Implementation

#### 🧪 Test Suite Implementation

Implemented comprehensive test suite for the IntentRouter feature with three main components:

1. **Unit Tests for IntentRouterService**:
   - Created tests for each intent handler (`play_music`, `stop_music`, `set_eye_color`)
   - Added parameter validation tests (missing/invalid parameters)
   - Verified correct event emission for each command type
   - Confirmed proper error handling for unknown intents

2. **Unit Tests for GPTService Function Calling**:
   - Added specific tests for the function calling capabilities
   - Verified correct handling of tool calls in GPT responses
   - Tested valid and invalid parameter handling
   - Confirmed processing of multiple intents in a single response
   - Tested error cases (malformed JSON, unknown functions)

3. **Integration Tests for End-to-End Flow**:
   - Created tests for the complete voice → intent → command flow
   - Mocked the LLM response to simulate function calls
   - Verified correct propagation of intent payloads through the system
   - Confirmed both speech response and command events are generated
   - Successfully tested multiple intents in a single request

All test suites are now passing, representing a complete verification of the IntentRouter feature's functionality.

#### 🚧 Challenges Overcome

- **Event Handling**: Fixed critical issues in the BaseService's event handling:
  - Updated `emit()` method to properly await event bus emissions
  - Updated `subscribe()` method to properly await event bus subscriptions
  - These fixes resolved race conditions in the event pipeline

- **Integration Test Approach**: 
  - Identified that mocking `_get_gpt_response` was insufficient for integration testing
  - Successfully implemented proper mocking of `_process_with_gpt` to simulate the full event flow
  - Added robust test fixtures that properly initialize and await service startups

- **Event Topic Usage**:
  - Corrected usage of event topics in tests (TRANSCRIPTION_TEXT → TRANSCRIPTION_FINAL → VOICE_LISTENING_STOPPED)
  - Aligned test events with the actual event flow used in production

#### 🔍 Test Coverage

The implemented tests now verify the complete IntentRouter pipeline:
- Intent detection and extraction from LLM responses
- Parameter validation and transformation
- Correct routing to hardware commands
- Error handling and edge cases
- Multi-intent processing
- End-to-end event propagation

With all integration tests passing, the IntentRouter feature is now fully validated for production use. 