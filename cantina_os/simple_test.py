#!/usr/bin/env python3
"""
Simple test runner for CantinaOS

This script runs a specific test for the MicInputService with minimal dependencies.
"""

import sys
import os
import asyncio
from unittest.mock import patch, Mock
import pytest

# Add src to path
# sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from cantina_os.services.mic_input_service import MicInputService, AudioConfig
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatus
from pyee.asyncio import AsyncIOEventEmitter

async def run_simple_test():
    """Run a simplified test for MicInputService."""
    print("Starting simple test for MicInputService...")
    
    # Create event bus
    event_bus = AsyncIOEventEmitter()
    
    # Create test config
    test_config = {
        "AUDIO_DEVICE_INDEX": 0,
        "AUDIO_SAMPLE_RATE": 16000,
        "AUDIO_CHANNELS": 1,
    }
    
    # Mock sounddevice
    with patch("sounddevice.InputStream") as mock_stream, \
         patch("sounddevice.query_devices") as mock_query:
        
        # Set up mock device list
        mock_query.return_value = [{"name": "Test Device", "max_input_channels": 2}]
        
        # Set up mock stream
        mock_stream_instance = Mock()
        mock_stream.return_value = mock_stream_instance
        
        print("Creating MicInputService...")
        service = MicInputService(event_bus, test_config)
        
        # Verify initial state
        print(f"Initial state - service_name: {service.service_name}")
        print(f"Initial state - is_started: {service.is_started}")
        print(f"Initial state - status: {service.status}")
        
        # Start service
        print("Starting service...")
        await service.start()
        
        # Verify started state
        print(f"Started state - is_started: {service.is_started}")
        print(f"Started state - status: {service.status}")
        
        # Verify stream configuration
        print("Verifying stream configuration...")
        assert mock_stream.called, "InputStream was not created"
        stream_args = mock_stream.call_args[1]
        print(f"Stream args - device: {stream_args['device']}")
        print(f"Stream args - samplerate: {stream_args['samplerate']}")
        print(f"Stream args - channels: {stream_args['channels']}")
        
        # Start capture
        print("Starting audio capture...")
        await service.start_capture()
        assert service._is_capturing, "Capture not started"
        assert mock_stream_instance.start.called, "Stream not started"
        
        # Stop capture
        print("Stopping audio capture...")
        await service.stop_capture()
        assert not service._is_capturing, "Capture not stopped"
        assert mock_stream_instance.stop.called, "Stream not stopped"
        
        # Stop service
        print("Stopping service...")
        await service.stop()
        assert not service.is_started, "Service not stopped"
        
        print("Test completed successfully!")

if __name__ == "__main__":
    asyncio.run(run_simple_test()) 