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
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pyee.asyncio import AsyncIOEventEmitter
import asyncio

from cantina_os.services.mode_command_handler_service import ModeCommandHandlerService
from cantina_os.event_topics import EventTopics
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
    """Test handling of 'engage' command."""
    # Setup response listener
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    
    # Send engage command
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="engage").model_dump()
    )
    
    # Wait for async operations to complete
    await asyncio.sleep(0.1)
    
    # Verify mode change
    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.INTERACTIVE)
    
    # Verify response
    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "Interactive mode engaged" in response["message"]
    assert not response["is_error"]

@pytest.mark.asyncio
async def test_ambient_command(service, event_bus, mock_mode_manager):
    """Test handling of 'ambient' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="ambient").model_dump()
    )
    
    await asyncio.sleep(0.1)
    
    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.AMBIENT)
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
        CliCommandPayload(command="disengage").model_dump()
    )
    
    await asyncio.sleep(0.1)
    
    mock_mode_manager.set_mode.assert_called_once_with(SystemMode.IDLE)
    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "System disengaged" in response["message"]

@pytest.mark.asyncio
async def test_status_command(service, event_bus, mock_mode_manager):
    """Test handling of 'status' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    
    mock_mode_manager.current_mode = SystemMode.INTERACTIVE
    
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="status").model_dump()
    )
    
    await asyncio.sleep(0.1)
    
    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert "INTERACTIVE" in response["message"]

@pytest.mark.asyncio
async def test_help_command(service, event_bus):
    """Test handling of 'help' command."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    
    event_bus.emit(
        EventTopics.CLI_HELP_REQUEST,
        CliCommandPayload(command="help").model_dump()
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
        CliCommandPayload(command="reset").model_dump()
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
        CliCommandPayload(command="invalid_command").model_dump()
    )
    
    await asyncio.sleep(0.1)
    
    # Should not error, but also not emit a response
    response_future.assert_not_called()

@pytest.mark.asyncio
async def test_invalid_payload(service, event_bus):
    """Test handling of invalid payload format."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    status_future = AsyncMock()
    event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, status_future)
    
    # Send invalid payload
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        {"invalid": "payload"}
    )
    
    await asyncio.sleep(0.1)
    
    # Should emit error response and error status
    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert response["is_error"]
    
    status_future.assert_called_once()
    status = status_future.call_args[0][0]
    assert status["status"] == ServiceStatus.ERROR
    assert status["severity"] == LogLevel.ERROR

@pytest.mark.asyncio
async def test_mode_manager_error(service, event_bus, mock_mode_manager):
    """Test handling of mode manager errors."""
    response_future = AsyncMock()
    event_bus.on(EventTopics.CLI_RESPONSE, response_future)
    
    # Make mode manager raise an error
    mock_mode_manager.set_mode.side_effect = Exception("Mode change failed")
    
    event_bus.emit(
        EventTopics.MODE_COMMAND,
        CliCommandPayload(command="engage").model_dump()
    )
    
    await asyncio.sleep(0.1)
    
    response_future.assert_called_once()
    response = response_future.call_args[0][0]
    assert response["is_error"]
    assert "Error handling mode command" in response["message"] 