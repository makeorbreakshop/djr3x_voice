"""
Tests for the MicInputService.

These tests verify the functionality of the microphone input service,
including initialization, audio capture, and event emission.
"""

import asyncio
import pytest
import numpy as np
from unittest.mock import patch

from cantina_os.services.mic_input_service import MicInputService, AudioConfig, AudioChunkPayload
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatus

@pytest.mark.asyncio
async def test_mic_input_service_initialization(event_bus, test_config, mock_sounddevice):
    """Test service initialization."""
    service = MicInputService(event_bus, test_config)
    
    # Verify initial state
    assert service.service_name == "mic_input"
    assert not service.is_started
    assert service.status == ServiceStatus.INITIALIZING
    
    # Start service
    await service.start()
    
    # Verify started state
    assert service.is_started
    assert service.status == ServiceStatus.RUNNING
    
    # Verify stream configuration
    mock_sounddevice["stream"].assert_called_once()
    stream_args = mock_sounddevice["stream"].call_args[1]
    assert stream_args["device"] == test_config["AUDIO_DEVICE_INDEX"]
    assert stream_args["samplerate"] == test_config["AUDIO_SAMPLE_RATE"]
    assert stream_args["channels"] == test_config["AUDIO_CHANNELS"]
    
    # Stop service
    await service.stop()
    assert not service.is_started

@pytest.mark.asyncio
async def test_audio_capture_lifecycle(event_bus, test_config, mock_sounddevice):
    """Test audio capture start/stop cycle."""
    service = MicInputService(event_bus, test_config)
    await service.start()
    
    # Start capture
    await service.start_capture()
    assert service._is_capturing
    mock_sounddevice["stream_instance"].start.assert_called_once()
    
    # Stop capture
    await service.stop_capture()
    assert not service._is_capturing
    mock_sounddevice["stream_instance"].stop.assert_called_once()
    
    await service.stop()

@pytest.mark.asyncio
async def test_audio_event_emission(event_bus, test_config):
    """Test that audio events are emitted correctly."""
    # Create a mock audio callback that simulates audio data
    test_audio = np.zeros((1024, 1), dtype=np.int16)
    received_events = []
    event_received = asyncio.Event()
    
    def collect_event(event):
        received_events.append(event)
        event_received.set()
    
    event_bus.on(EventTopics.AUDIO_RAW_CHUNK, collect_event)
    
    # Mock sounddevice directly for this test
    with patch("sounddevice.InputStream") as mock_stream, \
         patch("sounddevice.query_devices", return_value=[{"name": "Test Device"}]):
        
        # Set up mock stream
        mock_stream_instance = mock_stream.return_value
        mock_stream.return_value = mock_stream_instance
        
        service = MicInputService(event_bus, test_config)
        try:
            await service.start()
            await service.start_capture()
            
            # Simulate audio callback
            service._audio_callback(
                test_audio,
                len(test_audio),
                {"input_buffer_adc_time": 0.0},
                None
            )
            
            # Allow time for the async queue operation to complete
            await asyncio.sleep(0.1)
            
            # Wait for event with timeout
            try:
                await asyncio.wait_for(event_received.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pytest.fail("Timeout waiting for audio event")
            
            # Verify event was emitted
            assert len(received_events) > 0
            event = received_events[0]
            # Access fields directly from the Pydantic model
            assert hasattr(event, 'samples')
            assert hasattr(event, 'sample_rate')
            assert event.sample_rate == test_config["AUDIO_SAMPLE_RATE"]
            
        finally:
            # Ensure cleanup happens even if assertions fail
            await service.stop_capture()
            # Allow time for cleanup to complete
            await asyncio.sleep(0.1)
            await service.stop()

@pytest.mark.asyncio
async def test_error_handling(event_bus, test_config):
    """Test error handling during initialization and capture."""
    with patch("sounddevice.query_devices", return_value=[]):
        service = MicInputService(event_bus, test_config)
        
        # Verify initialization error is handled
        with pytest.raises(ValueError):
            await service.start()
        
        assert service.status == ServiceStatus.ERROR
        
@pytest.mark.asyncio
async def test_config_loading(event_bus):
    """Test configuration loading with defaults."""
    service = MicInputService(event_bus)  # No config provided
    config = service._config
    
    # Verify default values
    assert isinstance(config, AudioConfig)
    assert config.sample_rate == 16000
    assert config.channels == 1
    assert config.dtype == np.int16 