"""
Test suite for GPTService function calling capabilities.

This test suite tests the function calling capabilities of the GPTService,
focusing on intent detection and emission of INTENT_DETECTED events.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock, call
import json
import asyncio

from cantina_os.services.gpt_service import GPTService
from cantina_os.event_topics import EventTopics
from cantina_os.llm.command_functions import get_all_function_definitions

@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus

@pytest.fixture
def gpt_service(mock_event_bus):
    service = GPTService(mock_event_bus)
    service._config = {
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
    # Set the current conversation ID
    service._current_conversation_id = "test_conversation_id"
    return service

@pytest.mark.asyncio
async def test_process_tool_calls_valid_play_music(gpt_service, mock_event_bus):
    """Test processing of valid play_music function call."""
    # Mock tool call with valid play_music intent
    tool_call = {
        "id": "call_123",
        "type": "function",
        "function": {
            "name": "play_music",
            "arguments": json.dumps({"track": "cantina"})
        }
    }
    
    # Test processing of the tool call
    await gpt_service._process_tool_calls([tool_call], "Play the cantina song")
    
    # Check that the correct event was emitted
    mock_event_bus.emit.assert_called()
    args = mock_event_bus.emit.call_args_list[0][0]
    assert args[0] == EventTopics.INTENT_DETECTED
    assert args[1].get("intent_name") == "play_music"
    assert args[1].get("parameters") == {"track": "cantina"}
    assert args[1].get("original_text") == "Play the cantina song"
    assert args[1].get("conversation_id") == "test_conversation_id"

@pytest.mark.asyncio
async def test_process_tool_calls_valid_stop_music(gpt_service, mock_event_bus):
    """Test processing of valid stop_music function call."""
    # Mock tool call with valid stop_music intent
    tool_call = {
        "id": "call_456",
        "type": "function",
        "function": {
            "name": "stop_music",
            "arguments": json.dumps({})
        }
    }
    
    # Test processing of the tool call
    await gpt_service._process_tool_calls([tool_call], "Stop the music")
    
    # Check that the correct event was emitted
    mock_event_bus.emit.assert_called()
    args = mock_event_bus.emit.call_args_list[0][0]
    assert args[0] == EventTopics.INTENT_DETECTED
    assert args[1].get("intent_name") == "stop_music"
    assert args[1].get("parameters") == {}
    assert args[1].get("original_text") == "Stop the music"
    assert args[1].get("conversation_id") == "test_conversation_id"

@pytest.mark.asyncio
async def test_process_tool_calls_valid_set_eye_color(gpt_service, mock_event_bus):
    """Test processing of valid set_eye_color function call."""
    # Mock tool call with valid set_eye_color intent
    tool_call = {
        "id": "call_789",
        "type": "function",
        "function": {
            "name": "set_eye_color",
            "arguments": json.dumps({
                "color": "blue", 
                "pattern": "pulse", 
                "intensity": 0.8
            })
        }
    }
    
    # Test processing of the tool call
    await gpt_service._process_tool_calls([tool_call], "Change your eyes to blue")
    
    # Check that the correct event was emitted
    mock_event_bus.emit.assert_called()
    args = mock_event_bus.emit.call_args_list[0][0]
    assert args[0] == EventTopics.INTENT_DETECTED
    assert args[1].get("intent_name") == "set_eye_color"
    assert args[1].get("parameters") == {
        "color": "blue", 
        "pattern": "pulse", 
        "intensity": 0.8
    }
    assert args[1].get("original_text") == "Change your eyes to blue"
    assert args[1].get("conversation_id") == "test_conversation_id"

@pytest.mark.asyncio
async def test_process_tool_calls_invalid_parameters(gpt_service, mock_event_bus):
    """Test processing of function call with invalid parameters."""
    # Mock tool call with invalid eye color parameters
    tool_call = {
        "id": "call_invalid",
        "type": "function",
        "function": {
            "name": "set_eye_color",
            "arguments": json.dumps({
                "color": "ultraviolet",  # Valid but might be restricted
                "intensity": 2.5  # Invalid: above max of 1.0
            })
        }
    }
    
    # Test that invalid parameters are handled properly
    await gpt_service._process_tool_calls([tool_call], "Make your eyes ultraviolet")
    
    # Check that no INTENT_DETECTED event was emitted due to validation failure
    intent_calls = [
        call for call in mock_event_bus.emit.call_args_list 
        if call[0][0] == EventTopics.INTENT_DETECTED
    ]
    assert len(intent_calls) == 0

@pytest.mark.asyncio
async def test_process_tool_calls_multiple_calls(gpt_service, mock_event_bus):
    """Test processing of multiple function calls in single response."""
    # Mock multiple tool calls
    tool_calls = [
        {
            "id": "call_multi_1",
            "type": "function",
            "function": {
                "name": "set_eye_color",
                "arguments": json.dumps({"color": "red"})
            }
        },
        {
            "id": "call_multi_2",
            "type": "function",
            "function": {
                "name": "play_music",
                "arguments": json.dumps({"track": "imperial march"})
            }
        }
    ]
    
    # Process multiple tool calls
    await gpt_service._process_tool_calls(tool_calls, "Make your eyes red and play the imperial march")
    
    # Check that both events were emitted in order
    assert mock_event_bus.emit.call_count == 2
    
    # Check first call - set_eye_color
    args1 = mock_event_bus.emit.call_args_list[0][0]
    assert args1[0] == EventTopics.INTENT_DETECTED
    assert args1[1].get("intent_name") == "set_eye_color"
    assert args1[1].get("parameters") == {"color": "red"}
    
    # Check second call - play_music
    args2 = mock_event_bus.emit.call_args_list[1][0]
    assert args2[0] == EventTopics.INTENT_DETECTED
    assert args2[1].get("intent_name") == "play_music"
    assert args2[1].get("parameters") == {"track": "imperial march"}

@pytest.mark.asyncio
async def test_process_tool_calls_malformed_json(gpt_service, mock_event_bus):
    """Test processing of function call with malformed JSON."""
    # Mock tool call with malformed JSON
    tool_call = {
        "id": "call_malformed",
        "type": "function",
        "function": {
            "name": "play_music",
            "arguments": "{track: cantina}" # Missing quotes around property name
        }
    }
    
    # Test handling of malformed JSON
    await gpt_service._process_tool_calls([tool_call], "Play the cantina song")
    
    # Check that no INTENT_DETECTED event was emitted due to JSON parsing error
    intent_calls = [
        call for call in mock_event_bus.emit.call_args_list 
        if call[0][0] == EventTopics.INTENT_DETECTED
    ]
    assert len(intent_calls) == 0

@pytest.mark.asyncio
async def test_process_tool_calls_unknown_function(gpt_service, mock_event_bus):
    """Test processing of call to unknown function."""
    # Mock tool call for unknown function
    tool_call = {
        "id": "call_unknown",
        "type": "function",
        "function": {
            "name": "unknown_function",
            "arguments": json.dumps({"param1": "value1"})
        }
    }
    
    # Test handling of unknown function
    await gpt_service._process_tool_calls([tool_call], "Do something unknown")
    
    # Check that no INTENT_DETECTED event was emitted due to unknown function
    intent_calls = [
        call for call in mock_event_bus.emit.call_args_list 
        if call[0][0] == EventTopics.INTENT_DETECTED
    ]
    assert len(intent_calls) == 0

@pytest.mark.asyncio
async def test_register_command_functions(gpt_service):
    """Test that command functions are registered correctly."""
    # Call the method to register command functions
    gpt_service._register_command_functions()
    
    # Check that tools were registered
    assert len(gpt_service._tool_schemas) > 0
    
    # Check that known functions exist in tool schemas
    function_names = []
    for tool in gpt_service._tool_schemas:
        if isinstance(tool, dict) and "function" in tool:
            function_names.append(tool["function"].get("name"))
    
    assert "play_music" in function_names
    assert "stop_music" in function_names
    assert "set_eye_color" in function_names

@pytest.mark.asyncio
@patch("cantina_os.services.gpt_service.aiohttp.ClientSession")
async def test_emit_llm_response_with_tool_calls(mock_session, gpt_service, mock_event_bus):
    """Test that _emit_llm_response properly handles tool calls."""
    # Mock response with tool calls
    response_text = "I'll play that for you!"
    tool_calls = [{
        "id": "call_emit_test",
        "type": "function",
        "function": {
            "name": "play_music",
            "arguments": json.dumps({"track": "cantina"})
        }
    }]
    
    # Call the method
    await gpt_service._emit_llm_response(response_text, tool_calls)
    
    # Check that both events were emitted
    assert mock_event_bus.emit.call_count >= 1
    
    # Check LLM_RESPONSE event
    llm_calls = [
        call for call in mock_event_bus.emit.call_args_list 
        if call[0][0] == EventTopics.LLM_RESPONSE
    ]
    assert len(llm_calls) >= 1
    assert llm_calls[0][0][1].get("text") == "I'll play that for you!" 