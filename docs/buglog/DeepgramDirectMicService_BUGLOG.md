# DeepgramDirectMicService Implementation Plan

## Issue Summary

**Problem**: Our current audio pipeline has threading and event flow issues that create reliability problems and potential race conditions. The architecture uses two separate services:

1. MicInputService - Captures audio with sounddevice
2. DeepgramTranscriptionService - Processes captured audio and sends to Deepgram

This separation creates multiple thread-to-asyncio boundaries that must be carefully managed:
- Audio callback thread → Event Loop → Event Bus → Audio Queue → Deepgram Thread

## Investigation

### Current Implementation Analysis

The dev log (2025-05-12) indicates ongoing issues with our audio transcription pipeline:

> "Audio Pipeline Integration Issues Fixed:
> - Fixed critical issue where audio wasn't reaching Deepgram
> - Updated to Deepgram SDK v4.0.0 with proper event handler registration
> - Implemented proper thread-to-asyncio bridging for audio callbacks
> - Enhanced debugging and monitoring capabilities"

Despite these fixes, we're still experiencing complexity and reliability issues with this approach.

### Deepgram Microphone Class Capability

Investigating the Deepgram Python SDK documentation reveals that Deepgram provides a built-in `Microphone` class that directly streams audio from the local microphone to their service:

```python
# Example from Deepgram docs
microphone = Microphone(dg_connection.send)
microphone.start()
# ...
microphone.finish()
```

This allows bypassing our complex threading architecture entirely by letting Deepgram handle both audio capture and streaming.

## Proposed Solution

We propose creating a new `DeepgramDirectMicService` that:

1. Uses Deepgram's built-in `Microphone` class to capture and stream audio directly
2. Eliminates our custom audio capture and intermediate event bus messaging
3. Simplifies the threading model by reducing thread-to-asyncio boundaries
4. Maintains compatibility with our existing event system

### Alignment with Architecture Standards

This solution aligns with multiple guidelines from our [ARCHITECTURE_STANDARDS.md](../ARCHITECTURE_STANDARDS.md):

1. **Section 9: Audio Processing Standards**:
   - "Use a dedicated thread for audio I/O operations" - Deepgram's `Microphone` class handles this for us
   - "Single direction of data flow" - Direct flow from microphone to Deepgram
   - "Minimize thread crossings" - Eliminates intermediate event bus

2. **Section 3: Asynchronous Programming**:
   - "Always manage tasks properly" - Simpler task management with fewer components
   - "Handle task cancellation properly" - Cleaner lifecycle with fewer moving parts

3. **Section 2: Error Handling**:
   - "Use consistent error handling pattern" - Simplified error flow means more predictable handling
   - "Service status reporting" - Clearer status reporting with a unified service

## Implementation Plan

### Service Structure

The new service will follow our `StandardService` template pattern:

```python
class DeepgramDirectMicService(StandardService):
    """
    Service that directly captures and streams microphone audio to Deepgram.
    
    Features:
    - Direct microphone integration using Deepgram's Microphone class
    - Streaming transcription with interim and final results
    - Configurable Deepgram model and language options
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        # Initialize with standard template pattern
        super().__init__(event_bus, config or {}, logger, name="deepgram_direct_mic")
        # ...
```

### Core Components

1. **Service Configuration**:
   - Deepgram API key and transcription options
   - Microphone device configuration
   - Processing options (language, model, etc.)

2. **Client Management**:
   - Deepgram client initialization
   - WebSocket connection handling
   - Event callback setup

3. **Lifecycle Methods**:
   - Proper initialization in `_start()`
   - Clean resource handling in `_stop()`
   - State management throughout

4. **Event Handling**:
   - Voice control event subscriptions
   - Transcript event processing and emission
   - Error handling and reporting

### Event Flow

1. User initiates voice recording (via CLI, push-to-talk, etc.)
2. System emits `VOICE_LISTENING_STARTED` event
3. DeepgramDirectMicService handles event, initializes Deepgram client
4. Service starts the Deepgram microphone capture
5. Deepgram processes audio and returns transcription results
6. Service emits transcription events (interim & final)
7. Other services respond to transcription events

## Key Benefits

1. **Simplified Architecture**:
   - Single service for audio capture and transcription
   - Fewer components to manage and debug
   - Clearer event flow

2. **Improved Reliability**:
   - Reduced thread boundary crossings
   - Fewer race condition opportunities
   - Leverages Deepgram's optimized implementation

3. **Better Performance**:
   - Lower latency with direct streaming
   - Reduced CPU usage
   - Less event bus traffic for audio data

4. **Easier Maintenance**:
   - Less custom code to maintain
   - Better alignment with SDK provider recommendations
   - Clearer debugging and error reporting

## Implementation References

- Deepgram Python SDK Documentation: https://developers.deepgram.com/docs/python-sdk-streaming-transcription
- CantinaOS Architecture Standards: [ARCHITECTURE_STANDARDS.md](../ARCHITECTURE_STANDARDS.md)
- Service Template: [service_template.py](../cantina_os/service_template.py)

## TODO List

1. **Initial Implementation**:
   - [ ] Create `DeepgramDirectMicService` class following service template
   - [ ] Implement configuration loading and validation
   - [ ] Set up Deepgram client and WebSocket connection handling
   - [ ] Implement microphone initialization and management
   - [ ] Add event subscriptions and handlers
   - [ ] Implement proper resource cleanup

2. **Testing**:
   - [ ] Create unit tests for service initialization and configuration
   - [ ] Implement integration tests for event handling
   - [ ] Test microphone access across platforms (macOS, Linux, Windows)
   - [ ] Add test cases for error handling and recovery
   - [ ] Test performance and latency compared to current implementation

3. **Integration**:
   - [ ] Update service registration in main.py
   - [ ] Add configuration options in app_settings.py
   - [ ] Ensure compatibility with existing CLI commands
   - [ ] Test integration with other services (LED indicators, response handling)
   - [ ] Verify proper event flow through the system

4. **Documentation & Cleanup**:
   - [ ] Add detailed code documentation
   - [ ] Update architecture documentation
   - [ ] Create usage examples
   - [ ] Deprecation plan for old services
   - [ ] Update dev log with implementation details

5. **Optimization**:
   - [ ] Add performance metrics for latency tracking
   - [ ] Optimize configuration for different environments
   - [ ] Add fallback mechanisms for Deepgram service interruptions
   - [ ] Explore options for local audio preprocessing if needed 