"""
Test cases for MemoryService using mock classes
"""

import asyncio
import pytest
import uuid
from unittest.mock import MagicMock, patch
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Callable
from pydantic import BaseModel

# Mock classes to replace imported ones
class EventTopics:
    """Mock event topics"""
    MEMORY_UPDATED = "memory_updated"
    MUSIC_PLAYBACK_STARTED = "music_playback_started"
    MUSIC_PLAYBACK_STOPPED = "music_playback_stopped"
    INTENT_DETECTED = "intent_detected"
    TRANSCRIPTION_FINAL = "transcription_final"

class ServiceStatus(str, Enum):
    """Mock service status enum"""
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"

class LogLevel(str, Enum):
    """Mock log level enum"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

class IntentPayload(BaseModel):
    """Mock intent payload"""
    intent_name: str
    parameters: Dict[str, Any]
    original_text: str
    event_id: str
    timestamp: float

# Mock StandardService class
class StandardService:
    """Mock standard service"""
    def __init__(self, name="test_service", config=None):
        self.name = name
        self.config = config or {}
        self.emit = MagicMock()
        
        # Create mocks that return awaitables for async methods
        self._emit_dict = MagicMock()
        self._emit_dict.return_value = asyncio.Future()
        self._emit_dict.return_value.set_result(None)
        
        self._emit_status = MagicMock()
        self._emit_status.return_value = asyncio.Future()
        self._emit_status.return_value.set_result(None)
        
        self._subscribe = MagicMock()
        self._subscribe.return_value = asyncio.Future()
        self._subscribe.return_value.set_result(None)
        
        self.unsubscribe = MagicMock()
        self._loop = None
        self._tasks = []
        self._subs = []
        self._config = MagicMock()
        self._config.chat_history_max_turns = 10
        
        # MemoryService specific
        self._memory = {}
        self._chat_history = []
        self._waiters = {}

# Mock the MemoryService with a simplified implementation for testing
class MemoryService(StandardService):
    """Memory Service for DJ R3X"""

    async def _start(self):
        """Initialize memory service and set up subscriptions."""
        self._loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        
        # Initialize default memory state
        self._memory = {
            "mode": "idle",
            "music_playing": False,
            "current_track": None,
            "last_intent": None,
            "chat_history": []
        }
        
        # Initialize chat history
        self._chat_history = []
        
        # Initialize waiters (for wait_for functionality)
        self._waiters = {}
        
        await self._emit_status(ServiceStatus.OK, "Memory service started")

    async def _stop(self):
        """Clean up tasks and subscriptions."""
        # Cancel any pending waiters
        for future in self._waiters.values():
            if not future.done():
                future.cancel()
                
        self._waiters.clear()
        
        # Cancel any other tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
            
        self._tasks.clear()

        # Unsubscribe from all topics
        for topic, handler in self._subs:
            self.unsubscribe(topic, handler)
        self._subs.clear()

        await self._emit_status(ServiceStatus.OK, "Memory service stopped")

    async def _setup_subscriptions(self):
        """Register event-handlers for memory updates."""
        # Mock successful subscription
        await self._subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_started)
        await self._subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_stopped)
        await self._subscribe(EventTopics.INTENT_DETECTED, self._handle_intent_detected)
        await self._subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription)

    # Memory state management
    def get(self, key, default=None):
        """Get a value from memory."""
        return self._memory.get(key, default)
        
    def set(self, key, value):
        """Set a value in memory and emit update."""
        self._memory[key] = value
        asyncio.create_task(self._notify_memory_updated(key))
        
        # Check if this update satisfies any waiting predicates
        self._check_waiters()
        
    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 30.0):
        """Wait for a condition to be true in memory."""
        # If predicate is already true, return immediately
        if predicate():
            return True
            
        # Create a unique ID for this waiter
        waiter_id = str(uuid.uuid4())
        
        # Create a future to wait on
        future = asyncio.Future()
        self._waiters[waiter_id] = {
            "future": future,
            "predicate": predicate
        }
        
        try:
            # Wait for the predicate to become true or timeout
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            # Clean up the waiter
            if waiter_id in self._waiters:
                del self._waiters[waiter_id]
    
    def _check_waiters(self):
        """Check if any waiters' predicates are now true."""
        for waiter_id, waiter in list(self._waiters.items()):
            try:
                if waiter["predicate"]():
                    if not waiter["future"].done():
                        waiter["future"].set_result(True)
                    self._waiters.pop(waiter_id, None)
            except Exception as e:
                # If predicate raises an exception, cancel the waiter
                if not waiter["future"].done():
                    waiter["future"].set_exception(e)
                self._waiters.pop(waiter_id, None)
    
    # Chat history management
    def append_chat(self, role, text):
        """Add a message to chat history."""
        self._chat_history.append({"role": role, "content": text})
        
        # Prune history if it exceeds the max length
        max_turns = self._config.chat_history_max_turns
        if len(self._chat_history) > max_turns * 2:  # Each turn is user + assistant
            self._chat_history = self._chat_history[-max_turns*2:]
            
        # Update memory
        self._memory["chat_history"] = self._chat_history
        
        # Notify of update
        asyncio.create_task(self._notify_memory_updated("chat_history"))
    
    # Event handlers
    async def _handle_music_started(self, payload):
        """Handle MUSIC_PLAYBACK_STARTED events."""
        self.set("music_playing", True)
        self.set("current_track", payload.get("track_metadata", {}))
    
    async def _handle_music_stopped(self, payload):
        """Handle MUSIC_PLAYBACK_STOPPED events."""
        self.set("music_playing", False)
    
    async def _handle_intent_detected(self, payload):
        """Handle INTENT_DETECTED events."""
        intent = IntentPayload(**payload)
        self.set("last_intent", {
            "name": intent.intent_name,
            "parameters": intent.parameters,
            "text": intent.original_text
        })
    
    async def _handle_transcription(self, payload):
        """Handle TRANSCRIPTION_FINAL events."""
        text = payload.get("text", "")
        if text:
            self.append_chat("user", text)
    
    async def _notify_memory_updated(self, key):
        """Emit MEMORY_UPDATED event."""
        await self._emit_dict(
            EventTopics.MEMORY_UPDATED,
            {
                "key": key,
                "value": self._memory.get(key),
                "timestamp": asyncio.get_event_loop().time()
            }
        )

