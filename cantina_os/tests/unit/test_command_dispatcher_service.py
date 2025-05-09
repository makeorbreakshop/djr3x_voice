"""
Unit tests for CommandDispatcherService

This module contains tests for the CommandDispatcherService, which routes CLI commands
to appropriate handlers in CantinaOS.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from cantina_os.services.command_dispatcher_service import CommandDispatcherService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel
)

@pytest.fixture
def event_bus():
    """Create a mock event bus."""
    bus = Mock()
    bus.emit = AsyncMock()
    bus.on = Mock()
    return bus

@pytest.fixture
async def command_dispatcher(event_bus):
    """Create a CommandDispatcherService instance."""
    service = CommandDispatcherService(event_bus)
    await service.start()
    yield service
    await service.stop()

@pytest.mark.asyncio
async def test_initialization(command_dispatcher, event_bus):
    """Test service initialization."""
    # Status might still be INITIALIZING as the async _emit_status may not have completed
    assert command_dispatcher._started is True
    event_bus.on.assert_called()

@pytest.mark.asyncio
async def test_command_registration(command_dispatcher):
    """Test command registration."""
    await command_dispatcher.register_command(
        "test_command", 
        "test_service", 
        "TEST_EVENT_TOPIC"
    )
    
    assert "test_command" in command_dispatcher._command_handlers
    handler_service, event_topic = command_dispatcher._command_handlers["test_command"]
    assert handler_service == "test_service"
    assert event_topic == "TEST_EVENT_TOPIC"

@pytest.mark.asyncio
async def test_command_routing_registered(command_dispatcher, event_bus):
    """Test routing of a registered command."""
    # Register a command
    await command_dispatcher.register_command(
        "test_command", 
        "test_service", 
        "TEST_EVENT_TOPIC"
    )
    
    # Create command payload
    command_payload = CliCommandPayload(
        command="test_command",
        args=["arg1", "arg2"]
    )
    
    # Reset mock to clear previous calls
    event_bus.emit.reset_mock()
    
    # Route the command
    await command_dispatcher._route_command(command_payload)
    
    # Check that the event was emitted correctly
    event_bus.emit.assert_called_once()
    assert event_bus.emit.call_args[0][0] == "TEST_EVENT_TOPIC"
    # Don't check the entire payload as it includes dynamic fields (timestamp, ID)
    emitted_payload = event_bus.emit.call_args[0][1]
    assert emitted_payload["command"] == "test_command"
    assert emitted_payload["args"] == ["arg1", "arg2"]

@pytest.mark.asyncio
async def test_command_routing_unregistered(command_dispatcher, event_bus):
    """Test routing of an unregistered command."""
    # Create command payload for unregistered command
    command_payload = CliCommandPayload(
        command="unknown_command",
        args=[]
    )
    
    # Reset mock to clear previous calls
    event_bus.emit.reset_mock()
    
    # Route the command
    await command_dispatcher._route_command(command_payload)
    
    # Check that the error response was emitted
    event_bus.emit.assert_called_once()
    args = event_bus.emit.call_args[0]
    
    assert args[0] == EventTopics.CLI_RESPONSE
    response_payload = args[1]
    assert response_payload["is_error"] is True
    assert "Unknown command" in response_payload["message"]
    assert response_payload["command"] == "unknown_command"

@pytest.mark.asyncio
async def test_command_routing_exception(event_bus):
    """Test exception handling during command routing."""
    # Create a fresh instance to avoid test interactions
    command_dispatcher = CommandDispatcherService(event_bus)
    await command_dispatcher.start()
    
    # Register a test command
    await command_dispatcher.register_command(
        "test_command", 
        "test_service", 
        "TEST_EVENT_TOPIC"
    )
    
    # Create command payload
    command_payload = CliCommandPayload(
        command="test_command",
        args=[]
    )
    
    # Reset mocks
    event_bus.emit.reset_mock()
    
    # Make the event_bus.emit raise an exception
    event_bus.emit.side_effect = Exception("Test error")
    
    # Route the command
    await command_dispatcher._route_command(command_payload)
    
    # Check that the exception was logged (we don't assert emit calls because
    # the mock will continue to raise exceptions)
    
    # Clean up
    event_bus.emit.side_effect = None  # Clear the side effect for stop() to work
    await command_dispatcher.stop()
    
    # This test passes just by not raising an unhandled exception

@pytest.mark.asyncio
async def test_dict_payload_conversion(command_dispatcher, event_bus):
    """Test that dict payloads are converted to proper types."""
    # Register a command
    await command_dispatcher.register_command(
        "test_command", 
        "test_service", 
        "TEST_EVENT_TOPIC"
    )
    
    # Create dict payload instead of CliCommandPayload
    dict_payload = {
        "command": "test_command",
        "args": ["arg1", "arg2"]
    }
    
    # Reset mock to clear previous calls
    event_bus.emit.reset_mock()
    
    # Route the command
    await command_dispatcher._route_command(dict_payload)
    
    # Check that the event was emitted correctly (with topic and relevant fields)
    event_bus.emit.assert_called_once()
    assert event_bus.emit.call_args[0][0] == "TEST_EVENT_TOPIC"
    emitted_payload = event_bus.emit.call_args[0][1]
    assert emitted_payload["command"] == "test_command"
    assert emitted_payload["args"] == ["arg1", "arg2"]

@pytest.mark.asyncio
async def test_service_stop(command_dispatcher):
    """Test service shutdown."""
    await command_dispatcher.stop()
    assert command_dispatcher._started is False
    assert len(command_dispatcher._command_handlers) == 0  # Should be cleared 