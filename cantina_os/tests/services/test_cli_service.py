"""
Tests for the CLI Service
"""

import asyncio
import pytest
from unittest.mock import Mock, patch, AsyncMock, call, ANY
from typing import Dict, Any

from cantina_os.services import CLIService
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
def mock_io():
    """Create mock I/O functions."""
    return {
        'input': Mock(return_value="test input"),
        'output': Mock(),
        'error': Mock()
    }

@pytest.fixture
def config():
    """Create test configuration."""
    return {
        'CLI_MAX_HISTORY': 10
    }

@pytest.fixture
async def cli_service(event_bus, mock_io, config):
    """Create a CLI service instance."""
    service = CLIService(event_bus, config=config, io_functions=mock_io)
    await service.start()
    yield service
    await service.stop()

@pytest.mark.asyncio
async def test_cli_service_initialization(cli_service, event_bus, config):
    """Test that the service initializes correctly."""
    assert cli_service._running == True
    assert cli_service._input_task is not None
    assert cli_service._max_history == config['CLI_MAX_HISTORY']
    assert cli_service._command_history == []
    event_bus.on.assert_called_with(EventTopics.CLI_RESPONSE, cli_service._handle_response)

@pytest.mark.asyncio
async def test_service_lifecycle(event_bus, config, mock_io):
    """Test service start and stop lifecycle."""
    service = CLIService(event_bus, config=config, io_functions=mock_io)
    
    # Test start
    await service.start()
    assert service._running == True
    assert service._input_task is not None
    
    # Verify status emission
    await asyncio.sleep(0.1)  # Give time for async operations
    event_bus.emit.assert_any_call(
        EventTopics.SERVICE_STATUS_UPDATE,
        {
            "timestamp": ANY,
            "event_id": ANY,
            "conversation_id": None,
            "schema_version": "1.0",
            "service": "cli",
            "status": ServiceStatus.RUNNING,
            "message": "CLI service ready"
        }
    )
    
    # Test stop
    await service.stop()
    assert service._running == False
    assert service._input_task.cancelled()

@pytest.mark.asyncio
async def test_command_shortcuts(cli_service, event_bus):
    """Test that command shortcuts are properly expanded."""
    # Reset mock to clear initialization calls
    event_bus.emit.reset_mock()
    
    # Test all shortcuts
    shortcuts_tests = [
        ('e', 'engage', []),
        ('a', 'ambient', []),
        ('d', 'disengage', []),
        ('h', 'help', []),
        ('s', 'status', []),
        ('r', 'reset', []),
        ('l', 'list music', []),
        ('p', 'play music', ['test_song']),
    ]
    
    for shortcut, expected_cmd, args in shortcuts_tests:
        event_bus.emit.reset_mock()
        await cli_service._process_command(f"{shortcut} {' '.join(args)}".strip())
        
        if expected_cmd in ['engage', 'ambient', 'disengage', 'status', 'reset']:
            expected_topic = EventTopics.MODE_COMMAND
        elif expected_cmd.startswith(('play', 'list', 'stop')):
            expected_topic = EventTopics.MUSIC_COMMAND
        elif expected_cmd == 'help':
            expected_topic = EventTopics.CLI_HELP_REQUEST
        else:
            expected_topic = EventTopics.CLI_COMMAND
            
        # Use ANY for dynamic fields
        assert event_bus.emit.call_args_list[0] == call(
            expected_topic,
            {
                'timestamp': ANY,
                'event_id': ANY,
                'conversation_id': None,
                'schema_version': '1.0',
                'command': expected_cmd.split()[0],
                'args': expected_cmd.split()[1:] + args if len(expected_cmd.split()) > 1 else args,
                'raw_input': f"{shortcut} {' '.join(args)}".strip()
            }
        )