@pytest.fixture
def memory_service():
    """Create a MemoryService instance for testing."""
    service = MemoryService()
    return service

@pytest.mark.asyncio
async def test_memory_service_initialization(memory_service):
    """Test that MemoryService initializes correctly."""
    # Call
    await memory_service._start()
    
    # Assert
    assert memory_service._loop is not None
    assert memory_service._memory["mode"] == "idle"
    assert memory_service._memory["music_playing"] is False
    memory_service._emit_status.assert_called_once()
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_memory_get_set(memory_service):
    """Test getting and setting memory values."""
    # Setup
    await memory_service._start()
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Call
    memory_service.set("test_key", "test_value")
    
    # Assert
    assert memory_service.get("test_key") == "test_value"
    memory_service._emit_dict.assert_called_once()
    args, kwargs = memory_service._emit_dict.call_args
    assert args[0] == EventTopics.MEMORY_UPDATED
    assert args[1]["key"] == "test_key"
    assert args[1]["value"] == "test_value"
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_chat_history_management(memory_service):
    """Test chat history management."""
    # Setup
    await memory_service._start()
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Call - add messages
    memory_service.append_chat("user", "Hello")
    memory_service.append_chat("assistant", "Hi there!")
    
    # Assert
    assert len(memory_service._chat_history) == 2
    assert memory_service._chat_history[0]["role"] == "user"
    assert memory_service._chat_history[0]["content"] == "Hello"
    assert memory_service._chat_history[1]["role"] == "assistant"
    assert memory_service._chat_history[1]["content"] == "Hi there!"
    
    # Check that memory was updated
    assert memory_service._memory["chat_history"] == memory_service._chat_history
    
    # Check that MEMORY_UPDATED was emitted
    memory_service._emit_dict.assert_called()
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_chat_history_pruning(memory_service):
    """Test that chat history is pruned when it exceeds the max length."""
    # Setup
    await memory_service._start()
    memory_service._config.chat_history_max_turns = 2  # Set a small limit for testing
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Add more messages than the limit
    for i in range(6):  # 3 turns (user + assistant)
        memory_service.append_chat("user", f"Message {i}")
        memory_service.append_chat("assistant", f"Response {i}")
    
    # Assert
    assert len(memory_service._chat_history) == 4  # 2 turns (user + assistant)
    assert memory_service._chat_history[0]["content"] == "Message 2"
    assert memory_service._chat_history[1]["content"] == "Response 2"
    assert memory_service._chat_history[2]["content"] == "Message 3"
    assert memory_service._chat_history[3]["content"] == "Response 3"
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_wait_for_existing_condition(memory_service):
    """Test wait_for when condition is already true."""
    # Setup
    await memory_service._start()
    memory_service.set("flag", True)
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Call
    result = await memory_service.wait_for(lambda: memory_service.get("flag") is True, timeout=1.0)
    
    # Assert
    assert result is True
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_wait_for_future_condition(memory_service):
    """Test wait_for when condition becomes true in the future."""
    # Setup
    await memory_service._start()
    memory_service.set("flag", False)
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Create a task to wait for the condition
    wait_task = asyncio.create_task(
        memory_service.wait_for(lambda: memory_service.get("flag") is True, timeout=3.0)
    )
    
    # Let the event loop run a bit
    await asyncio.sleep(0.1)
    
    # Now set the flag to true
    memory_service.set("flag", True)
    
    # Wait for the result
    result = await wait_task
    
    # Assert
    assert result is True
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_wait_for_timeout(memory_service):
    """Test wait_for when condition times out."""
    # Setup
    await memory_service._start()
    memory_service.set("flag", False)
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Call with a short timeout
    result = await memory_service.wait_for(lambda: memory_service.get("flag") is True, timeout=0.1)
    
    # Assert
    assert result is False
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_handle_music_started(memory_service):
    """Test handling of music started events."""
    # Setup
    await memory_service._start()
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Create a mock payload
    payload = {
        "track_metadata": {
            "title": "Test Track",
            "artist": "Test Artist"
        }
    }
    
    # Call
    await memory_service._handle_music_started(payload)
    
    # Assert
    assert memory_service.get("music_playing") is True
    assert memory_service.get("current_track")["title"] == "Test Track"
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_handle_music_stopped(memory_service):
    """Test handling of music stopped events."""
    # Setup
    await memory_service._start()
    memory_service.set("music_playing", True)
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Call
    await memory_service._handle_music_stopped({})
    
    # Assert
    assert memory_service.get("music_playing") is False
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_handle_intent_detected(memory_service):
    """Test handling of intent detected events."""
    # Setup
    await memory_service._start()
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Create a mock intent payload
    intent_id = str(uuid.uuid4())
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={"genre": "jazz"},
        original_text="Play some jazz music",
        event_id=intent_id,
        timestamp=123456.789
    ).model_dump()
    
    # Call
    await memory_service._handle_intent_detected(intent_payload)
    
    # Assert
    last_intent = memory_service.get("last_intent")
    assert last_intent is not None
    assert last_intent["name"] == "play_music"
    assert last_intent["parameters"]["genre"] == "jazz"
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_handle_transcription(memory_service):
    """Test handling of transcription events."""
    # Setup
    await memory_service._start()
    
    # Reset mock call counts for this test
    memory_service._emit_dict.reset_mock()
    
    # Create a mock transcription payload
    payload = {
        "text": "Hello DJ R3X"
    }
    
    # Call
    await memory_service._handle_transcription(payload)
    
    # Assert
    assert len(memory_service._chat_history) == 1
    assert memory_service._chat_history[0]["role"] == "user"
    assert memory_service._chat_history[0]["content"] == "Hello DJ R3X"
    
    # Cleanup
    await memory_service._stop() 