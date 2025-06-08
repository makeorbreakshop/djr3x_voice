# CantinaOS Integration Plan

## Status Update

**[APPROVED & FINALIZED] - Implementation Ready**

This integration plan has been reviewed, approved, and is now ready for implementation. The phased approach outlined in this document will guide the refactoring of the DJ R3X codebase to align with the CantinaOS architecture.

## Overview

This document outlines the practical integration plan for CantinaOS, focusing on refactoring the current DJ R3X MVP codebase to align with the `CantinaOS-Initial Plan.md` architecture. The goal is to establish a robust, decoupled, and extensible foundation for current functionality and future expansion, incorporating ROS-inspired design principles for enhanced scalability, maintainability, and testability.

## 1. Event Bus Standardization, Naming, & Payload Design

**Current Status:** Basic event bus implemented with `pyee.AsyncIOEventEmitter` and a flat `EventTypes` enum. Payload structures are inconsistent.

**Required Changes:**
-   **Event Bus Technology (MVP):** Continue using `pyee.AsyncIOEventEmitter` for the MVP to maintain focus on the initial structural refactor.
-   **Hierarchical Event Naming (Topics):**
    -   Replace the flat `EventTypes` enum with a system of hierarchical, string-based topic names (e.g., `/audio/transcription/final`, `/speech/synthesis/started`, `/tools/execution/result`).
    -   This improves organization, reduces potential for naming conflicts, and allows for more flexible subscription patterns in the future if the bus technology evolves.
    -   A helper mechanism (e.g., classes or functions) can be used to construct and manage these topic strings consistently.
-   **Design for Distributability:**
    -   The event bus abstraction within services (`emit`, `on`, using string-based topics) must be kept simple to allow for potential future swapping of the underlying bus technology (e.g., to Redis Pub/Sub, ZeroMQ) with minimal changes to service logic.
    -   All Pydantic event payloads must be fully serializable (e.g., to JSON).
-   **Standardized Payloads (Pydantic):**
    -   Define standardized, versioned Pydantic models for *every* distinct event type's payload. These models serve as schemas and provide runtime validation.
    -   Create a dedicated file (e.g., `src/event_payloads.py`) for these Pydantic model definitions.
    -   Document required/optional fields clearly within each Pydantic model using `Field` descriptions.
    -   **Essential Payload Fields:** Each Pydantic model for event payloads must include (likely via a `BaseEventPayload` class):
        -   `timestamp: float = Field(default_factory=time.time, description="Unix timestamp of event creation")`
        -   `event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique ID for this specific event instance")`
        -   `conversation_id: Optional[str] = Field(default=None, description="ID for the overarching user interaction or conversation turn. Propagated through related events.")`
        -   `schema_version: str = Field(default="1.0", description="Version of the event payload schema.")`
    -   Services will emit events with instances of these Pydantic models, and subscribers will expect these types, ensuring type safety and clarity.
-   Enhance error handling for events that fail validation (Pydantic provides significant built-in validation).

