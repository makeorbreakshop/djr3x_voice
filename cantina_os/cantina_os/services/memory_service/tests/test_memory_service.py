"""
Test cases for MemoryService
"""

import asyncio
import pytest
import uuid
from unittest.mock import MagicMock, patch

from cantina_os.services.memory_service.memory_service import MemoryService
from cantina_os.bus import EventTopics
from cantina_os.event_payloads import IntentPayload

@pytest.fixture
def memory_service():
    """Create a MemoryService instance for testing."""
    service = MemoryService()
    # Mock the emit method
    service.emit = MagicMock()
    return service

@pytest.mark.asyncio
async def test_memory_service_initialization(memory_service):
    """Test that MemoryService initializes correctly."""
    # Setup
    memory_service._emit_dict = MagicMock()
    memory_service._emit_status = MagicMock()
    
    # Call
    await memory_service._start()
    
    # Assert
    assert memory_service._loop is not None
    memory_service._emit_status.assert_called_once()
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_set_and_get_values(memory_service):
    """Test setting and getting values from memory."""
    # Setup
    memory_service._emit_dict = MagicMock()
    memory_service._emit_status = MagicMock()
    await memory_service._start()
    
    # Call
    await memory_service.set("test_key", "test_value")
    value = memory_service.get("test_key")
    
    # Assert
    assert value == "test_value"
    memory_service._emit_dict.assert_called_once()
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_append_chat(memory_service):
    """Test appending to chat history."""
    # Setup
    memory_service._emit_dict = MagicMock()
    memory_service._emit_status = MagicMock()
    await memory_service._start()
    
    # Call
    await memory_service.append_chat({"role": "user", "content": "Hello"})
    chat_history = memory_service.get("chat_history")
    
    # Assert
    assert len(chat_history) == 1
    assert chat_history[0]["role"] == "user"
    assert chat_history[0]["content"] == "Hello"
    memory_service._emit_dict.assert_called_once()
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_chat_history_limit(memory_service):
    """Test that chat history is limited to the configured size."""
    # Setup
    memory_service._emit_dict = MagicMock()
    memory_service._emit_status = MagicMock()
    memory_service._config.chat_history_max_turns = 3
    await memory_service._start()
    
    # Call
    for i in range(5):
        await memory_service.append_chat({"role": "user", "content": f"Message {i}"})
    
    chat_history = memory_service.get("chat_history")
    
    # Assert
    assert len(chat_history) == 3
    assert chat_history[0]["content"] == "Message 2"
    assert chat_history[2]["content"] == "Message 4"
    
    # Cleanup
    await memory_service._stop()

@pytest.mark.asyncio
async def test_handle_intent_detected(memory_service):
    """Test handling of intent detection events."""
    # Setup
    memory_service._emit_dict = MagicMock()
    memory_service._emit_status = MagicMock()
    await memory_service._start()
    
    # Create a mock intent payload
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={"genre": "jazz"},
        original_text="Play some jazz music",
        event_id=str(uuid.uuid4()),
        timestamp=123456.789
    ).model_dump()
    
    # Call
    await memory_service._handle_intent_detected(intent_payload)
    
    # Assert
    last_intent = memory_service.get("last_intent")
    assert last_intent["name"] == "play_music"
    assert last_intent["parameters"]["genre"] == "jazz"
    assert last_intent["original_text"] == "Play some jazz music"
    
    chat_history = memory_service.get("chat_history")
    assert len(chat_history) == 1
    assert chat_history[0]["content"] == "Play some jazz music"
    
    # Cleanup
    await memory_service._stop() 