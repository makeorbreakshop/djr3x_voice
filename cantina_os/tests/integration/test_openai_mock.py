"""Integration tests for the OpenAI mock service."""
import pytest
import asyncio
from typing import Dict, List, Any
from ..mocks.openai_mock import OpenAIMock

@pytest.mark.asyncio
async def test_openai_mock_lifecycle(openai_mock: OpenAIMock):
    """Test the basic lifecycle of the OpenAI mock service."""
    assert openai_mock.is_initialized
    assert not openai_mock.is_streaming
    
    # Test conversation history
    messages = [{"role": "user", "content": "Hello"}]
    await openai_mock.chat_completion(messages)
    
    history = openai_mock.get_conversation_history()
    assert len(history) == 1
    assert history[0] == messages[0]
    
    # Test conversation clear
    openai_mock.clear_conversation()
    assert len(openai_mock.get_conversation_history()) == 0

@pytest.mark.asyncio
async def test_openai_mock_chat_completion(
    configured_openai_mock: OpenAIMock,
    sample_chat_completion: Dict[str, Any]
):
    """Test non-streaming chat completion."""
    messages = [
        {"role": "system", "content": "You are DJ R3X"},
        {"role": "user", "content": "Hello!"}
    ]
    
    response = await configured_openai_mock.chat_completion(messages, stream=False)
    assert response == sample_chat_completion
    
    history = configured_openai_mock.get_conversation_history()
    assert len(history) == 2
    assert all(msg in history for msg in messages)

@pytest.mark.asyncio
async def test_openai_mock_streaming(
    configured_openai_mock: OpenAIMock,
    sample_streaming_response: List[Dict[str, Any]]
):
    """Test streaming chat completion."""
    chunks = []
    
    def on_chunk(data: Dict[str, Any]):
        chunks.append(data)
        
    configured_openai_mock.on_chunk(on_chunk)
    messages = [{"role": "user", "content": "Hello"}]
    
    # Collect streaming response
    async for chunk in await configured_openai_mock.chat_completion(messages, stream=True):
        assert chunk in sample_streaming_response
        
    assert len(chunks) == len(sample_streaming_response)
    assert chunks == sample_streaming_response
    assert not configured_openai_mock.is_streaming

@pytest.mark.asyncio
async def test_openai_mock_function_calls(
    configured_openai_mock: OpenAIMock,
    sample_function_call: Dict[str, Any]
):
    """Test function call handling."""
    function_calls = []
    
    def on_function_call(data: Dict[str, Any]):
        function_calls.append(data)
        
    configured_openai_mock.on_function_call(on_function_call)
    configured_openai_mock.set_response('completion', sample_function_call)
    
    messages = [{"role": "user", "content": "Play the cantina band song"}]
    functions = [{
        "name": "play_music",
        "description": "Play a music track",
        "parameters": {
            "type": "object",
            "properties": {
                "track_name": {"type": "string"},
                "volume": {"type": "number"}
            }
        }
    }]
    
    response = await configured_openai_mock.chat_completion(messages, functions=functions)
    assert response == sample_function_call
    
    # Simulate function call
    configured_openai_mock.simulate_function_call(sample_function_call["choices"][0]["message"]["function_call"])
    assert len(function_calls) == 1
    assert function_calls[0]["name"] == "play_music"

@pytest.mark.asyncio
async def test_openai_mock_error_handling(configured_openai_mock: OpenAIMock):
    """Test error handling in the mock service."""
    errors = []
    
    def on_error(error_msg: str):
        errors.append(error_msg)
        
    configured_openai_mock.on_error(on_error)
    
    # Simulate an error
    test_error = "API rate limit exceeded"
    configured_openai_mock.simulate_error(test_error)
    
    assert len(errors) == 1
    assert errors[0] == test_error

@pytest.mark.asyncio
async def test_openai_mock_streaming_cleanup(configured_openai_mock: OpenAIMock):
    """Test proper cleanup of streaming resources."""
    messages = [{"role": "user", "content": "Hello"}]
    
    # Start streaming
    stream = await configured_openai_mock.chat_completion(messages, stream=True)
    assert configured_openai_mock.is_streaming
    
    # Read first chunk
    chunk = await anext(stream)
    assert chunk is not None
    
    # Stop streaming
    await configured_openai_mock.stop_streaming()
    assert not configured_openai_mock.is_streaming
    
    # Verify stream is closed
    with pytest.raises(StopAsyncIteration):
        await anext(stream) 