**Example Hierarchical Topic Naming & Payload:**
```python
# src/event_topics.py (New file for topic definitions)
class EventTopics:
    AUDIO_TRANSCRIPTION_FINAL = "/audio/transcription/final"
    SPEECH_SYNTHESIS_STARTED = "/speech/synthesis/started"
    SPEECH_SYNTHESIS_AMPLITUDE = "/speech/synthesis/amplitude"
    SPEECH_SYNTHESIS_ENDED = "/speech/synthesis/ended"
    LLM_RESPONSE_TEXT = "/llm/response/text"
    LLM_SENTIMENT_ANALYZED = "/llm/sentiment/analyzed"
    TOOL_CALL_REQUEST = "/tools/execution/request"
    TOOL_CALL_RESULT = "/tools/execution/result"
    SYSTEM_MODE_CHANGE = "/system/mode/change"
    SYSTEM_SET_MODE_REQUEST = "/system/mode/set_request"
    EYES_COMMAND = "/eyes/command/set"
    MUSIC_PLAY_REQUEST = "/music/command/play"
    SERVICE_STATUS_UPDATE = "/system/diagnostics/status_update"
    # ... and so on for all events

# src/event_payloads.py
import time
import uuid
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class BaseEventPayload(BaseModel):
    timestamp: float = Field(default_factory=time.time, description="Unix timestamp of event creation")
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique ID for this specific event instance")
    conversation_id: Optional[str] = Field(default=None, description="ID for the overarching user interaction or conversation turn.")
    schema_version: str = Field(default="1.0", description="Version of the event payload schema.")

class TranscriptionTextPayload(BaseEventPayload):
    text: str
    source: str = Field(description="Origin of the transcription, e.g., 'deepgram_stream', 'whisper_batch'")
    is_final: bool = Field(default=True, description="Whether this is a final or interim transcription")

# ... other Pydantic models inheriting from BaseEventPayload

# Example Usage:
# from src.event_topics import EventTopics
# from src.event_payloads import TranscriptionTextPayload
# event_bus.emit(EventTopics.AUDIO_TRANSCRIPTION_FINAL, TranscriptionTextPayload(...))
```

**Implementation Priority:** High

## 2. Service Decoupling & Refactoring

**Current Status:** Modular managers exist. Some direct service instance attributes on the event bus object could lead to tight coupling.

**Required Changes:**
-   **Strict Event-Only Communication:** Services must interact *exclusively* through event emissions (using defined topic strings) and subscriptions.
-   Remove any direct inter-service method calls or access to other service instances.
-   **Refactor and Map Existing Managers to CantinaOS Services:** (Details as previously discussed: `StreamManager` -> `DeepgramTranscriptionService`, `VoiceManager` splits into `MicInputService`, `GPTService`, `ElevenLabsService`, etc.)
    -   (Full list of service mappings as per previous detailed plan)
-   **Service Lifecycle:** `async def start(self)` and `async def stop(self)`.
-   **Handling Out-of-Order/Stale Events:** Services, particularly those driving UI/Physical outputs (e.g., `EyeLightControllerService`), must implement logic to:
    -   Maintain context of the current `conversation_id` they are processing.
    -   Ignore or appropriately handle events with a non-matching `conversation_id`.
    -   Use `timestamp` to deal with stale events within the correct `conversation_id` context (e.g., ignore an event if a later, superseding event has already been processed).

**Implementation Priority:** High

## 3. Startup Orchestration & Configuration Management

**Current Status:** Components initialized in `src/main.py` (`initialize_components`). Configuration primarily from environment variables and `config/*.py` files.

**Required Changes:**
-   **Startup Orchestration (MVP):** The `initialize_components` function in `src/main.py` will continue to instantiate and start all services.
-   **Configurable Service Loading (Phase 2+):** Enhance `main.py` or develop a dedicated "Launcher" module to:
    -   Support different run configurations (e.g., "default", "test_with_mocks", "performance_profiling") loaded from a configuration file or command-line arguments.
    -   Enable conditional launching of services (e.g., real vs. mock versions of hardware-dependent services).
    -   Potentially support dependency-based startup ordering if needed, though explicit ordering in `initialize_components` is acceptable for MVP.
-   **Centralized Configuration Service (Future - Phase 3+):**
    -   Design and implement a `ConfigurationService` responsible for loading, storing, and providing configuration parameters to all other services.
    -   Services would request their configuration from this service at startup.
    -   Future enhancements could include support for namespaced parameters, dynamic reconfiguration at runtime with change notifications (complex, for later consideration).

**Implementation Priority:** Configurable service loading (Phase 2). `ConfigurationService` (Phase 3+).

## 4. Memory Management (within GPTService)

**Current Status:** `VoiceManager` likely handles some conversation state internally, but this is not formalized as a dedicated, structured memory component.

