# IntentRouter Implementation TODO

## Overview
This document outlines the implementation plan for the Voice-to-Command MVP with GPTService function calling and IntentRouterService. The goal is to enable DJ R3X to respond to voice commands by both replying in character and executing the requested action.

## 🔄 Event Flow
```
[Transcription] → [GPTService] → [INTENT_DETECTED] → [IntentRouterService] → [Hardware Commands]
```

## 📋 Implementation Checklist

### 1️⃣ Event Bus Updates
- [x] Add `INTENT_DETECTED` to `event_topics.py` enum
- [x] Create `IntentPayload` model in `event_payloads.py` with:
  - [x] `intent_name: str` - name of the detected intent
  - [x] `parameters: Dict[str, Any]` - parameters for the intent
  - [x] `confidence: float` - optional confidence score
  - [x] `original_text: str` - the text that triggered the intent
  - [x] `conversation_id: str` - to track conversation context

### 2️⃣ OpenAI Function Definitions (cantinaos/llm/command_functions.py)
- [x] Create module to centralize function definitions
- [x] Implement Pydantic models for function parameters:
  - [x] Create parameter models like `PlayMusicParams`, `StopMusicParams`, etc.
  - [x] Use `Field()` with descriptions for clear parameter documentation
  - [x] Implement validation rules using Pydantic constraints
- [x] Create `FunctionDefinition` Pydantic model for consistent function schema creation
- [x] Define core functions with JSON schema:
  - [x] `play_music(track: str)`
  - [x] `stop_music()`
  - [x] `set_eye_color(color: str)`
- [x] Add helper methods for OpenAI tool format conversion
- [x] Add type hints and documentation
- [x] Ensure schemas are properly formatted for OpenAI API

### 3️⃣ GPTService Updates
- [x] Update GPTService to use function calling:
  - [x] Import function definitions from command_functions.py
  - [x] Add functions to API requests
  - [x] Set `tool_choice="auto"` in API params
- [x] Add intent extraction logic:
  - [x] Extract function call information when present
  - [x] Parse function arguments with proper error handling
  - [x] Create IntentPayload instances
- [x] Update emission logic:
  - [x] Emit `INTENT_DETECTED` event when function calls occur
  - [x] Keep `LLM_RESPONSE` clean of function call text
  - [x] Handle streaming vs. non-streaming responses
- [x] Add proper logging for intent detection

### 4️⃣ IntentRouterService Implementation
- [x] Create `intent_router_service.py`:
  - [x] Inherit from `StandardService`
  - [x] Implement `_start()` and `_stop()` methods
  - [x] Create `_setup_subscriptions()` with proper task wrapping
  - [x] Add intent handler method
- [x] Implement intent routing logic:
  - [x] Map intents to appropriate command events
  - [x] `play_music` → `MUSIC_COMMAND` with correct payload
  - [x] `stop_music` → `MUSIC_COMMAND` with correct payload
  - [x] `set_eye_color` → `EYE_COMMAND` with correct payload
- [x] Add error handling:
  - [x] Handle unknown intents gracefully
  - [x] Validate parameters before forwarding
  - [x] Log routing decisions and errors

### 5️⃣ Configuration and Service Registration
- [x] Add IntentRouterService to services list in main.py
- [x] Add service configuration in config.json if needed
- [x] Ensure proper initialization order

### 6️⃣ Testing
- [x] Write tests for GPTService intent extraction:
  - [x] Test that function calls are properly detected
  - [x] Test that correct intents are emitted
  - [x] Test error cases (malformed function calls)
- [x] Write tests for IntentRouterService:
  - [x] Test routing of each intent type
  - [x] Test parameter forwarding
  - [x] Test error handling
- [x] Create integration tests:
  - [x] Test end-to-end flow from transcript to command
  - [x] Test handling of ambiguous requests

### 7️⃣ Documentation Updates
- [x] Update system architecture document:
  - [x] Add IntentRouterService to service registry table
  - [x] Add `INTENT_DETECTED` to event topology
  - [x] Document the new event flow
- [x] Update dev log with implementation details and test progress
- [ ] Create user documentation for supported commands

### 8️⃣ Validation and Edge Cases
- [x] Handle rate limiting of intent processing
- [x] Implement graceful degradation if services are unavailable
- [x] Add timeout handling for intent routing
- [x] Consider conversation context for intent routing
- [x] Handle concurrent intents appropriately

## Acceptance Criteria
- [x] Voice command "play music" results in:
  - [x] One `LLM_RESPONSE` with no intent content
  - [x] One `INTENT_DETECTED` event
  - [x] One appropriate hardware command event
- [x] ElevenLabs only speaks the assistant's conversational response
- [x] All existing CLI commands continue to work
- [x] New code matches architecture standards
- [x] Unit tests pass
- [x] Integration tests pass
- [x] No machine-readable content in text-to-speech output

## Future Enhancements (Not for MVP)
- Intent confidence thresholds
- Multi-step intents
- YodaModeManager integration
- Intent history tracking
- Expanded function library
- Parameter validation rules

## Implementation Notes

The IntentRouter feature has been implemented with the following architecture:

1. **Command Functions**: Defined in `llm/command_functions.py` using Pydantic models for type safety
   - Implemented three core functions: play_music, stop_music, and set_eye_color
   - Each function has proper parameter validation and documentation

2. **GPT Service Enhancements**:
   - Now automatically registers command functions during initialization
   - Uses OpenAI function calling with `tool_choice="auto"` to enable intent detection
   - Properly handles both streaming and non-streaming response modes
   - Extracts intents from function calls and emits `INTENT_DETECTED` events
   - Validates parameters using Pydantic models before emitting events

3. **IntentRouter Service**:
   - Subscribes to `INTENT_DETECTED` events
   - Routes intents to appropriate hardware commands
   - Implements handlers for each intent type with appropriate parameter conversion
   - Includes comprehensive error handling and logging

4. **Event Flow**:
   - Voice input is transcribed normally
   - GPT Service processes the transcript and may detect intents via function calls
   - IntentRouter receives intents and converts them to hardware commands
   - DJ R3X responds conversationally via the normal LLM_RESPONSE flow
   - DJ R3X performs the requested action via the hardware command events

The implementation maintains clean separation between conversational responses and structured intents, ensuring that no technical JSON or function data appears in the spoken responses. 