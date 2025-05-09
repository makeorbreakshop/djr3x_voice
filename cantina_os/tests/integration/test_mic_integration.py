"""
Integration tests for the MicInputService.

These tests verify the MicInputService's integration with other components
and the event bus system.
"""

import asyncio
import pytest
import numpy as np
from unittest.mock import patch

from cantina_os.services.mic_input_service import MicInputService, AudioChunkPayload
from cantina_os.event_topics import EventTopics
from cantina_os.base_service import BaseService
from cantina_os.event_payloads import ServiceStatusPayload

class MockConsumerService(BaseService):
    """
    Mock service that consumes audio events for testing.
    
    This service subscribes to AUDIO_RAW_CHUNK events and records them
    for verification in tests.
    """
    
    def __init__(self, event_bus):
        """Initialize the mock consumer service."""
        super().__init__("mock_consumer", event_bus)
        self.received_audio_chunks = []
        
    async def _initialize(self) -> None:
        """Initialize the service."""
        pass
        
    def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        self.subscribe(
            EventTopics.AUDIO_RAW_CHUNK,
            self._handle_audio_chunk
        )
        self.subscribe(
            EventTopics.SERVICE_STATUS_UPDATE,
            self._handle_status_update
        )
        
    async def _handle_audio_chunk(self, payload) -> None:
        """Handle audio chunk events."""
        self.received_audio_chunks.append(payload)
        
    async def _handle_status_update(self, payload) -> None:
        """Handle service status events."""
        # Just for tracking status updates
        pass

@pytest.mark.asyncio
async def test_mic_integration_with_consumer(event_bus, test_config):
    """Test that MicInputService properly integrates with a consumer service."""
    # Create the mock services
    with patch("sounddevice.InputStream"), \
         patch("sounddevice.query_devices", return_value=[{"name": "Test Device"}]):
        
        mic_service = MicInputService(event_bus, test_config)
        consumer_service = MockConsumerService(event_bus)
        
        # Start both services
        await mic_service.start()
        await consumer_service.start()
        
        # Start audio capture
        await mic_service.start_capture()
        
        # Create test audio data
        test_audio = np.zeros((1024, 1), dtype=np.int16)
        
        # Simulate audio callback
        mic_service._audio_callback(
            test_audio,
            len(test_audio),
            {"input_buffer_adc_time": 0.0},
            None
        )
        
        # Give the event loop time to process events
        await asyncio.sleep(0.1)
        
        # Verify consumer received the audio chunks
        assert len(consumer_service.received_audio_chunks) > 0
        chunk = consumer_service.received_audio_chunks[0]
        assert "samples" in chunk
        assert "sample_rate" in chunk
        assert chunk["sample_rate"] == test_config["AUDIO_SAMPLE_RATE"]
        
        # Stop services
        await mic_service.stop()
        await consumer_service.stop()

@pytest.mark.asyncio
async def test_status_propagation(event_bus, test_config):
    """Test that service status events are properly propagated through the event bus."""
    # Track status events
    status_events = []
    
    def collect_status(event):
        status_events.append(event)
    
    event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, collect_status)
    
    # Create and start the service
    with patch("sounddevice.InputStream"), \
         patch("sounddevice.query_devices", return_value=[{"name": "Test Device"}]):
        
        mic_service = MicInputService(event_bus, test_config)
        await mic_service.start()
        
        # Wait for events to be processed
        await asyncio.sleep(0.1)
        
        # Verify status events were emitted
        assert len(status_events) >= 2  # At least INITIALIZING and RUNNING
        
        # Get the latest status event
        latest_status = status_events[-1]
        assert latest_status["service_name"] == "mic_input"
        assert latest_status["status"] == "RUNNING"
        
        # Stop the service
        await mic_service.stop()
        
        # Wait for events to be processed
        await asyncio.sleep(0.1)
        
        # Verify STOPPED status was emitted
        latest_status = status_events[-1]
        assert latest_status["status"] == "STOPPED" 