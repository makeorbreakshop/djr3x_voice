"""
Test suite for IntentRouterService.

This test suite tests the functionality of the IntentRouterService which routes intents 
detected by the GPT service to appropriate hardware commands.
"""

import pytest
from unittest.mock import MagicMock, patch, call
import asyncio

from cantina_os.services.intent_router_service import IntentRouterService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import IntentPayload, MusicCommandPayload, EyeCommandPayload
from cantina_os.base_service import BaseService

@pytest.fixture
def mock_event_bus():
    return MagicMock()

@pytest.fixture
def router_service(mock_event_bus):
    service = IntentRouterService(mock_event_bus)
    service._config = {}
    return service

@pytest.mark.asyncio
async def test_play_music_intent_routing(router_service, mock_event_bus):
    """Test that play_music intent is routed correctly to a music command."""
    # Create intent payload
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={"track": "cantina"},
        original_text="Play the cantina song",
        conversation_id="test_conv_123"
    )
    
    # Call the handler directly
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify correct music command was emitted
    mock_event_bus.emit.assert_called_once()
    args = mock_event_bus.emit.call_args[0]
    assert args[0] == EventTopics.MUSIC_COMMAND
    
    # Check the payload content
    music_payload = args[1]
    assert isinstance(music_payload, dict)
    assert music_payload.get("action") == "play"
    assert music_payload.get("song_query") == "cantina"
    assert music_payload.get("conversation_id") == "test_conv_123"

@pytest.mark.asyncio
async def test_stop_music_intent_routing(router_service, mock_event_bus):
    """Test that stop_music intent is routed correctly to a music command."""
    # Create intent payload
    intent_payload = IntentPayload(
        intent_name="stop_music",
        parameters={},
        original_text="Stop the music",
        conversation_id="test_conv_456"
    )
    
    # Call the handler
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify correct music command was emitted
    mock_event_bus.emit.assert_called_once()
    args = mock_event_bus.emit.call_args[0]
    assert args[0] == EventTopics.MUSIC_COMMAND
    
    # Check the payload content
    music_payload = args[1]
    assert isinstance(music_payload, dict)
    assert music_payload.get("action") == "stop"
    assert music_payload.get("conversation_id") == "test_conv_456"

@pytest.mark.asyncio
async def test_set_eye_color_intent_routing(router_service, mock_event_bus):
    """Test that set_eye_color intent is routed correctly to an eye command."""
    # Create intent payload with all parameters
    intent_payload = IntentPayload(
        intent_name="set_eye_color",
        parameters={"color": "blue", "pattern": "pulse", "intensity": 0.8},
        original_text="Change your eyes to blue",
        conversation_id="test_conv_789"
    )
    
    # Call the handler
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify correct eye command was emitted
    mock_event_bus.emit.assert_called_once()
    args = mock_event_bus.emit.call_args[0]
    assert args[0] == EventTopics.EYE_COMMAND
    
    # Check the payload content
    eye_payload = args[1]
    assert isinstance(eye_payload, dict)
    assert eye_payload.get("color") == "blue"
    assert eye_payload.get("pattern") == "pulse"
    assert eye_payload.get("intensity") == 0.8
    assert eye_payload.get("conversation_id") == "test_conv_789"

@pytest.mark.asyncio
async def test_set_eye_color_with_missing_pattern(router_service, mock_event_bus):
    """Test that set_eye_color uses default pattern when not provided."""
    # Create intent payload with missing pattern
    intent_payload = IntentPayload(
        intent_name="set_eye_color",
        parameters={"color": "red", "intensity": 0.5},
        original_text="Make your eyes red",
        conversation_id="test_conv_101"
    )
    
    # Call the handler
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify correct eye command was emitted
    mock_event_bus.emit.assert_called_once()
    args = mock_event_bus.emit.call_args[0]
    assert args[0] == EventTopics.EYE_COMMAND
    
    # Check the payload content - should have "solid" as default pattern
    eye_payload = args[1]
    assert isinstance(eye_payload, dict)
    assert eye_payload.get("color") == "red"
    assert eye_payload.get("pattern") == "solid"
    assert eye_payload.get("intensity") == 0.5

@pytest.mark.asyncio
async def test_unknown_intent_handling(router_service, mock_event_bus):
    """Test that unknown intents are handled gracefully."""
    # Create payload with unknown intent
    intent_payload = IntentPayload(
        intent_name="unknown_function",
        parameters={"param1": "value1"},
        original_text="Do something weird",
        conversation_id="test_conv_202"
    )
    
    # Call the handler
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify no events were emitted for unknown intent
    mock_event_bus.emit.assert_not_called()

@pytest.mark.asyncio
async def test_missing_required_parameters(router_service, mock_event_bus):
    """Test that intents with missing required parameters are handled gracefully."""
    # Create payload with missing required parameter (track)
    intent_payload = IntentPayload(
        intent_name="play_music",
        parameters={},  # Missing track parameter
        original_text="Play some music",
        conversation_id="test_conv_303"
    )
    
    # Call the handler
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify no events were emitted due to missing required parameter
    mock_event_bus.emit.assert_not_called()

@pytest.mark.asyncio
async def test_set_eye_color_missing_color(router_service, mock_event_bus):
    """Test that set_eye_color with missing color is handled gracefully."""
    # Create payload with missing required color parameter
    intent_payload = IntentPayload(
        intent_name="set_eye_color",
        parameters={"pattern": "blink", "intensity": 0.9},  # Missing color parameter
        original_text="Make your eyes blink",
        conversation_id="test_conv_404"
    )
    
    # Call the handler
    await router_service._handle_intent(intent_payload.dict())
    
    # Verify no events were emitted due to missing required parameter
    mock_event_bus.emit.assert_not_called()

@pytest.mark.asyncio
async def test_event_subscription(router_service, mock_event_bus):
    """Test that the service subscribes to INTENT_DETECTED events."""
    # Setup subscriptions
    with patch.object(BaseService, 'subscribe', autospec=True) as mock_subscribe:
        await router_service._setup_subscriptions()
        
        # Verify subscription was made via BaseService.subscribe
        mock_subscribe.assert_called_once_with(
            router_service,
            EventTopics.INTENT_DETECTED,
            router_service._handle_intent
        ) 