"""
Common test fixtures and utilities for CantinaOS tests.

This module provides fixtures that can be used across all test files,
including mocks for external dependencies, event bus setup, and configuration.
"""

import pytest
import asyncio
import logging
from unittest.mock import Mock, patch
from pyee.asyncio import AsyncIOEventEmitter
from typing import AsyncGenerator, Dict, Any, List, Generator
from .mocks.deepgram_mock import DeepgramMock
from .mocks.openai_mock import OpenAIMock
from .mocks.elevenlabs_mock import ElevenLabsMock
import json
import os
import sys

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

# Import test dependencies
from cantina_os.event_bus import EventBus

@pytest.fixture
def event_bus():
    """Create a new event bus for each test."""
    return AsyncIOEventEmitter()

@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    logger = Mock(spec=logging.Logger)
    return logger

@pytest.fixture(scope="session")
def event_loop_policy():
    """Override the default event loop policy for tests."""
    policy = asyncio.DefaultEventLoopPolicy()
    asyncio.set_event_loop_policy(policy)
    return policy

@pytest.fixture
def test_config():
    """Create a test configuration with default values."""
    return {
        # Audio configuration
        "AUDIO_DEVICE_INDEX": 0,
        "AUDIO_SAMPLE_RATE": 16000,
        "AUDIO_CHANNELS": 1,
        "AUDIO_BLOCKSIZE": 1024,
        "AUDIO_LATENCY": 0.1,
        
        # ASR configuration
        "ASR_PROVIDER": "deepgram",
        "DEEPGRAM_MODEL": "nova-3",
        "DEEPGRAM_API_KEY": "mock_api_key",
        "DEEPGRAM_LANGUAGE": "en-US",
        
        # LLM configuration
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-4o",
        "OPENAI_API_KEY": "mock_api_key",
        "MAX_TOKENS": 4000,
        "TEMPERATURE": 0.7,
        
        # TTS configuration
        "ELEVENLABS_VOICE_ID": "mock_voice_id",
        "ELEVENLABS_API_KEY": "mock_api_key",
        "ELEVENLABS_MODEL": "eleven_monolingual_v1",
        
        # System configuration
        "DEFAULT_MODE": "idle",
        "LOG_LEVEL": "DEBUG"
    }

@pytest.fixture(autouse=True)
def mock_external_apis():
    """
    Automatically mock external API clients for all tests.
    This prevents accidental API calls during testing.
    """
    with patch("openai.Client") as mock_openai, \
         patch("deepgram.Deepgram") as mock_deepgram:
        
        # Create a mock for elevenlabs (different approach since it doesn't have Client class)
        mock_elevenlabs = Mock()
        
        with patch.dict("sys.modules", {"elevenlabs": mock_elevenlabs}):
            yield {
                "openai": mock_openai,
                "elevenlabs": mock_elevenlabs,
                "deepgram": mock_deepgram
            }

@pytest.fixture
def mock_sounddevice():
    """Mock sounddevice module for audio testing."""
    with patch("sounddevice.InputStream") as mock_stream, \
         patch("sounddevice.query_devices") as mock_query:
         
        # Set up mock device list
        mock_query.return_value = [
            {"name": "Test Device", "max_input_channels": 2}
        ]
        
        # Set up mock stream
        mock_stream_instance = Mock()
        mock_stream.return_value = mock_stream_instance
        
        yield {
            "stream": mock_stream,
            "stream_instance": mock_stream_instance,
            "query_devices": mock_query
        } 

@pytest.fixture
async def deepgram_mock() -> AsyncGenerator[DeepgramMock, None]:
    """Provide a configured Deepgram mock service."""
    mock = DeepgramMock()
    await mock.initialize()
    yield mock
    await mock.shutdown()
    
@pytest.fixture
def sample_transcript() -> Dict[str, Any]:
    """Provide a sample Deepgram transcript response."""
    return {
        "type": "Results",
        "channel_index": [0],
        "duration": 1.0,
        "start": 0.0,
        "is_final": True,
        "speech_final": True,
        "channel": {
            "alternatives": [{
                "transcript": "Hello, this is a test transcript.",
                "confidence": 0.98
            }]
        }
    }

@pytest.fixture
async def configured_deepgram_mock(
    deepgram_mock: DeepgramMock,
    sample_transcript: Dict[str, Any]
) -> AsyncGenerator[DeepgramMock, None]:
    """Provide a pre-configured Deepgram mock with sample responses."""
    await deepgram_mock.connect()
    deepgram_mock.set_response('transcript', sample_transcript)
    yield deepgram_mock
    await deepgram_mock.disconnect()

@pytest.fixture
async def openai_mock() -> AsyncGenerator[OpenAIMock, None]:
    """Provide a configured OpenAI mock service."""
    mock = OpenAIMock()
    await mock.initialize()
    yield mock
    await mock.shutdown()

@pytest.fixture
def sample_chat_completion() -> Dict[str, Any]:
    """Provide a sample GPT chat completion response."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Hello! I'm DJ R3X, your friendly cantina DJ. How can I help you today?"
            }
        }]
    }

@pytest.fixture
def sample_function_call() -> Dict[str, Any]:
    """Provide a sample GPT function call response."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "function_call": {
                    "name": "play_music",
                    "arguments": json.dumps({
                        "track_name": "Cantina Band",
                        "volume": 0.8
                    })
                }
            }
        }]
    }

@pytest.fixture
def sample_streaming_response() -> List[Dict[str, Any]]:
    """Provide a sample GPT streaming response sequence."""
    return [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Hello! "}}]},
        {"choices": [{"delta": {"content": "I'm DJ R3X, "}}]},
        {"choices": [{"delta": {"content": "your friendly "}}]},
        {"choices": [{"delta": {"content": "cantina DJ!"}}]},
        {"choices": [{"delta": {"content": None}, "finish_reason": "stop"}]}
    ]

@pytest.fixture
async def configured_openai_mock(
    openai_mock: OpenAIMock,
    sample_chat_completion: Dict[str, Any],
    sample_streaming_response: List[Dict[str, Any]]
) -> AsyncGenerator[OpenAIMock, None]:
    """Provide a pre-configured OpenAI mock with sample responses."""
    openai_mock.set_response('completion', sample_chat_completion)
    openai_mock.set_response('stream', sample_streaming_response)
    yield openai_mock 

@pytest.fixture
async def elevenlabs_mock(event_bus):
    """Create an ElevenLabs mock service."""
    mock = ElevenLabsMock(event_bus)
    await mock.start()
    yield mock
    await mock.stop()

@pytest.fixture
def sample_audio_data() -> bytes:
    """Provide sample WAV audio data."""
    # 44.1kHz, 16-bit, mono WAV with 1 second of silence
    return (
        b'RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00'
        b'\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00'
        b'data\x00\x00\x00\x00'
    )

@pytest.fixture
async def configured_elevenlabs_mock(
    elevenlabs_mock: ElevenLabsMock,
    sample_audio_data: bytes
) -> AsyncGenerator[ElevenLabsMock, None]:
    """Provide a pre-configured ElevenLabs mock with sample responses."""
    elevenlabs_mock.set_response('audio_data', sample_audio_data)
    yield elevenlabs_mock 

# Configure pytest-asyncio to use function scope by default
def pytest_configure(config):
    """Configure pytest-asyncio defaults."""
    config.option.asyncio_mode = "auto"
    config.option.asyncio_loop_scope = "function" 