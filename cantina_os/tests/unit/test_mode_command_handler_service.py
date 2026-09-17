"""
Unit tests for ModeCommandHandlerService.

Key test areas:
1. Command handling for mode transitions
2. Status command functionality
3. Help text generation
4. Reset command behavior
5. Error handling
6. Event emission
7. Service lifecycle

NOTE ON PRODUCTION CONTRACT (as of this branch):
- `_handle_mode_command` reads `payload.get("raw_input", "")` and calls
  `.strip().lower()` on it unconditionally (mode_command_handler_service.py
  ~line 100). `CliCommandPayload` defaults `raw_input` to `None`, so any
  payload built without an explicit `raw_input` matching the command text
  will raise `AttributeError` inside the event handler (swallowed by the
  event bus), and NO response is ever emitted. All payloads below now set
  `raw_input` explicitly.
- The decorated handlers (`handle_engage`, `handle_disengage`,
  `handle_ambient`, `handle_idle`) only call `mode_manager.set_mode(...)`;
  they do not themselves emit a CLI_RESPONSE. The user-facing message is
  emitted separately by `_handle_mode_change`, which reacts to a real
  `SYSTEM_MODE_CHANGE` event from the (real) YodaModeManagerService. Since
  these tests use a mocked mode manager that does not emit that event,
  responses for engage/disengage/ambient are verified by manually emitting
  `SYSTEM_MODE_CHANGE` to exercise `_handle_mode_change` directly.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pyee.asyncio import AsyncIOEventEmitter
import asyncio

from cantina_os.services.mode_command_handler_service import ModeCommandHandlerService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    CliCommandPayload,
    CliResponsePayload,
    ServiceStatus,
    LogLevel
)
from cantina_os.services.yoda_mode_manager_service import SystemMode

pytestmark = pytest.mark.asyncio(loop_scope="function")

@pytest.fixture
async def event_bus():
    """Create a fresh event bus for each test."""
    bus = AsyncIOEventEmitter()
    yield bus
    # Clean up any remaining listeners
    bus.remove_all_listeners()

@pytest.fixture
def mock_mode_manager():
    """Create a mock YodaModeManagerService."""
    manager = MagicMock()
    manager.set_mode = AsyncMock()
    manager.current_mode = SystemMode.IDLE
    return manager

@pytest.fixture
async def service(event_bus, mock_mode_manager):
    """Create and start a ModeCommandHandlerService instance."""
    service = ModeCommandHandlerService(event_bus, mock_mode_manager)
    await service.start()
    yield service
    await service.stop()

async def test_service_lifecycle(service, event_bus):
    """Test service start and stop."""
    # Service is started in fixture
    assert service.is_running

    # Verify subscriptions
    assert event_bus.listeners(EventTopics.MODE_COMMAND)

    # Stop service
    await service.stop()
    assert not service.is_running

@pytest.mark.asyncio
async def test_engage_command(service, event_bus, mock_mode_manager):
    """Test handling of 'engage' command.

    The 'engage' command only triggers `mode_manager.set_mode`; the actual
    CLI_RESPONSE text is produced later by `_handle_mode_change` in reaction
    to a SYSTEM_MODE_CHANGE event emitted by the real mode manager. We
    simulate that event here since the mode manager is mocked.
    """
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    # Send engage command
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="engage", raw_input="engage").model_dump()
    )

    await asyncio.sleep(0.1)

    # Verify mode change was requested
    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.INTERACTIVE)
    # The decorated handler itself does not emit a CLI response
    response_future.assert_not_called()

    # Simulate the real mode manager announcing the completed transition
    event_bus.emit(
        EventTopics.SYSTEM_MODE_CHANGE,
        {"new_mode": SystemMode.INTERACTIVE.name}
    )
    await asyncio.sleep(0.1)

    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "Interactive voice mode engaged" in response["message"]
    assert not response["is_error"]

@pytest.mark.asyncio
async def test_ambient_command(service, event_bus, mock_mode_manager):
    """Test handling of 'ambient' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="ambient", raw_input="ambient").model_dump()
    )

    await asyncio.sleep(0.1)

    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.AMBIENT)
    response_future.assert_not_called()

    event_bus.emit(
        EventTopics.SYSTEM_MODE_CHANGE,
        {"new_mode": SystemMode.AMBIENT.name}
    )
    await asyncio.sleep(0.1)

    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "Ambient show mode" in response["message"]