**Required Changes:**
-   The new `GPTService` will own and instantiate a `SessionMemory` class.
-   The `SessionMemory` class will:
    -   Store a list of messages (e.g., `{"role": "user", "content": "..."}`, `{"role": "assistant", "content": "..."}`).
    -   Include the system prompt/persona at the beginning of the history.
    -   Implement `add_message(self, role: str, content: str)` method.
    -   In `add_message`, use a tokenizer (e.g., `tiktoken`) to estimate the total token count of the history.
    -   If `max_tokens` (configurable, e.g., 4000) is exceeded, implement a summarization strategy.
        -   **MVP Summarization:** Truncate the oldest user/assistant message pair (after the system prompt) until the token count is within limits.
        -   **Future Enhancement:** LLM-based summarization of older turns.
    -   Provide a method `get_history(self) -> List[Dict[str, str]]` for the `GPTService` to pass to the LLM.
-   Design the `SessionMemory` API with future integration of vector memory or other long-term memory solutions in mind.

**Implementation Priority:** High (as a core part of `GPTService` development)

## 5. Command/Tool Call System

**Current Status:** `CommandInputThread` handles direct CLI commands. No formalized system for LLM-driven tool execution.

**Required Changes:**
-   **Tool Definition & Prompting:** The `GPTService`'s system prompt will define available tools, their purpose, parameters they accept, and the exact JSON format for invoking them. OpenAI's "tool use" or "function calling" features should be leveraged.
-   **`GPTService` Role:** When the LLM indicates a tool should be used, the `GPTService` will parse this and emit a `TOOL_CALL_REQUEST` event (e.g., `event_bus.emit(EventTopics.TOOL_CALL_REQUEST, CommandCallPayload(tool_name="play_song", parameters={"song_name": "Cantina Band"}))`).
-   **New `ToolExecutorService`:**
    -   Subscribes to `EventTopics.TOOL_CALL_REQUEST`.
    -   Maintains an internal "tool registry" (e.g., a dictionary mapping `tool_name` strings to asynchronous handler methods within the `ToolExecutorService`).
    -   **Execution Flow:**
        1.  Receives `CommandCallPayload`.
        2.  Looks up `tool_name` in its registry.
        3.  If found, emits `EventTopics.TOOL_CALL_RESULT` (optional, for UI feedback).
        4.  Calls the associated handler method with `payload.parameters`.
        5.  The handler method will either:
            -   Execute a self-contained tool (e.g., a web search).
            -   Emit a more specific event that another service (e.g., `MusicControllerService`) listens for (e.g., `self.event_bus.emit(EventTopics.MUSIC_PLAY_REQUEST, MusicPlayRequestPayload(query=song_name))`). This maintains decoupling.
        6.  Upon completion or error, the handler (or `ToolExecutorService`) emits `EventTopics.TOOL_CALL_RESULT`, including results or error details. These events can be used by `GPTService` to inform the LLM of the tool's outcome.
-   Add validation and basic permission checking for tool execution if necessary in the future.

**Implementation Priority:** Core tool call system (Medium, dependent on `GPTService`).

## 6. Sentiment-Driven Expression

**Current Status:** `LEDManager` handles LED patterns based on system states (e.g., listening, processing) derived from `VoiceManager` activity. No explicit, distinct `sentiment` event currently drives LED behavior.

**Required Changes:**
-   **Sentiment Extraction in `GPTService`:**
    -   After receiving a response from the LLM, the `GPTService` will determine its sentiment.
    -   This can be achieved by:
        -   Prompting the LLM itself to output a sentiment classification.
        -   Using a local sentiment analysis library (e.g., VADER) on the `response_text`. (MVP approach: faster, cheaper).
    -   `GPTService` emits a `LLM_SENTIMENT_ANALYZED` event with a `SentimentPayload` (e.g., `event_bus.emit(EventTopics.LLM_SENTIMENT_ANALYZED, SentimentPayload(label="positive", score=0.8))`).
-   **Speech Lifecycle Events from `ElevenLabsService`:**
    -   The new `ElevenLabsService` (handling TTS) will emit:
        -   `EventTopics.SPEECH_SYNTHESIS_STARTED` (Payload: e.g., `text`, `duration_estimate`).
        -   `EventTopics.SPEECH_SYNTHESIS_AMPLITUDE` (Payload: e.g., `amplitude_value`, emitted frequently during speech for pulsing).
        -   `EventTopics.SPEECH_SYNTHESIS_ENDED`.
