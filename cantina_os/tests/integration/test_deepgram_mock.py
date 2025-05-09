"""Integration tests for the Deepgram mock service."""
import pytest
import asyncio
from typing import Dict, Any
from ..mocks.deepgram_mock import DeepgramMock

@pytest.mark.asyncio
async def test_deepgram_mock_lifecycle(deepgram_mock: DeepgramMock):
    """Test the basic lifecycle of the Deepgram mock service."""
    assert deepgram_mock.is_initialized
    assert not deepgram_mock.websocket_connected
    assert not deepgram_mock.streaming_active
    
    # Test connection
    await deepgram_mock.connect()
    assert deepgram_mock.websocket_connected
    
    # Test disconnection
    await deepgram_mock.disconnect()
    assert not deepgram_mock.websocket_connected

@pytest.mark.asyncio
async def test_deepgram_mock_streaming(configured_deepgram_mock: DeepgramMock):
    """Test streaming transcription functionality."""
    transcripts = []
    
    def on_transcript(data: Dict[str, Any]):
        transcripts.append(data)
        
    configured_deepgram_mock.on_transcript(on_transcript)
    await configured_deepgram_mock.start_streaming()
    
    # Wait for some transcripts
    await asyncio.sleep(0.3)  # Should get about 3 transcripts
    
    await configured_deepgram_mock.stop_streaming()
    
    assert len(transcripts) > 0
    assert all(isinstance(t, dict) for t in transcripts)
    assert all('channel' in t for t in transcripts)

@pytest.mark.asyncio
async def test_deepgram_mock_error_handling(configured_deepgram_mock: DeepgramMock):
    """Test error handling in the mock service."""
    errors = []
    
    def on_error(error_msg: str):
        errors.append(error_msg)
        
    configured_deepgram_mock.on_error(on_error)
    
    # Simulate an error
    test_error = "Test error message"
    configured_deepgram_mock.simulate_error(test_error)
    
    assert len(errors) == 1
    assert errors[0] == test_error

@pytest.mark.asyncio
async def test_deepgram_mock_connection_required(deepgram_mock: DeepgramMock):
    """Test that streaming requires an active connection."""
    with pytest.raises(RuntimeError, match="Must connect before streaming"):
        await deepgram_mock.start_streaming()

@pytest.mark.asyncio
async def test_deepgram_mock_cleanup(configured_deepgram_mock: DeepgramMock):
    """Test proper cleanup of streaming resources."""
    await configured_deepgram_mock.start_streaming()
    assert configured_deepgram_mock.streaming_active
    assert configured_deepgram_mock._transcript_task is not None
    
    await configured_deepgram_mock.stop_streaming()
    assert not configured_deepgram_mock.streaming_active
    assert configured_deepgram_mock._transcript_task is None 