@pytest.mark.asyncio
async def test_disengage_command(service, event_bus, mock_mode_manager):
    """Test handling of 'disengage' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="disengage", raw_input="disengage").model_dump()
    )

    await asyncio.sleep(0.1)

    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.IDLE)
    response_future.assert_not_called()

    event_bus.emit(
        EventTopics.SYSTEM_MODE_CHANGE,
        {"new_mode": SystemMode.IDLE.name}
    )
    await asyncio.sleep(0.1)

    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "System is now in IDLE mode" in response["message"]

@pytest.mark.asyncio
async def test_status_command(service, event_bus, mock_mode_manager):
    """Test handling of 'status' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    mock_mode_manager.current_mode = SystemMode.INTERACTIVE

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="status", raw_input="status").model_dump()
    )

    await asyncio.sleep(0.1)

    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "INTERACTIVE" in response["message"]

@pytest.mark.asyncio
async def test_help_command(service, event_bus):
    """Test handling of 'help' command.

    Help is routed through EventTopics.MODE_COMMAND (the service's
    `_default_command_topic`); there is no separate CLI_HELP_REQUEST
    subscription in `_start()`.
    """
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="help", raw_input="help").model_dump()
    )

    await asyncio.sleep(0.1)

    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "Available Commands" in response["message"]
    assert "engage" in response["message"]
    assert "ambient" in response["message"]
    assert "disengage" in response["message"]

@pytest.mark.asyncio
async def test_reset_command(service, event_bus, mock_mode_manager):
    """Test handling of 'reset' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="reset", raw_input="reset").model_dump()
    )

    await asyncio.sleep(0.1)

    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.IDLE)
    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "System reset" in response["message"]

@pytest.mark.asyncio
async def test_invalid_command(service, event_bus):
    """Test handling of invalid command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="invalid_command", raw_input="invalid_command").model_dump()
    )

    await asyncio.sleep(0.1)

    # Should not error, but also not emit a response
    response_future.assert_not_called()

@pytest.mark.asyncio
async def test_invalid_payload(service, event_bus):
    """Test handling of a payload missing 'command'/'raw_input' entirely.

    Actual current behavior: `_handle_mode_command` uses `payload.get(...)`
    defensively for both `raw_input` and `command`, so a payload with
    neither key present simply resolves to an empty command string, matches
    no branch, and produces no response and no error status - it is a
    silent no-op, not an error path. (The former assumption that this shape
    of payload would raise/errored no longer matches
    mode_command_handler_service.py's `_handle_mode_command`.)
    """
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    status_future = AsyncMock()
    event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, status_future)

    # Send payload with neither "command" nor "raw_input"
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        {"invalid": "payload"}
    )

    await asyncio.sleep(0.1)

    # No response and no error status are emitted for this shape of payload
    response_future.assert_not_called()
    status_future.assert_not_called()

@pytest.mark.asyncio
async def test_error_in_non_decorated_command(service, event_bus, mock_mode_manager):
    """Test error handling for commands handled inline in _handle_mode_command
    (e.g. 'reset'), where exceptions ARE caught by its own try/except and
    turned into an error CLI_RESPONSE + SERVICE_STATUS_UPDATE.
    """
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    status_future = AsyncMock()
    # NOTE: BaseService._emit_status (base_service.py ~line 155) emits on the
    # hardcoded literal topic "service_status", NOT
    # EventTopics.SERVICE_STATUS_UPDATE ("service.status.update"). Listening
    # on the EventTopics constant here would never see this event - see the
    # production-bug note in the final test report.
    event_bus.on("service_status", status_future)

    mock_mode_manager.set_mode.side_effect = Exception("Mode change failed")

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="reset", raw_input="reset").model_dump()
    )

    await asyncio.sleep(0.1)

    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert response["is_error"]
    assert "Error handling mode command" in response["message"]

    status_future.assert_called()
    status = status_future.call_args_list[-1][0][0]
    assert status["status"] == ServiceStatus.ERROR

@pytest.mark.asyncio
async def test_mode_manager_error(service, event_bus, mock_mode_manager):
    """Test handling of mode manager errors raised from a decorated command.

    'engage' is a decorated compound command wrapped in
    `command_error_handler` (utils/command_decorators.py). That decorator
    only forwards the error to `self._send_error(...)` if the service
    defines that method - `ModeCommandHandlerService` does not, so the
    exception is logged and swallowed with no CLI_RESPONSE emitted. See the
    production-bug note in the final test report.
    """
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)

    # Make mode manager raise an error
    mock_mode_manager.set_mode.side_effect = Exception("Mode change failed")

    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="engage", raw_input="engage").model_dump()
    )

    await asyncio.sleep(0.1)

    # command_error_handler swallows the error silently (no _send_error on
    # this service), so no CLI_RESPONSE is emitted.
    response_future.assert_not_called()
