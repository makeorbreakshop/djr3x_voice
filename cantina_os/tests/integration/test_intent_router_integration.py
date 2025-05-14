"""
Integration tests for the IntentRouter feature.

These tests verify the end-to-end flow from transcript processing through
intent detection to command emission.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
import json

from cantina_os.event_bus import EventBus
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    TranscriptionTextPayload, 
    IntentPayload,
    MusicCommandPayload,
    EyeCommandPayload
)
from cantina_os.services.gpt_service import GPTService
from cantina_os.services.intent_router_service import IntentRouterService

@pytest.fixture
async def setup_test_environment():
    """Set up the test environment with event bus and services."""
    # Create real event bus for integration testing
    event_bus = EventBus()
    
    # Initialize mocks for monitoring events
    music_command_mock = AsyncMock()
    eye_command_mock = AsyncMock()
    
    # Subscribe mocks to relevant events
    await event_bus.on(EventTopics.MUSIC_COMMAND, music_command_mock)
    await event_bus.on(EventTopics.EYE_COMMAND, eye_command_mock)
    
    # Create the services
    gpt_service = GPTService(event_bus)
    gpt_service._config = {
        "MODEL": "gpt-4",
        "OPENAI_API_KEY": "test-key",
        "MAX_TOKENS": 4000,
        "MAX_MESSAGES": 20,
        "TEMPERATURE": 0.7,
        "SYSTEM_PROMPT": "You are DJ R3X, a helpful Star Wars droid DJ.",
        "TIMEOUT": 30,
        "RATE_LIMIT_REQUESTS": 50,
        "STREAMING": False
    }
    
    router_service = IntentRouterService(event_bus)
    router_service._config = {}
    
    # Properly start both services to ensure full initialization
    await gpt_service._start()
    await router_service._start()
    
    # No need to manually subscribe services now since _start() handles that
    
    # Make OpenAI's function callable for testing
    gpt_service._get_gpt_response = AsyncMock()
    
    yield {
        "event_bus": event_bus,
        "gpt_service": gpt_service,
        "router_service": router_service,
        "music_command_mock": music_command_mock,
        "eye_command_mock": eye_command_mock
    }
    
    # Cleanup
    await gpt_service._stop()
    await router_service._stop()
    event_bus.clear_all_handlers()

@pytest.mark.asyncio
@patch("cantina_os.services.gpt_service.GPTService._process_with_gpt")
async def test_end_to_end_play_music(mock_process_with_gpt, setup_test_environment):
    """Test end-to-end flow for play_music intent."""
    env = setup_test_environment
    
    # Configure mock OpenAI response
    async def simulate_gpt_process(user_input):
        gpt_service = env["gpt_service"]
        
        # Simulate what _process_with_gpt would do
        if not gpt_service._current_conversation_id:
            await gpt_service.reset_conversation()
            
        # Add user message to memory
        gpt_service._memory.add_message("user", user_input)
        
        # Simulate GPT response with tool calls
        tool_calls = [{
            "id": "call_123",
            "type": "function",
            "function": {
                "name": "play_music",
                "arguments": json.dumps({"track": "cantina"})
            }
        }]
        response_text = "I'll play the cantina song for you!"
        
        # Process tool calls and emit LLM response
        await gpt_service._process_tool_calls(tool_calls, response_text)
        await gpt_service._emit_llm_response(response_text, tool_calls)
        
    mock_process_with_gpt.side_effect = simulate_gpt_process
    
    # Emit a transcript event
    await env["event_bus"].emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": "Play the cantina song"})
    
    # Wait for async processing
    await asyncio.sleep(0.3)  # Increased sleep time to ensure async operations complete
    
    # Verify that MUSIC_COMMAND was emitted with the right parameters
    env["music_command_mock"].assert_called_once()
    args = env["music_command_mock"].call_args[0][0]
    assert args.get("action") == "play"
    assert args.get("song_query") == "cantina"

@pytest.mark.asyncio
@patch("cantina_os.services.gpt_service.GPTService._process_with_gpt")
async def test_end_to_end_set_eye_color(mock_process_with_gpt, setup_test_environment):
    """Test end-to-end flow for set_eye_color intent."""
    env = setup_test_environment
    
    # Configure mock OpenAI response
    async def simulate_gpt_process(user_input):
        gpt_service = env["gpt_service"]
        
        # Simulate what _process_with_gpt would do
        if not gpt_service._current_conversation_id:
            await gpt_service.reset_conversation()
            
        # Add user message to memory
        gpt_service._memory.add_message("user", user_input)
        
        # Simulate GPT response with tool calls
        tool_calls = [{
            "id": "call_456",
            "type": "function",
            "function": {
                "name": "set_eye_color",
                "arguments": json.dumps({
                    "color": "blue",
                    "pattern": "pulse",
                    "intensity": 0.9
                })
            }
        }]
        response_text = "Changing my eyes to blue with a pulsing pattern!"
        
        # Process tool calls and emit LLM response
        await gpt_service._process_tool_calls(tool_calls, response_text)
        await gpt_service._emit_llm_response(response_text, tool_calls)
    
    mock_process_with_gpt.side_effect = simulate_gpt_process
    
    # Emit a transcript event
    await env["event_bus"].emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": "Change your eyes to blue and make them pulse"})
    
    # Wait for async processing
    await asyncio.sleep(0.3)  # Increased sleep time to ensure async operations complete
    
    # Verify that EYE_COMMAND was emitted with the right parameters
    env["eye_command_mock"].assert_called_once()
    args = env["eye_command_mock"].call_args[0][0]
    assert args.get("color") == "blue"
    assert args.get("pattern") == "pulse"
    assert args.get("intensity") == 0.9

@pytest.mark.asyncio
@patch("cantina_os.services.gpt_service.GPTService._process_with_gpt")
async def test_end_to_end_multiple_intents(mock_process_with_gpt, setup_test_environment):
    """Test end-to-end flow with multiple intents in single request."""
    env = setup_test_environment
    
    # Configure mock OpenAI response
    async def simulate_gpt_process(user_input):
        gpt_service = env["gpt_service"]
        
        # Simulate what _process_with_gpt would do
        if not gpt_service._current_conversation_id:
            await gpt_service.reset_conversation()
            
        # Add user message to memory
        gpt_service._memory.add_message("user", user_input)
        
        # Simulate GPT response with tool calls
        tool_calls = [
            {
                "id": "call_multi_1",
                "type": "function",
                "function": {
                    "name": "set_eye_color",
                    "arguments": json.dumps({
                        "color": "red",
                        "pattern": "solid",
                        "intensity": 1.0
                    })
                }
            },
            {
                "id": "call_multi_2",
                "type": "function",
                "function": {
                    "name": "play_music",
                    "arguments": json.dumps({
                        "track": "imperial march"
                    })
                }
            }
        ]
        response_text = "I'll change my eyes to red and play the Imperial March!"
        
        # Process tool calls and emit LLM response
        await gpt_service._process_tool_calls(tool_calls, response_text)
        await gpt_service._emit_llm_response(response_text, tool_calls)
    
    mock_process_with_gpt.side_effect = simulate_gpt_process
    
    # Emit a transcript event
    await env["event_bus"].emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": "Make your eyes red and play the Imperial March"})
    
    # Wait for async processing
    await asyncio.sleep(0.3)  # Increased sleep time to ensure async operations complete
    
    # Verify that both commands were emitted
    assert env["eye_command_mock"].call_count == 1
    assert env["music_command_mock"].call_count == 1
    
    # Check eye command
    eye_args = env["eye_command_mock"].call_args[0][0]
    assert eye_args.get("color") == "red"
    assert eye_args.get("pattern") == "solid"
    assert eye_args.get("intensity") == 1.0
    
    # Check music command
    music_args = env["music_command_mock"].call_args[0][0]
    assert music_args.get("action") == "play"
    assert music_args.get("song_query") == "imperial march"

@pytest.mark.asyncio
@patch("cantina_os.services.gpt_service.GPTService._process_with_gpt")
async def test_end_to_end_no_intent(mock_process_with_gpt, setup_test_environment):
    """Test end-to-end flow with no intents detected."""
    env = setup_test_environment
    
    # Configure mock OpenAI response with no tool calls
    async def simulate_gpt_process(user_input):
        gpt_service = env["gpt_service"]
        
        # Simulate what _process_with_gpt would do
        if not gpt_service._current_conversation_id:
            await gpt_service.reset_conversation()
            
        # Add user message to memory
        gpt_service._memory.add_message("user", user_input)
        
        # Simulate response with no tool calls
        response_text = "I'm doing great today! How are you?"
        await gpt_service._emit_llm_response(response_text, None)
    
    mock_process_with_gpt.side_effect = simulate_gpt_process
    
    # Emit a transcript event
    await env["event_bus"].emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": "How are you doing today?"})
    
    # Wait for async processing
    await asyncio.sleep(0.1)
    
    # Verify that no commands were emitted
    env["eye_command_mock"].assert_not_called()
    env["music_command_mock"].assert_not_called()

@pytest.mark.asyncio
@patch("cantina_os.services.gpt_service.GPTService._process_with_gpt")
async def test_intent_routing_with_invalid_parameters(mock_process_with_gpt, setup_test_environment):
    """Test intent routing with invalid parameters."""
    env = setup_test_environment
    
    # Configure mock OpenAI response with invalid parameters
    async def simulate_gpt_process(user_input):
        gpt_service = env["gpt_service"]
        
        # Simulate what _process_with_gpt would do
        if not gpt_service._current_conversation_id:
            await gpt_service.reset_conversation()
            
        # Add user message to memory
        gpt_service._memory.add_message("user", user_input)
        
        # Create tool calls with invalid parameters
        tool_calls = [{
            "id": "call_invalid",
            "type": "function",
            "function": {
                "name": "set_eye_color",
                "arguments": json.dumps({
                    "color": "blue",
                    "intensity": 2.5  # Invalid: above max of 1.0
                })
            }
        }]
        response_text = "Setting your eyes to super bright blue!"
        
        # Process tool calls and emit LLM response
        await gpt_service._process_tool_calls(tool_calls, response_text)
        await gpt_service._emit_llm_response(response_text, tool_calls)
    
    mock_process_with_gpt.side_effect = simulate_gpt_process
    
    # Emit a transcript event
    await env["event_bus"].emit(EventTopics.VOICE_LISTENING_STOPPED, {"transcript": "Make your eyes super bright blue"})
    
    # Wait for async processing
    await asyncio.sleep(0.1)
    
    # Verify that no command was emitted due to validation failure
    env["eye_command_mock"].assert_not_called() 