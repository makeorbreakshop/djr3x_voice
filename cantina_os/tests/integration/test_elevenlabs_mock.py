"""Integration tests for the ElevenLabs mock service."""
import pytest
import asyncio
from typing import Dict, Any
from ..mocks.elevenlabs_mock import ElevenLabsMock

@pytest.mark.asyncio
async def test_elevenlabs_mock_lifecycle(elevenlabs_mock: ElevenLabsMock):
    """Test the basic lifecycle of the ElevenLabs mock service."""
    assert elevenlabs_mock.is_initialized
    assert not elevenlabs_mock.is_playing
    
    # Test temporary directory creation
    assert elevenlabs_mock._temp_dir is not None
    
    # Test cleanup
    await elevenlabs_mock.shutdown()
    assert not elevenlabs_mock.is_initialized

@pytest.mark.asyncio
async def test_elevenlabs_mock_speech_generation(
    configured_elevenlabs_mock: ElevenLabsMock,
    sample_audio_data: bytes
):
    """Test speech generation functionality."""
    text = "Hello, I am DJ R3X!"
    voice_id = "test_voice"
    
    audio_data, mime_type = await configured_elevenlabs_mock.generate_speech(
        text=text,
        voice_id=voice_id
    )
    
    assert audio_data == sample_audio_data
    assert mime_type == "audio/wav"
    
    # Verify call was recorded
    calls = configured_elevenlabs_mock.calls.get('generate_speech', [])
    assert len(calls) == 1
    assert calls[0]['args'][0] == text  # First arg is text
    assert calls[0]['args'][1] == voice_id  # Second arg is voice_id

@pytest.mark.asyncio
async def test_elevenlabs_mock_audio_playback(configured_elevenlabs_mock: ElevenLabsMock):
    """Test audio playback functionality."""
    start_called = False
    complete_called = False
    
    def on_start():
        nonlocal start_called
        start_called = True
        
    def on_complete():
        nonlocal complete_called
        complete_called = True
        
    configured_elevenlabs_mock.on_audio_start(on_start)
    configured_elevenlabs_mock.on_audio_complete(on_complete)
    
    # Start playback
    audio_data = b'RIFF' + b'\x00' * 100  # Minimal audio data
    playback_task = asyncio.create_task(configured_elevenlabs_mock.play_audio(audio_data))
    
    # Wait for playback to start
    assert await configured_elevenlabs_mock.wait_for_playback_start()
    assert configured_elevenlabs_mock.is_playing
    assert start_called
    
    # Wait for playback to complete
    await playback_task
    assert not configured_elevenlabs_mock.is_playing
    assert complete_called

@pytest.mark.asyncio
async def test_elevenlabs_mock_playback_interruption(configured_elevenlabs_mock: ElevenLabsMock):
    """Test interrupting audio playback."""
    complete_called = False
    
    def on_complete():
        nonlocal complete_called
        complete_called = True
        
    configured_elevenlabs_mock.on_audio_complete(on_complete)
    
    # Start long playback
    audio_data = b'RIFF' + b'\x00' * 44100  # ~1 second of audio
    playback_task = asyncio.create_task(configured_elevenlabs_mock.play_audio(audio_data))
    
    # Wait for playback to start
    await asyncio.sleep(0.1)
    assert configured_elevenlabs_mock.is_playing
    
    # Stop playback
    await configured_elevenlabs_mock.stop_playback()
    assert not configured_elevenlabs_mock.is_playing
    assert complete_called
    
    # Ensure task is cleaned up
    await playback_task

@pytest.mark.asyncio
async def test_elevenlabs_mock_text_to_speech(configured_elevenlabs_mock: ElevenLabsMock):
    """Test complete text-to-speech pipeline."""
    events = []
    
    def on_start():
        events.append('start')
        
    def on_complete():
        events.append('complete')
        
    configured_elevenlabs_mock.on_audio_start(on_start)
    configured_elevenlabs_mock.on_audio_complete(on_complete)
    
    # Generate and play speech
    await configured_elevenlabs_mock.text_to_speech(
        text="Hello from DJ R3X!",
        voice_id="test_voice"
    )
    
    assert events == ['start', 'complete']
    assert not configured_elevenlabs_mock.is_playing

@pytest.mark.asyncio
async def test_elevenlabs_mock_error_handling(configured_elevenlabs_mock: ElevenLabsMock):
    """Test error handling in the mock service."""
    errors = []
    
    def on_error(error_msg: str):
        errors.append(error_msg)
        
    configured_elevenlabs_mock.on_error(on_error)
    
    # Simulate an error
    test_error = "Failed to generate speech"
    configured_elevenlabs_mock.simulate_error(test_error)
    
    assert len(errors) == 1
    assert errors[0] == test_error

@pytest.mark.asyncio
async def test_elevenlabs_mock_cleanup(configured_elevenlabs_mock: ElevenLabsMock):
    """Test proper cleanup of resources."""
    # Start playback
    audio_data = b'RIFF' + b'\x00' * 44100
    playback_task = asyncio.create_task(configured_elevenlabs_mock.play_audio(audio_data))
    
    # Wait for playback to start
    await asyncio.sleep(0.1)
    assert configured_elevenlabs_mock.is_playing
    
    # Shutdown should stop playback and clean up
    await configured_elevenlabs_mock.shutdown()
    assert not configured_elevenlabs_mock.is_playing
    assert configured_elevenlabs_mock._playback_task is None
    
    # Ensure task is cleaned up
    await playback_task 