@pytest.mark.asyncio
async def test_music_commands(cli_service, event_bus):
    """Test music-specific commands."""
    event_bus.emit.reset_mock()
    
    # Test cases
    commands = [
        ('list music', EventTopics.MUSIC_COMMAND, {'command': 'list', 'args': ['music']}),
        ('play music test_song', EventTopics.MUSIC_COMMAND, {'command': 'play', 'args': ['music', 'test_song']}),
        ('stop music', EventTopics.MUSIC_COMMAND, {'command': 'stop', 'args': ['music']})
    ]
    
    for cmd, expected_topic, expected_payload in commands:
        event_bus.emit.reset_mock()
        await cli_service._process_command(cmd)
        
        assert event_bus.emit.call_args_list[0] == call(
            expected_topic,
            {
                'timestamp': ANY,
                'event_id': ANY,
                'conversation_id': None,
                'schema_version': '1.0',
                'command': expected_payload['command'],
                'args': expected_payload['args'],
                'raw_input': cmd
            }
        )

@pytest.mark.asyncio
async def test_command_history(cli_service, config):
    """Test command history management."""
    # Test adding commands
    commands = ["test1", "test2", "test3"]
    for cmd in commands:
        cli_service._add_to_history(cmd)
    assert cli_service._command_history == commands
    
    # Test history limit
    max_commands = config['CLI_MAX_HISTORY']
    overflow_commands = [f"cmd{i}" for i in range(max_commands + 10)]
    
    for cmd in overflow_commands:
        cli_service._add_to_history(cmd)
        
    assert len(cli_service._command_history) == max_commands
    assert cli_service._command_history == overflow_commands[-max_commands:]

@pytest.mark.asyncio
async def test_handle_response(cli_service, mock_io):
    """Test response handling with different payload types."""
    # Test successful response
    success_payload = CliResponsePayload(message="Success", is_error=False)
    await cli_service._handle_response(success_payload.model_dump())
    mock_io['output'].assert_called_with("Success")
    
    # Test error response
    error_payload = CliResponsePayload(message="Error occurred", is_error=True)
    await cli_service._handle_response(error_payload.model_dump())
    mock_io['error'].assert_called_with("Error: Error occurred")
    
    # Test dict payload
    dict_payload = {"message": "Dict message", "is_error": False}
    await cli_service._handle_response(dict_payload)
    mock_io['output'].assert_called_with("Dict message")

@pytest.mark.asyncio
async def test_error_handling(cli_service, event_bus):
    """Test error handling in command processing."""
    event_bus.emit.reset_mock()
    
    # Test empty command
    await cli_service._process_command("")
    
    # Verify error status was emitted
    event_bus.emit.assert_any_call(
        EventTopics.SERVICE_STATUS_UPDATE,
        {
            "timestamp": ANY,
            "event_id": ANY,
            "conversation_id": None,
            "schema_version": "1.0",
            "service": "cli",
            "status": ServiceStatus.ERROR,
            "message": "Error processing command: Empty command",
            "severity": LogLevel.ERROR
        }
    )

@pytest.mark.asyncio
async def test_shutdown_handling(cli_service, event_bus):
    """Test shutdown command handling."""
    event_bus.emit.reset_mock()
    
    # Test quit command
    await cli_service._process_command("quit")
    assert event_bus.emit.call_args_list[0] == call(
        EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
        {"reason": "User requested shutdown"}
    )
    
    # Test exit command
    event_bus.emit.reset_mock()
    await cli_service._process_command("exit")
    assert event_bus.emit.call_args_list[0] == call(
        EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
        {"reason": "User requested shutdown"}
    )

@pytest.mark.asyncio
async def test_input_loop_error_handling(event_bus, mock_io):
    """Test error handling in the input loop."""
    mock_io['input'].side_effect = Exception("Test input error")
    service = CLIService(event_bus, io_functions=mock_io)
    
    await service.start()
    await asyncio.sleep(0.1)  # Give time for error handling
    
    # Verify error status was emitted
    assert any(
        call.args[0] == EventTopics.SERVICE_STATUS_UPDATE and
        call.args[1]["status"] == ServiceStatus.ERROR and
        "Test input error" in call.args[1]["message"]
        for call in event_bus.emit.mock_calls
    )
    
    await service.stop() 