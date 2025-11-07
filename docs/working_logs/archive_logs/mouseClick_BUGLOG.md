# Mouse Click Microphone Control Implementation Plan

## Background
Currently, the CLI interface instructs users to "Type 'rec' to start recording" but this is causing CLI errors as the actual command is "record". We want to replace this text-based interaction with mouse click functionality for a more intuitive user experience - clicking once to start recording and clicking again to stop.

## Architecture Requirements
- Follow `ARCHITECTURE_STANDARDS.md` for service structure
- Align with the event-based system outlined in `CantinaOS-Initial Plan.md`
- Use the `service_template.py` pattern for new service creation

## Implementation Plan

### 1. Create MouseInputService
Create a new service based on the `StandardService` template that will:
- Listen for mouse clicks using pynput library
- Toggle recording state on each click
- Emit appropriate events to the event bus

```python
from pynput import mouse
import asyncio
from typing import Dict, Any

from cantina_os.service_template import StandardService
from cantina_os.event_topics import EventTopics

class MouseInputService(StandardService):
    async def _start(self) -> None:
        """Initialize the mouse input service."""
        self.logger.info(f"Starting {self.__class__.__name__}")
        
        # Initialize state
        self._mouse_listener = None
        self._is_recording = False
        
        # Set up mouse listener
        await self._setup_mouse_listener()
        
        # Set up event subscriptions
        await self._setup_subscriptions()
        
        self.logger.info(f"{self.__class__.__name__} started successfully")
    
    async def _setup_mouse_listener(self) -> None:
        """Set up the mouse click listener."""
        def on_click(x, y, button, pressed):
            if button == mouse.Button.left and pressed:
                asyncio.create_task(self._handle_mouse_click())
        
        self._mouse_listener = mouse.Listener(on_click=on_click)
        self._mouse_listener.start()
        self.logger.info("Mouse listener initialized")
    
    async def _handle_mouse_click(self) -> None:
        """Handle mouse click event and toggle recording state."""
        self._is_recording = not self._is_recording
        
        if self._is_recording:
            self.logger.info("Mouse click detected - starting recording")
            await self.emit(EventTopics.MIC_RECORDING_START, {})
        else:
            self.logger.info("Mouse click detected - stopping recording")
            await self.emit(EventTopics.MIC_RECORDING_STOP, {})
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # No subscriptions needed for this service as it only emits events
        pass
    
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info(f"Stopping {self.__class__.__name__}")
        
        # Stop mouse listener
        if self._mouse_listener:
            self._mouse_listener.stop()
        
        self.logger.info(f"{self.__class__.__name__} stopped successfully")
```

### 2. Update EventTopics
Add new event topics to handle microphone recording control:

```python
class EventTopics(Enum):
    # Existing topics
    # ...
    
    # New topics for mouse click microphone control
    MIC_RECORDING_START = "mic_recording_start"
    MIC_RECORDING_STOP = "mic_recording_stop"
```

### 3. Modify MicInputService
Update the MicInputService to respond to the new recording events:

```python
async def _setup_subscriptions(self) -> None:
    """Set up event subscriptions."""
    # Existing subscriptions
    # ...
    
    # Add new subscriptions for mouse click control
    asyncio.create_task(self.subscribe(
        EventTopics.MIC_RECORDING_START,
        self._handle_recording_start
    ))
    
    asyncio.create_task(self.subscribe(
        EventTopics.MIC_RECORDING_STOP,
        self._handle_recording_stop
    ))
    
async def _handle_recording_start(self, event_data: Dict[str, Any]) -> None:
    """Start recording from the microphone."""
    self.logger.info("Starting microphone recording")
    # Existing recording logic or new implementation
    
async def _handle_recording_stop(self, event_data: Dict[str, Any]) -> None:
    """Stop recording from the microphone."""
    self.logger.info("Stopping microphone recording")
    # Existing stop logic or new implementation
```

### 4. Register the New Service
Update the main.py file to register and start the new service:

```python
async def initialize_services(self):
    # Existing service initialization
    # ...
    
    # Initialize and start the mouse input service
    await self.start_service(
        "mouse_input", 
        MouseInputService(self.event_bus, self.config)
    )
```

### 5. Update User Feedback
Modify CLI output to inform users about the new mouse click functionality:

```python
# Replace this message:
"Type 'rec' to start recording, then 'done' when finished speaking."

# With this message:
"Click once to start recording, then click again to stop."
```

## Dependencies
- pynput library for mouse monitoring: `pip install pynput`

## Implementation Checklist

- [x] Install pynput library (already in requirements.txt)
- [x] Update EventTopics with new event definitions
- [x] Create MouseInputService class
- [x] Modify MicInputService to handle new events
- [x] Update CLI output text to reflect new interaction method
- [x] Update main.py to register the new service
- [ ] Test left-click to start recording
- [ ] Test left-click to stop recording
- [ ] Verify proper event emission and handling
- [x] Add error handling for failed mouse listener initialization
- [x] Ensure proper resource cleanup on service stop

## Potential Issues
1. Mouse clicks might occur from other applications or contexts
2. Need to ensure proper synchronization between mouse events and async processing
3. Mouse listener may need elevated permissions on some operating systems
4. Need to handle edge cases like rapid multiple clicks

## Testing Plan
1. Test in IDLE mode (should not respond to clicks)
2. Test in INTERACTIVE mode (should toggle recording)
3. Test with rapid clicks to ensure robustness
4. Test proper cleanup and resource management when stopping service 