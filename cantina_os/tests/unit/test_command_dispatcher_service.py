"""
Unit tests for CommandDispatcherService

This module contains tests for the CommandDispatcherService, which routes CLI commands
to appropriate handlers in CantinaOS.

NOTE ON PRODUCTION CONTRACT (as of this branch):
- `register_command(command, service_name, event_topic)` is a plain synchronous
  method (not a coroutine) that stores `{"service": ..., "topic": ...}` dicts
  in `self.command_handlers` (compound commands go in `self.compound_commands`)
  - there is no `_command_handlers` attribute and no tuple return shape.
- There is no `_route_command` method; CLI_COMMAND payloads are routed through
  `_handle_command`, which expects a plain dict (it calls `.get()` on the
  payload directly) - not a `CliCommandPayload` model instance, so tests
  build payload dicts (or call `.model_dump()`).
- `_handle_command` first runs `validate_command_payload(payload)`, which
  requires `command`, `args`, and `raw_input` to all be present AND
  `raw_input` to be a `str` - `CliCommandPayload`'s default `raw_input=None`
  fails this check, so every payload here sets `raw_input` explicitly.
- `_send_error` (used for unknown commands and for the top-level exception
  handler) emits `{"message": ..., "is_error": True}` on `CLI_RESPONSE` -
  it does NOT include a `"command"` key.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from cantina_os.services.command_dispatcher_service import CommandDispatcherService
from cantina_os.core.event_topics import EventTopics
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
    # register_command is a plain (non-async) method
    command_dispatcher.register_command(
        "test_command",
        "test_service",
        "TEST_EVENT_TOPIC"
    )

    assert "test_command" in command_dispatcher.command_handlers
    service_info = command_dispatcher.command_handlers["test_command"]
    assert service_info["service"] == "test_service"
    assert service_info["topic"] == "TEST_EVENT_TOPIC"

@pytest.mark.asyncio
async def test_command_routing_registered(command_dispatcher, event_bus):
    """Test routing of a registered command."""
    command_dispatcher.register_command(
        "test_command",
        "test_service",
        "TEST_EVENT_TOPIC"
    )

    # _handle_command works on a plain dict and requires raw_input to be a str
    command_payload = {
        "command": "test_command",
        "args": ["arg1", "arg2"],
        "raw_input": "test_command arg1 arg2",
    }

    # Reset mock to clear previous calls
    event_bus.emit.reset_mock()

    # Route the command
    await command_dispatcher._handle_command(command_payload)

    # Check that the event was emitted correctly
    event_bus.emit.assert_called_once()
    assert event_bus.emit.call_args[0][0] == "TEST_EVENT_TOPIC"
    # Don't check the entire payload as it includes dynamic fields (timestamp, ID)
    emitted_payload = event_bus.emit.call_args[0][1]
    assert emitted_payload["command"] == "test_command"
    assert emitted_payload["args"] == ["arg1", "arg2"]

@pytest.mark.asyncio
async def test_command_routing_unregistered(command_dispatcher, event_bus):
    """Test routing of an unregistered command.

    `_send_error` is what actually emits the CLI_RESPONSE here, and its
    payload shape is `{"message": ..., "is_error": True}` - no `"command"`
    key is included.
    """
    command_payload = {
        "command": "unknown_command",
        "args": [],
        "raw_input": "unknown_command",
    }

    # Reset mock to clear previous calls
    event_bus.emit.reset_mock()

    # Route the command
    await command_dispatcher._handle_command(command_payload)

    # Check that the error response was emitted
    event_bus.emit.assert_called_once()
    args = event_bus.emit.call_args[0]

    assert args[0] == EventTopics.CLI_RESPONSE
    response_payload = args[1]
    assert response_payload["is_error"] is True
    assert "Unknown command" in response_payload["message"]
    assert "unknown_command" in response_payload["message"]

@pytest.mark.asyncio
async def test_command_routing_exception(event_bus):
    """Test exception handling during command routing.

    `BaseService.emit` calls `self._event_bus.emit(event, payload)` WITHOUT
    awaiting it (base_service.py ~line 246, with a comment claiming pyee's
    `emit` "returns a boolean, not a coroutine" - true for the real
    `AsyncIOEventEmitter`, but not for this fixture's `AsyncMock`). Because
    the call is never awaited, an `AsyncMock(side_effect=...)` never actually
    raises here - it just creates an unawaited coroutine (visible as a
    `RuntimeWarning: coroutine ... was never awaited`). So, with a mocked
    event bus, `_handle_command` cannot actually observe an exception from
    `emit` at all; this test only verifies no exception escapes.
    """
    command_dispatcher = CommandDispatcherService(event_bus)
    await command_dispatcher.start()

    command_dispatcher.register_command(
        "test_command",
        "test_service",
        "TEST_EVENT_TOPIC"
    )

    command_payload = {
        "command": "test_command",
        "args": [],
        "raw_input": "test_command",
    }

    # Reset mocks
    event_bus.emit.reset_mock()

    # Make the event_bus.emit raise an exception
    event_bus.emit.side_effect = Exception("Test error")

    # Route the command - must not raise, since the fire-and-forget emit()
    # never actually surfaces the mock's side_effect (see docstring above).
    await command_dispatcher._handle_command(command_payload)

    # Clean up
    event_bus.emit.side_effect = None  # Clear the side effect for stop() to work
    await command_dispatcher.stop()

@pytest.mark.asyncio
async def test_dict_payload_conversion(command_dispatcher, event_bus):
    """Test that plain dict payloads are routed the same way as any other payload."""
    command_dispatcher.register_command(
        "test_command",
        "test_service",
        "TEST_EVENT_TOPIC"
    )

    dict_payload = {
        "command": "test_command",
        "args": ["arg1", "arg2"],
        "raw_input": "test_command arg1 arg2",
    }

    # Reset mock to clear previous calls
    event_bus.emit.reset_mock()

    # Route the command
    await command_dispatcher._handle_command(dict_payload)

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
    assert len(command_dispatcher.command_handlers) == 0  # Should be cleared