-   **`EyeLightControllerService` (Refactored `LEDManager`) Role:**
    -   Subscribes to:
        -   `EventTopics.LLM_SENTIMENT_ANALYZED` (from `GPTService`): To set a base eye color, animation, or mood.
        -   `EventTopics.SPEECH_SYNTHESIS_STARTED` (from `ElevenLabsService`): To trigger active speaking animations (e.g., start pulsing).
        -   `EventTopics.SPEECH_SYNTHESIS_AMPLITUDE` (from `ElevenLabsService`): To modulate LED brightness in sync with speech volume.
        -   `EventTopics.SPEECH_SYNTHESIS_ENDED` (from `ElevenLabsService`): To transition back to an idle or sentiment-based ambient animation.
        -   `EventTopics.CURRENT_MODE` (from `YodaModeManagerService`): To apply mode-specific base patterns.
    -   Maintains an internal mapping (configurable) between sentiment categories, speech states, system modes, and specific LED animations/colors/brightness patterns.
-   Prepare for future word-level expression control if ElevenLabs provides detailed SSML timestamps.

**Implementation Priority:** Medium

## 7. Target CantinaOS Service Definitions

This is a summary of the target services post-refactor:

-   **`MicInputService`**: Captures raw audio; emits `raw_audio_chunk`. (For non-streaming ASR).
-   **`DeepgramTranscriptionService`** (was `StreamManager`): Handles streaming ASR via Deepgram; listens for audio input (or manages its own), emits `transcription_text` (with `is_final` flags).
-   **`WhisperTranscriptionService`** (Optional): Handles local ASR via Whisper; listens to `MicInputService`, emits `transcription_text`.
-   **`GPTService`** (New): Manages LLM interactions, `SessionMemory`, sentiment analysis; listens to `transcription_text`, emits `response_text`, `sentiment`, `command_calls`.
-   **`ElevenLabsService`** (New): Handles TTS via ElevenLabs; listens to `response_text`, plays audio, emits `speech_start`, `speech_amplitude`, `speech_end`.
-   **`EyeLightControllerService`** (was `LEDManager`): Controls all eye LED animations; listens to `sentiment`, speech lifecycle events, `current_mode`. Contains Arduino communication logic (`ArduinoBridge` equivalent).
-   **`MusicControllerService`** (was `MusicManager`): Manages music playback and ducking; listens to `music_play_request`, `current_mode`.
-   **`YodaModeManagerService`** (was `SystemModeManager`): Manages system modes; listens to `set_mode` requests, emits `current_mode`.
-   **`CLIService`** (was `CommandInputThread`): Provides CLI for control; emits `set_mode` or other specific command events.
-   **`ToolExecutorService`** (New): Orchestrates LLM-driven tool calls; listens to `command_calls`, emits specific requests to other services or executes tools internally, emits tool status events.

## 8. Service Reliability, Diagnostics & Health Monitoring

**Required Changes:**
-   **Robust Error Handling:** All services must implement robust internal error handling to prevent crashes and attempt graceful recovery or state reporting.
-   **Standardized Diagnostic/Status Events (MVP):**
    -   Services should emit `SERVICE_STATUS_UPDATE` events (topic defined in `EventTopics`).
    -   Payload for these events (e.g., `ServiceStatusPayload`) should include: `service_name: str`, `status: str ('INITIALIZING', 'RUNNING', 'DEGRADED', 'ERROR', 'STOPPED')`, `message: Optional[str]`, `severity: Optional[str] ('INFO', 'WARNING', 'ERROR', 'CRITICAL')`.
    -   Centralized logging of these status events will provide basic health visibility.
-   **Future `HealthMonitorService` (Phase 3+):**
    -   A dedicated `HealthMonitorService` could subscribe to `SERVICE_STATUS_UPDATE` events (and potentially periodic heartbeat events if implemented later).
    -   This service could log aggregated health, trigger alerts, or orchestrate system-level graceful degradation.
