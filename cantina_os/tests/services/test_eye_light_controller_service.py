"""
Tests for the EyeLightControllerService
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import pytest
import serial
from pyee.asyncio import AsyncIOEventEmitter

from cantina_os.event_payloads import (
    BaseEventPayload,
    EyeCommandPayload,
    SentimentPayload,
    ServiceStatus
)
from cantina_os.event_topics import EventTopics
from cantina_os.services.eye_light_controller_service import (
    EyeLightControllerService,
    EyePattern,
    ArduinoCommand
)

# Patch EventTopics to include EYES_COMMAND (mapped to LED_COMMAND)
EventTopics.EYES_COMMAND = EventTopics.LED_COMMAND


class MockEventEmitter(AsyncIOEventEmitter):
    """Custom event emitter for testing that properly handles both sync and async events."""
    
    def __init__(self):
        super().__init__()
        self._tasks = set()
    
    def emit(self, event, *args, **kwargs):
        """Override emit to handle both sync and async events properly."""
        # Get all listeners for this event
        listeners = self._events.get(event, [])
        
        # For new_listener events, handle synchronously
        if event == "new_listener":
            for listener in listeners:
                listener(*args, **kwargs)
            return
        
        # For other events, handle async listeners properly
        for listener in listeners:
            if asyncio.iscoroutinefunction(listener):
                task = asyncio.create_task(listener(*args, **kwargs))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            else:
                listener(*args, **kwargs)
    
    async def cleanup(self):
        """Clean up any pending tasks."""
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


@pytest.fixture
def mock_serial_port():
    """Provide a mock serial port name for testing."""
    return "/dev/mock_arduino"


@pytest.fixture
async def mock_event_bus():
    """Provide a mock event bus for testing."""
    event_bus = MockEventEmitter()
    yield event_bus
    await event_bus.cleanup()


@pytest.fixture
async def service(mock_serial_port, mock_event_bus):
    """Create an EyeLightControllerService instance for testing in mock mode."""
    service = EyeLightControllerService(
        event_bus=mock_event_bus,
        serial_port=mock_serial_port,
        mock_mode=True,  # Use mock mode to avoid actual hardware
        name="TestEyeLightControllerService"
    )
    
    yield service
    
    # Ensure proper cleanup
    if service._status != ServiceStatus.STOPPED:
        await service.stop()
        # Wait for any pending tasks to complete
        await asyncio.sleep(0)


@pytest.fixture
async def real_service(mock_serial_port, mock_event_bus):
    """
    Create an EyeLightControllerService instance for testing with simulated hardware.
    This uses a mocked Serial connection instead of mock_mode for more realistic testing.
    """
    with patch("serial.Serial") as mock_serial:
        # Mock the serial connection methods
        mock_serial_instance = MagicMock()
        mock_serial.return_value = mock_serial_instance
        
        # Setup serial port behavior
        mock_serial_instance.in_waiting = 0
        mock_serial_instance.write = MagicMock()
        mock_serial_instance.reset_input_buffer = MagicMock()
        mock_serial_instance.reset_output_buffer = MagicMock()
        mock_serial_instance.flush = MagicMock()
        
        # Create service with hardware mode
        service = EyeLightControllerService(
            event_bus=mock_event_bus,
            serial_port=mock_serial_port,
            mock_mode=False,  # Use hardware mode
            name="TestEyeLightControllerService"
        )
        
        # Set mocked serial connection directly
        service.serial_connection = mock_serial_instance
        service.connected = True
        
        # Also patch _connect_to_arduino to avoid actual connection attempt
        service._connect_to_arduino = AsyncMock(return_value=True)
        
        # Mock logger
        service.logger = MagicMock()
        service.logger.error = MagicMock()
        service.logger.warning = MagicMock()
        service.logger.info = MagicMock()
        service.logger.debug = MagicMock()
        
        yield service
        
        if service._status != ServiceStatus.STOPPED:
            await service.stop()


class TestEyeLightControllerService:
    """Tests for the EyeLightControllerService."""

    @pytest.mark.asyncio
    async def test_mock_mode_initialization(self, service):
        """Test initialization in mock mode."""
        assert service.serial_port == "/dev/mock_arduino"
        assert service.mock_mode is True
        assert service._status == ServiceStatus.INITIALIZING
        
        # Start service in mock mode
        await service.start()
        
        # Verify it started successfully without real hardware
        assert service._status == ServiceStatus.RUNNING
        assert service.connected is True
        assert service.serial_connection is None  # No actual connection in mock mode

    @pytest.mark.asyncio
    @patch("serial.tools.list_ports.comports")
    async def test_auto_detect_arduino(self, mock_comports, service):
        """Test the Arduino auto-detection logic."""
        # Create mock port objects
        mock_port1 = MagicMock()
        mock_port1.device = "/dev/ttyACM0"
        mock_port1.vid = 0x2341  # Arduino vendor ID
        mock_port1.pid = 0x0043  # Arduino product ID
        mock_port1.description = "Arduino Uno"
        
        mock_port2 = MagicMock()
        mock_port2.device = "/dev/ttyUSB0"
        mock_port2.vid = 0x1A86  # CH340 vendor ID
        mock_port2.pid = 0x7523  # CH340 product ID
        mock_port2.description = "USB-Serial Controller"
        
        # Set up the mock to return these ports
        mock_comports.return_value = [mock_port1, mock_port2]
        
        # Test auto-detection
        port = await service._auto_detect_arduino()
        
        # Should find the Arduino Uno
        assert port == "/dev/ttyACM0"
        
        # Test fallback when no Arduino detected
        mock_port1.vid = 0x0000  # Not an Arduino
        mock_port2.vid = 0x0000  # Not an Arduino
        
        port = await service._auto_detect_arduino()
        
        # Should return first available port as fallback
        assert port == "/dev/ttyACM0"
        
        # Test when no ports available
        mock_comports.return_value = []
        
        port = await service._auto_detect_arduino()
        
        # Should return None when no ports available
        assert port is None

    @pytest.mark.asyncio
    async def test_mock_set_pattern(self, service):
        """Test setting LED patterns in mock mode."""
        # Start the service
        await service.start()
        
        # Set pattern
        success = await service.set_pattern(
            pattern=EyePattern.HAPPY,
            color="#00FF00",
            brightness=0.8
        )
        
        # Verify success and state update
        assert success is True
        assert service.current_pattern == EyePattern.HAPPY
        assert service.current_color == "#00FF00"
        assert service.current_brightness == 0.8

    @pytest.mark.asyncio
    async def test_real_set_pattern(self, real_service):
        """Test setting a pattern in hardware mode."""
        # Start the service
        await real_service.start()
        
        # Set up the mock to return success response
        response_sent = False
        def mock_read(size):
            nonlocal response_sent
            if not response_sent and real_service.serial_connection.in_waiting > 0:
                response_sent = True
                return b'{"ack": true}\n'
            return b''
        real_service.serial_connection.read = mock_read
        
        # Simulate data available after command is sent
        def mock_write(data):
            real_service.serial_connection.in_waiting = 20
        real_service.serial_connection.write = mock_write
        
        # Test setting a pattern
        result = await real_service.set_pattern(
            pattern=EyePattern.HAPPY,
            color="#FFFF00",
            brightness=0.8
        )
        
        # Verify success
        assert result is True
        assert real_service.current_pattern == EyePattern.HAPPY
        assert real_service.current_color == "#FFFF00"
        assert real_service.current_brightness == 0.8

    @pytest.mark.asyncio
    async def test_handle_eye_command(self, service):
        """Test handling eye command events."""
        # Start the service
        await service.start()
        
        # Create a test payload
        payload = EyeCommandPayload(
            pattern=EyePattern.HAPPY,
            color="#FFFF00",
            intensity=0.9,
            duration=2.0
        )
        
        # Track the task
        task = asyncio.create_task(service._handle_eye_command(payload))
        service.event_bus._tasks.add(task)
        
        # Wait for the command to complete
        await task
        
        # Verify the pattern was set correctly
        assert service.current_pattern == EyePattern.HAPPY
        assert service.current_color == "#FFFF00"
        assert service.current_brightness == 0.9

    @pytest.mark.asyncio
    async def test_handle_sentiment(self, service):
        """Test handling sentiment analysis events."""
        # Start the service
        await service.start()
        
        # Create a test payload for positive sentiment
        payload = SentimentPayload(
            conversation_id="test-conversation",
            label="positive",
            score=0.8
        )
        
        # Track the task
        task = asyncio.create_task(service._handle_sentiment(payload))
        service.event_bus._tasks.add(task)
        
        # Wait for the sentiment handling to complete
        await task
        
        # Verify pattern was set correctly for positive sentiment
        assert service.current_pattern == EyePattern.HAPPY
        assert service.current_color == "#00FF00"  # Green for positive
        
        # Test negative sentiment
        payload = SentimentPayload(
            conversation_id="test-conversation",
            label="negative",
            score=0.7
        )
        
        # Track the task
        task = asyncio.create_task(service._handle_sentiment(payload))
        service.event_bus._tasks.add(task)
        
        # Wait for the sentiment handling to complete
        await task
        
        # Verify pattern was set correctly for negative sentiment
        assert service.current_pattern == EyePattern.SAD
        assert service.current_color == "#0000FF"  # Blue for negative

    @pytest.mark.asyncio
    async def test_handle_speech_events(self, service):
        """Test handling speech synthesis events."""
        # Start the service
        await service.start()
        
        # Create a test payload
        event_payload = BaseEventPayload(
            conversation_id="test-conversation"
        )
        
        # Track and wait for speech started event
        task = asyncio.create_task(service._handle_speech_started(event_payload))
        service.event_bus._tasks.add(task)
        await task
        
        # Verify pattern was set to SPEAKING
        assert service.current_pattern == EyePattern.SPEAKING
        
        # Track and wait for speech ended event
        task = asyncio.create_task(service._handle_speech_ended(event_payload))
        service.event_bus._tasks.add(task)
        await task
        
        # Verify pattern was set to IDLE
        assert service.current_pattern == EyePattern.IDLE

    @pytest.mark.asyncio
    async def test_handle_listening_events(self, service):
        """Test handling listening events."""
        # Start the service
        await service.start()
        
        # Create a test payload
        event_payload = BaseEventPayload(
            conversation_id="test-conversation"
        )
        
        # Track and wait for listening active event
        task = asyncio.create_task(service._handle_listening_active(event_payload))
        service.event_bus._tasks.add(task)
        await task
        
        # Verify pattern was set to LISTENING
        assert service.current_pattern == EyePattern.LISTENING
        
        # Track and wait for listening ended event
        task = asyncio.create_task(service._handle_listening_ended(event_payload))
        service.event_bus._tasks.add(task)
        await task
        
        # Verify pattern was set to THINKING
        assert service.current_pattern == EyePattern.THINKING

    @pytest.mark.asyncio
    async def test_command_timeout(self, real_service):
        """Test command timeout handling."""
        # Start the service
        await real_service.start()
        
        # Mock the serial connection to simulate timeout
        def mock_read(size):
            # Simulate no data available
            return b''
        real_service.serial_connection.read = mock_read
        real_service.serial_connection.in_waiting = 0
        
        # Create a test command
        test_command = {
            "command": ArduinoCommand.STATUS,
            "params": {}
        }
        
        # Send command and verify it fails
        result = await real_service._send_command(test_command)
        
        # Verify timeout was detected
        assert result is False
        assert real_service.logger.error.call_count > 0
        assert any("timed out" in str(call.args[0]).lower() for call in real_service.logger.error.call_args_list)

    @pytest.mark.asyncio
    async def test_error_response(self, real_service):
        """Test handling error responses from Arduino."""
        # Start the service
        await real_service.start()
        
        # Set up the mock to return an error response
        response_sent = False
        def mock_read(size):
            nonlocal response_sent
            if not response_sent and real_service.serial_connection.in_waiting > 0:
                response_sent = True
                # Return a complete line with newline
                return b'{"error": "invalid_pattern", "details": "Pattern not supported"}\n'
            return b''
        real_service.serial_connection.read = mock_read
        
        # Create a test command
        test_command = {
            "command": ArduinoCommand.SET_PATTERN,
            "params": {"pattern": "invalid_pattern"}
        }
        
        # Simulate data available after command is sent
        def mock_write(data):
            real_service.serial_connection.in_waiting = len(b'{"error": "invalid_pattern", "details": "Pattern not supported"}\n')
        real_service.serial_connection.write = mock_write
        
        # Send command and verify it fails
        result = await real_service._send_command(test_command)
        
        # Verify error was detected
        assert result is False
        assert real_service.logger.error.call_count > 0
        
        # Get the actual error message that was logged
        error_messages = [str(call.args[0]) for call in real_service.logger.error.call_args_list]
        print("Actual error messages:", error_messages)  # Debug print
        
        # Check for the error message
        assert any("Arduino error: invalid_pattern - Pattern not supported" in msg for msg in error_messages)

    @pytest.mark.asyncio
    async def test_invalid_json_response(self, real_service):
        """Test handling invalid JSON responses from Arduino."""
        # Start the service
        await real_service.start()
        
        # Set up the mock to return invalid JSON
        response_sent = False
        def mock_read(size):
            nonlocal response_sent
            if not response_sent and real_service.serial_connection.in_waiting > 0:
                response_sent = True
                return b'invalid json data\n'
            return b''
        real_service.serial_connection.read = mock_read
        
        # Create a test command
        test_command = {
            "command": ArduinoCommand.STATUS,
            "params": {}
        }
        
        # Simulate data available after command is sent
        def mock_write(data):
            real_service.serial_connection.in_waiting = 20
        real_service.serial_connection.write = mock_write
        
        # Send command and verify it fails
        result = await real_service._send_command(test_command)
        
        # Verify invalid JSON was detected
        assert result is False
        assert real_service.logger.warning.call_count > 0
        assert any("Invalid JSON" in str(call.args[0]) for call in real_service.logger.warning.call_args_list) 