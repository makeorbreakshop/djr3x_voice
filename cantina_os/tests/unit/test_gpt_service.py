"""
Unit tests for the GPT Service

This module contains tests for the GPT service which handles interactions with OpenAI's API.
"""

import asyncio
import json
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch

from pyee.asyncio import AsyncIOEventEmitter
import aiohttp
from aioresponses import aioresponses

from cantina_os.services.gpt_service import GPTService, SessionMemory
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    TranscriptionTextPayload,
    ServiceStatus,
    ServiceStatusPayload,
    LLMResponsePayload,
    LogLevel,
    BaseEventPayload
)


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus for testing."""
    bus = AsyncIOEventEmitter()
    return bus


@pytest.fixture
def mock_responses():
    """Create a mock for aiohttp responses using aioresponses."""
    with aioresponses() as m:
        yield m


@pytest.fixture
def test_config():
    """Create a test configuration for the GPT service."""
    return {
        "OPENAI_API_KEY": "test-api-key",
        "GPT_MODEL": "test-model",
        "MAX_TOKENS": 1000,
        "MAX_MESSAGES": 10,
        "TEMPERATURE": 0.7,
        "SYSTEM_PROMPT": "You are a test assistant",
        "STREAMING": True,
        "TIMEOUT": 30,
        "RATE_LIMIT_REQUESTS": 50
    }


@pytest.fixture
async def gpt_service(mock_event_bus, test_config):
    """Create a GPT service instance for testing."""
    service = GPTService(mock_event_bus, test_config)
    yield service
    if service.is_started:
        await service.stop()


class TestSessionMemory:
    """Tests for the SessionMemory class."""
    
    def test_add_message(self):
        """Test adding messages to session memory."""
        memory = SessionMemory(max_tokens=100, max_messages=5)
        
        # Add system prompt
        memory.set_system_prompt("You are a test assistant")
        
        # Add user message
        memory.add_message("user", "Hello")
        assert len(memory.messages) == 1
        assert memory.messages[0].role == "user"
        assert memory.messages[0].content == "Hello"
        
        # Add assistant message
        memory.add_message("assistant", "Hi there!")
        assert len(memory.messages) == 2
        
        # Test token limit by adding large messages
        for i in range(10):
            memory.add_message("user", f"This is a long message {i} " * 10)
            
        # Should only keep max_messages (5)
        assert len(memory.messages) <= 5
        
    def test_get_messages_for_api(self):
        """Test getting messages in API format."""
        memory = SessionMemory()
        memory.set_system_prompt("You are a test assistant")
        memory.add_message("user", "Hello")
        memory.add_message("assistant", "Hi there!")
        
        messages = memory.get_messages_for_api()
        
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a test assistant"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["content"] == "Hi there!"


class TestGPTService:
    """Tests for the GPT service."""
    
    async def test_initialization(self, gpt_service):
        """Test service initialization."""
        await gpt_service.start()
        assert gpt_service.is_started
        assert gpt_service._memory is not None
        
    async def test_handle_transcription(self, gpt_service, mock_event_bus):
        """Test handling transcription events."""
        await gpt_service.start()
        
        # Create a transcription payload
        transcription = TranscriptionTextPayload(
            text="Hello, test",
            source="deepgram",
            is_final=True,
            conversation_id="test-convo-id"
        )
        
        # Create a future to track when the event is handled
        event_handled = asyncio.Future()
        
        # Mock the _process_with_gpt method
        async def mock_process(*args, **kwargs):
            event_handled.set_result(True)
            
        # Patch the method in the GPT service
        original_process = gpt_service._process_with_gpt
        gpt_service._process_with_gpt = mock_process
        
        # Explicitly set up the event subscription
        gpt_service.subscribe(EventTopics.TRANSCRIPTION_FINAL, gpt_service._handle_transcription)
        
        # Emit the event
        mock_event_bus.emit(
            EventTopics.TRANSCRIPTION_FINAL,
            transcription.model_dump()
        )
        
        # Wait for the event to be handled with a timeout
        try:
            await asyncio.wait_for(event_handled, timeout=1.0)
        except asyncio.TimeoutError:
            # Restore the original method before failing
            gpt_service._process_with_gpt = original_process
            pytest.fail("Event was not handled within timeout")
        
        # Restore the original method
        gpt_service._process_with_gpt = original_process
        
        # Check that conversation ID was set
        assert gpt_service._current_conversation_id == "test-convo-id"
        
        # Check that message was added to memory
        user_messages = [msg for msg in gpt_service._memory.messages if msg.role == "user"]
        assert len(user_messages) == 1
        assert user_messages[0].content == "Hello, test"
    
    @patch('cantina_os.services.gpt_service.GPTService._stream_gpt_response')    
    async def test_process_with_gpt_streaming(self, mock_stream, gpt_service):
        """Test processing user input with GPT in streaming mode."""
        await gpt_service.start()
        
        # Mock the streaming method
        async def simulated_streaming(*args, **kwargs):
            # Simulate streaming process
            await asyncio.sleep(0.1)
            # Emit stream chunk - this will also add message to memory
            await gpt_service._emit_llm_stream_chunk(
                "This is a test response",
                is_complete=True
            )
            
        mock_stream.side_effect = simulated_streaming
        
        # Clear memory first to ensure a clean state
        gpt_service._memory.clear()
        
        # Process a test input
        await gpt_service._process_with_gpt("Test input")
        
        # Check that streaming method was called
        mock_stream.assert_called_once()
        
        # Check memory has the expected messages
        user_messages = [msg for msg in gpt_service._memory.messages if msg.role == "user"]
        assistant_messages = [msg for msg in gpt_service._memory.messages if msg.role == "assistant"]
        
        assert len(user_messages) == 1
        assert user_messages[0].content == "Test input"
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == "This is a test response"
        
    @patch('cantina_os.services.gpt_service.GPTService._get_gpt_response')
    async def test_process_with_gpt_non_streaming(self, mock_get_response, gpt_service):
        """Test processing user input with GPT in non-streaming mode."""
        # Configure service for non-streaming
        gpt_service._config["STREAMING"] = False
        await gpt_service.start()
        
        # Mock the non-streaming method
        async def simulated_get_response(*args, **kwargs):
            # Simulate API call
            await asyncio.sleep(0.1)
            # Emit response - this will also add message to memory
            await gpt_service._emit_llm_response("This is a test response")
            
        mock_get_response.side_effect = simulated_get_response
        
        # Clear memory first to ensure a clean state
        gpt_service._memory.clear()
        
        # Process a test input
        await gpt_service._process_with_gpt("Test input")
        
        # Check that non-streaming method was called
        mock_get_response.assert_called_once()
        
        # Check memory has the expected messages
        user_messages = [msg for msg in gpt_service._memory.messages if msg.role == "user"]
        assistant_messages = [msg for msg in gpt_service._memory.messages if msg.role == "assistant"]
        
        assert len(user_messages) == 1
        assert user_messages[0].content == "Test input"
        assert len(assistant_messages) == 1
        assert assistant_messages[0].content == "This is a test response"
        
    async def test_register_tool(self, gpt_service):
        """Test registering a tool."""
        await gpt_service.start()
        
        tool_schema = {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_param": {
                        "type": "string"
                    }
                }
            }
        }
        
        gpt_service.register_tool(tool_schema)
        
        assert "test_tool" in gpt_service._tools
        assert len(gpt_service._tool_schemas) == 1
        assert gpt_service._tool_schemas[0] == tool_schema
        
    async def test_reset_conversation(self, gpt_service):
        """Test resetting conversation state."""
        await gpt_service.start()
        
        # Add some messages
        gpt_service._memory.add_message("user", "Hello")
        gpt_service._memory.add_message("assistant", "Hi there!")
        
        # Reset conversation
        await gpt_service.reset_conversation()
        
        # Check that memory was cleared and system prompt was added
        assert len(gpt_service._memory.messages) == 1  # Only system prompt
        assert gpt_service._memory.messages[0].role == "system"
        assert gpt_service._memory.messages[0].content == gpt_service._config["SYSTEM_PROMPT"]
        
        # Check that conversation ID was updated
        assert gpt_service._current_conversation_id is not None
        
    async def test_emit_llm_stream_chunk(self, gpt_service, mock_event_bus):
        """Test emitting LLM stream chunks."""
        await gpt_service.start()
        
        # Set up a message collector
        assistant_messages = []
        
        @mock_event_bus.on(EventTopics.LLM_RESPONSE)
        def collect_message(payload):
            message = LLMResponsePayload(**payload)
            assistant_messages.append(message)
            
        # Create a conversation ID
        test_conversation_id = "test-convo-id"
        gpt_service._current_conversation_id = test_conversation_id
        
        # Mock the self.emit method to collect calls
        original_emit = gpt_service.emit
        
        async def mock_emit(topic, payload):
            await original_emit(topic, payload)
            
        gpt_service.emit = mock_emit
            
        # Test emitting a stream chunk
        await gpt_service._emit_llm_stream_chunk(
            "This is a test chunk", 
            is_complete=True
        )
        
        # Give event handler time to process
        await asyncio.sleep(0.1)
        
        # Check that message was emitted correctly
        assert len(assistant_messages) == 1
        assert assistant_messages[0].text == "This is a test chunk"
        assert assistant_messages[0].is_complete is True
        assert assistant_messages[0].conversation_id == test_conversation_id
        
    async def test_rate_limiting(self, gpt_service):
        """Test rate limiting."""
        await gpt_service.start()
        
        # Set a low rate limit for testing
        gpt_service._max_requests_per_window = 2
        
        # Make requests up to the limit
        for _ in range(2):
            gpt_service._request_timestamps.append(time.time())
            
        # Next request should raise rate limit error
        with pytest.raises(Exception) as exc_info:
            await gpt_service._process_with_gpt("Test input")
            
        assert "Rate limit exceeded" in str(exc_info.value)
        
    async def test_error_handling(self, gpt_service, mock_event_bus):
        """Test error handling in the service."""
        await gpt_service.start()
        
        # Set up status handler to detect error status
        status_future = asyncio.Future()
        
        @mock_event_bus.on(EventTopics.SERVICE_STATUS_UPDATE)
        def status_handler(payload):
            status_data = ServiceStatusPayload(**payload)
            if status_data.status == ServiceStatus.ERROR:
                status_future.set_result(True)
                
        # Force an error in the service
        try:
            # This should cause an error
            await gpt_service._process_with_gpt("Test input")
        except Exception:
            pass  # We expect an exception
            
        try:
            # Wait for error status to be emitted
            status = await asyncio.wait_for(status_future, timeout=1.0)
            assert status is True
        except asyncio.TimeoutError:
            pytest.fail("Error status was not emitted")
            
    async def test_tool_handling(self, gpt_service, mock_event_bus):
        """Test tool handling capabilities."""
        await gpt_service.start()
        
        # Register a test tool
        tool_schema = {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string"}
                }
            }
        }
        gpt_service.register_tool(tool_schema)
        
        # Reset conversation
        await gpt_service.reset_conversation()
        
        # Set up response collector
        assistant_messages = []
        
        @mock_event_bus.on(EventTopics.LLM_RESPONSE)
        def collect_message(payload):
            message = LLMResponsePayload(**payload)
            assistant_messages.append(message)
        
        # Mock the streaming to simulate tool call
        original_stream = gpt_service._stream_gpt_response
        
        async def simulated_streaming(*args, **kwargs):
            # Create a fake tool call
            tool_call = {
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "arguments": '{"param1": "test value"}'
                }
            }
            
            # Emit the tool call response
            await gpt_service._emit_llm_stream_chunk(
                "Using the test tool",
                tool_calls=[tool_call],
                is_complete=True
            )
            
        # Replace the streaming method
        gpt_service._stream_gpt_response = simulated_streaming
        
        # Process input that should trigger a tool call
        await gpt_service._process_with_gpt("Use the test tool")
        
        # Give event handlers time to process
        await asyncio.sleep(0.1)
        
        # Check that a tool call was emitted
        assert len(assistant_messages) >= 1
        assert assistant_messages[0].text == "Using the test tool"
        assert len(assistant_messages[0].tool_calls) == 1
        assert assistant_messages[0].tool_calls[0]["function"]["name"] == "test_tool"
        
    async def test_conversation_persistence(self, gpt_service, mock_event_bus):
        """Test conversation persistence across messages."""
        await gpt_service.start()
        
        # Reset conversation to get a clean slate
        await gpt_service.reset_conversation()
        
        # Get the conversation ID
        conv_id = gpt_service._current_conversation_id
        assert conv_id is not None
        
        # Mock the processing method
        original_stream = gpt_service._stream_gpt_response
        
        async def simulated_streaming(*args, **kwargs):
            # Simulate a response
            await gpt_service._emit_llm_stream_chunk(
                "This is a response",
                is_complete=True
            )
            
        gpt_service._stream_gpt_response = simulated_streaming
        
        # Clear memory first to ensure a clean state
        gpt_service._memory.clear()
        
        # Send first message
        await gpt_service._process_with_gpt("First message")
        
        # Check that memory contains the expected messages
        user_messages = [msg for msg in gpt_service._memory.messages if msg.role == "user"]
        assistant_messages = [msg for msg in gpt_service._memory.messages if msg.role == "assistant"]
        
        assert len(user_messages) == 1
        assert user_messages[0].content == "First message"
        assert len(assistant_messages) == 1
        
        # Send second message
        await gpt_service._process_with_gpt("Second message")
        
        # Check that memory now contains all messages
        user_messages = [msg for msg in gpt_service._memory.messages if msg.role == "user"]
        assistant_messages = [msg for msg in gpt_service._memory.messages if msg.role == "assistant"]
        
        assert len(user_messages) == 2
        assert user_messages[1].content == "Second message"
        assert len(assistant_messages) == 2
        
        # Verify conversation ID is maintained
        assert gpt_service._current_conversation_id == conv_id 