-   Graceful degradation strategies should be considered for critical service failures.

**Implementation Priority:** Basic error handling and structured status events (MVP - Phase 1/2). `HealthMonitorService` (Future - Phase 3+).

## 9. Advanced Logging Framework

**Required Changes:**
-   **Standardized Logging Practices (Phase 1/2):**
    -   Utilize Python's built-in `logging` module consistently across all services.
    -   Establish standardized log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    -   **Contextual Logging:** Configure logging formatters (or use `LoggerAdapter`) to automatically include important context in all log messages, such as:
        -   `timestamp` (already part of standard logging).
        -   `service_name` (identifying the source service).
        -   `conversation_id` (when available/relevant to the operation being logged).
        -   `event_id` (if logging is related to processing a specific event).
    -   Log key lifecycle events, significant state changes, errors, and important decisions within services.
-   **Log Throttling & Filtering (Future - Phase 3+):**
    -   For very high-frequency logs (e.g., from `speech_amplitude` processing if debugged verbosely), implement log throttling mechanisms if they become problematic.
    -   Configure logging handlers and filters for different environments (e.g., more verbose to console in debug mode, structured JSON logs to file for production/analysis).

**Implementation Priority:** Standardized contextual logging (Phase 1/2). Advanced filtering/throttling (Phase 3+).

## 10. Performance & Latency

**Required Changes:**
-   **Timestamp Logging:** Leverage the `timestamp` field in all event payloads for performance analysis.
-   **Establish Latency Budgets:** Define target maximum latencies for critical interaction flows (e.g., user speech start to first eye/speech response: target <500ms, ideal <300ms).
-   **Monitor Key Stages:**
    -   Services should log processing times for their key operations.
    -   Use event logs (with `timestamp` and `conversation_id`) to trace interaction latency across multiple services.
    -   Periodically analyze these logs to identify and address bottlenecks.
-   This data will inform optimization efforts and ensure a responsive user experience.

**Implementation Priority:** Timestamping in payloads & basic logging (Phase 1). Active monitoring and budget adherence (Ongoing from Phase 1/2).

## Technical Implementation Guidelines

### Event Bus Enhancements & Payloads
Services should strictly type event payloads using Pydantic models.
```python
# Example: Emitting an event
# from src.event_payloads import TranscriptionTextPayload
# self.event_bus.emit(EventTopics.AUDIO_TRANSCRIPTION_FINAL, 
#                     TranscriptionTextPayload(text="Hello world", source="deepgram", timestamp=time.time(), is_final=True))

# Example: Handling an event
# from src.event_payloads import TranscriptionTextPayload
# async def on_transcription(payload: TranscriptionTextPayload):
#     if payload.is_final:
#         logger.info(f"Final transcription from {payload.source}: {payload.text}")
```

### Service Independence
Services should follow this pattern:
1.  Accept dependencies (like `event_bus`) through constructor.
2.  Initialize resources and subscribe to relevant events in an `async def start(self)` method.
3.  Implement core logic in event handlers and internal methods.
4.  Release resources and unsubscribe from events in an `async def stop(self)` method.
5.  Handle errors gracefully, emit status/error events if necessary for system monitoring.

### Memory Management (`SessionMemory` within `GPTService`)
```python
# Example snippet for SessionMemory (conceptual)
import tiktoken

class SessionMemory:
    def __init__(self, system_prompt: str, model_name: str = "gpt-4", max_tokens: int = 4000):
        self.system_prompt = {"role": "system", "content": system_prompt}
        self.messages: List[Dict[str, str]] = []
        self.max_tokens = max_tokens
        try:
            self.tokenizer = tiktoken.encoding_for_model(model_name)
        except KeyError:
            self.tokenizer = tiktoken.get_encoding("cl100k_base") # Fallback

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        self._ensure_token_limit()

    def _estimate_tokens(self, messages: List[Dict[str, str]]) -> int:
        # Simplified token estimation; a more accurate one would match OpenAI's approach
        num_tokens = 0
        for message in messages:
            num_tokens += len(self.tokenizer.encode(message["content"]))
            num_tokens += 4 # For role, name, etc.
        num_tokens += 2 # For priming
        return num_tokens

    def _ensure_token_limit(self):
        history = [self.system_prompt] + self.messages
        current_tokens = self._estimate_tokens(history)
        
        while current_tokens > self.max_tokens and len(self.messages) > 1:
            # Remove the oldest user/assistant pair after system prompt
            self.messages.pop(0) # Remove user message
            if self.messages: # Ensure there's a corresponding assistant message
                 self.messages.pop(0) # Remove assistant message
            
            history = [self.system_prompt] + self.messages
            current_tokens = self._estimate_tokens(history)

    def get_history(self) -> List[Dict[str, str]]:
        return [self.system_prompt] + self.messages
```

### Tool Call Structure (`ToolExecutorService`)
```python
# Conceptual snippet for ToolExecutorService
# from src.event_payloads import CommandCallPayload, MusicPlayRequestPayload

class ToolExecutorService:
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.tool_registry = {
            "play_song": self._handle_play_song,
            "web_search": self._handle_web_search, 
            # Add more tools here
        }
        self.event_bus.on(EventTopics.TOOL_CALL_REQUEST, self.on_command_call)

    async def on_command_call(self, payload: CommandCallPayload):
        handler = self.tool_registry.get(payload.tool_name)
        if handler:
            # Consider emitting TOOL_CALL_RESULT event here
            # self.event_bus.emit(EventTopics.TOOL_CALL_RESULT, {"request_id": payload.request_id, "result": result})
            try:
                result = await handler(payload.parameters)
                # self.event_bus.emit(EventTopics.TOOL_CALL_RESULT, {"request_id": payload.request_id, "result": result})
            except Exception as e:
                # self.event_bus.emit(EventTopics.TOOL_CALL_RESULT, {"request_id": payload.request_id, "error": str(e)})
                logger.error(f"Error executing tool {payload.tool_name}: {e}")
        else:
            logger.warning(f"Tool '{payload.tool_name}' not found in registry.")
            # self.event_bus.emit(EventTopics.TOOL_CALL_RESULT, {"request_id": payload.request_id, "error": f"Tool '{payload.tool_name}' not found"})

    async def _handle_play_song(self, parameters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        song_query = parameters.get("song_query") or parameters.get("song_name")
        if song_query:
            logger.info(f"Tool call: play_song with query '{song_query}'")
            # Emit a specific event for MusicControllerService, DO NOT call it directly
            # self.event_bus.emit(EventTopics.MUSIC_PLAY_REQUEST, MusicPlayRequestPayload(query=song_query))
            return {"status": "play_song request emitted", "query": song_query}
        return {"status": "failed", "error": "Missing song_query parameter"}

    async def _handle_web_search(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        query = parameters.get("query")
        if not query:
            return {"status": "failed", "error": "Missing query parameter for web_search"}
        logger.info(f"Tool call: web_search with query '{query}'")
        # Actual web search logic here...
        # For now, returning a dummy result
        return {"status": "success", "result": f"Search results for '{query}' would appear here."}
```

## Integration Roadmap

### Phase 1: Core Architecture Alignment (Foundation)
1.  **Event Naming & Payloads:**
    -   Define hierarchical `EventTopics` in `src/event_topics.py`.
    -   Define Pydantic models for ALL inter-service event payloads in `src/event_payloads.py`, inheriting from `BaseEventPayload` (with `timestamp`, `event_id`, `conversation_id`, `schema_version`).
    -   Define `conversation_id` generation (e.g., on first final ASR for a user turn) and propagation strategy.
2.  **Structured Logging Setup:** Implement contextual logging (service name, `conversation_id` where applicable) using Python\'s `logging` module.
3.  **Core Service Scaffolding & Refactoring:** (As previously detailed, services to use new topics and payloads).
4.  **Initial Stale/Out-of-Order Logic:** Implement basic `conversation_id` checking in key subscribing services.
5.  **Basic Service Status Events:** Services emit initial `ServiceStatusPayload` events (topic `EventTopics.SERVICE_STATUS_UPDATE`) for major state changes/errors.
6.  **Latency Logging:** Ensure services log critical processing times, and event timestamps are utilized.
7.  **Basic End-to-End Flow:** Test (ASR -> GPT -> TTS) with new services, topics, and payloads.

### Phase 2: Enhanced Capabilities & Expression
1.  **Sentiment Integration:** (As previously detailed, using new topics).
2.  **Tool Call System Implementation:** (As previously detailed, using new topics).
3.  **Configurable Service Loading:** Implement support for different run configurations (e.g., mock vs. real services) in `main.py` or a launcher module.
4.  **Mock Services Implementation:** Develop and integrate mock versions for all hardware-dependent services (see Testing Strategy).
5.  **Simulated Time for Testing:** Implement a mechanism for services to use a simulated clock for improved testability (see Testing Strategy).
6.  **Refine Error Handling & Diagnostics:** Improve consistency and detail of `ServiceStatusPayload` events.
7.  **Performance Monitoring & Optimization:** Actively analyze logs for latency bottlenecks against defined budgets.

### Phase 3: Prepare for Future Extensions
1.  **`ConfigurationService`:** Design and implement the centralized `ConfigurationService` (initial version).
2.  **Action-like Tool Enhancements:** Evolve tool call system for long-running tasks.
3.  **`HealthMonitorService`:** Implement the dedicated `HealthMonitorService`.
4.  Design interfaces for long-term memory, web interface event structure, document extension points.
5.  **Advanced Event Bus & Process Management (If Needed):** Evaluate and potentially implement Redis/ZeroMQ and distributed process management if system requirements evolve towards distribution.
6.  Advanced Logging Features: Implement log throttling/filtering as needed.

## Testing Strategy

1.  **Unit Tests:** (As previously detailed).
2.  **Integration Tests:** (As previously detailed).
3.  **End-to-End (E2E) Tests:** (As previously detailed).
4.  **Performance Benchmarks:** (As previously detailed).
5.  **Mock Services:**
    -   Develop mock/dummy versions for all hardware-dependent services (`EyeLightControllerService`, `AudioInput/TranscriptionServices` if they directly use mics, `MusicControllerService` for audio output, `ElevenLabsService` for API calls).
    -   Mocks must adhere to the same event contracts (payloads, event types) as real services.
    -   Mocks should log their actions or provide simple state inspection capabilities.
    -   The application must be configurable (e.g., via environment variables, config file) to run with real or mock services to facilitate development, automated testing, and CI/CD pipelines without requiring physical hardware.
6.  **Simulated Time:**
    -   Implement a mechanism (e.g., a global time provider that services can use instead of `time.time()`) that can be switched between real-time and a controllable simulated clock.
    -   This is crucial for testing time-dependent logic, timeouts, and replaying event sequences deterministically.
7.  **Enhanced Testing Process:**
    -   **Test-Driven Development (TDD):** Write tests before implementing services to ensure requirements are met.
    -   **Incremental Testing:** Test each service independently as we build them to catch issues early.
    -   **Continuous Testing:** Run tests frequently during development to validate changes.
    -   **Test Runner Script:** Create a dedicated script that executes all tests with appropriate configuration.
    -   **Comprehensive Logging:** Implement detailed logging in tests for easier debugging.
    -   **Granular Test Fixtures:** Use pytest fixtures for common test setup and to simulate different scenarios.
    -   **Visual Feedback:** Implement progress indicators and clear test result reporting.
    -   **Test Coverage Monitoring:** Track and maintain high test coverage for critical components.

## Conclusion

By systematically focusing on these core architecture improvements—including hierarchical event topics, standardized and enriched event payloads, clear service responsibilities with decoupled communication, structured memory management, a formal tool call system, and comprehensive strategies for configuration, reliability, performance, logging, and testability—we\'ll create a solid foundation that aligns with the original CantinaOS vision and ROS-inspired best practices. This will ensure the DJ R3X system is robust, maintainable, and readily extensible for exciting future capabilities. 