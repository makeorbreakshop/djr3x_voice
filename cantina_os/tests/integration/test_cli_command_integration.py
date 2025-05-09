"""
Integration Test for CLI Commands

This test module verifies the proper integration between the CLI service and other system 
services through the event bus. It tests command processing, event propagation, and 
response handling across service boundaries.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cantina_os.services.cli_service import CLIService
from cantina_os.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from cantina_os.services.music_controller_service import MusicControllerService
from cantina_os.event_topics import EventTopics

from ..utils.event_synchronizer import EventSynchronizer
from ..utils.retry_decorator import retry

class EventBusWrapper:
    """Wrapper around AsyncIOEventEmitter to make emit method awaitable."""
    
    def __init__(self, emitter):
        self._emitter = emitter
        
    async def emit(self, topic, payload=None):
        """Awaitable emit method that delegates to the emitter."""
        result = self._emitter.emit(topic, payload)
        return result
        
    async def on(self, topic, handler):
        """Awaitable on method that delegates to the emitter."""
        self._emitter.on(topic, handler)
        return None
        
    async def once(self, topic, handler):
        """Awaitable once method that delegates to the emitter."""
        self._emitter.once(topic, handler)
        return None
        
    async def remove_listener(self, topic, handler):
        """Awaitable remove_listener method that delegates to the emitter."""
        self._emitter.remove_listener(topic, handler)
        return None
        
    async def remove_all_listeners(self, topic=None):
        """Awaitable remove_all_listeners method that delegates to the emitter."""
        self._emitter.remove_all_listeners(topic)
        return None
        
    def listeners(self, topic):
        """Get listeners for a topic."""
        return self._emitter.listeners(topic)

class MockYodaModeManager:
    """Mock YodaModeManager for testing."""
    
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.current_mode = SystemMode.IDLE
        self.mode_requests = []
    
    async def start(self):
        """Start the mock service and emit initial mode."""
        await self.event_bus.emit(
            EventTopics.SYSTEM_MODE_CHANGE,
            {"mode": SystemMode.IDLE.value}
        )
        
    async def stop(self):
        """Stop the mock service."""
        pass
    
    async def handle_mode_request(self, payload):
        """Handle a mode request."""
        try:
            mode_name = payload.get("mode")
            if mode_name:
                self.mode_requests.append(mode_name)
                # Emit a mode change event
                await self.event_bus.emit(
                    EventTopics.SYSTEM_MODE_CHANGE,
                    {"mode": mode_name}
                )
        except Exception as e:
            print(f"Error in mock mode handler: {e}")

class TestCLICommandIntegration:
    """Tests for CLI command integration with other services."""
    
    @pytest.fixture
    async def event_bus(self):
        """Event bus fixture for testing."""
        from pyee.asyncio import AsyncIOEventEmitter
        emitter = AsyncIOEventEmitter()
        return EventBusWrapper(emitter)
    
    @pytest.fixture
    async def mock_io(self):
        """Mock I/O functions for CLI service."""
        input_queue = asyncio.Queue()
        output_list = []
        error_list = []
        
        async def mock_input():
            return await input_queue.get()
            
        def mock_output(text, end="\n"):
            output_list.append(text)
            
        def mock_error(text):
            error_list.append(text)
            
        return {
            "input": mock_input,
            "output": mock_output,
            "error": mock_error,
            "input_queue": input_queue,
            "output_list": output_list,
            "error_list": error_list
        }
    
    @pytest.fixture
    async def event_synchronizer(self, event_bus):
        """Event synchronizer fixture."""
        syncer = EventSynchronizer(event_bus, grace_period_ms=100)
        yield syncer
        await syncer.cleanup()
    
    @pytest.fixture
    async def cli_service(self, event_bus, mock_io):
        """CLI service fixture."""
        service = CLIService(
            event_bus,
            io_functions={
                'input': mock_io["input"],
                'output': mock_io["output"],
                'error': mock_io["error"]
            }
        )
        await service.start()
        yield service
        await service.stop()
    
    @pytest.fixture
    async def mode_manager(self, event_bus):
        """Mode manager fixture."""
        manager = MockYodaModeManager(event_bus)
        
        # Set up a subscription for mode requests
        await event_bus.on(EventTopics.SYSTEM_SET_MODE_REQUEST, manager.handle_mode_request)
        
        await manager.start()
        yield manager
        await manager.stop()
    
    @pytest.fixture
    async def music_controller(self, event_bus):
        """Music controller fixture with mocked VLC."""
        with patch('vlc.MediaPlayer'), patch('vlc.Instance'):
            controller = MusicControllerService(event_bus, "test_assets/music")
            await controller.start()
            yield controller
            await controller.stop()
    
    @retry(max_attempts=3)
    @pytest.mark.asyncio
    async def test_mode_change_commands(
        self,
        event_bus,
        cli_service,
        mode_manager,
        mock_io,
        event_synchronizer
    ):
        """Test that mode change commands propagate correctly through the event system."""
        # Emit the initial mode directly to ensure test stability
        await event_bus.emit(
            EventTopics.SYSTEM_MODE_CHANGE,
            {"mode": SystemMode.IDLE.value}
        )
        
        # Give a short grace period for event propagation
        await asyncio.sleep(0.1)
        
        # Get the initial mode from the manager
        assert mode_manager.current_mode == SystemMode.IDLE
        
        # Send "engage" command through CLI
        await mock_io["input_queue"].put("engage")
        
        # Give time for command processing
        await asyncio.sleep(0.2)
        
        # Wait for the mode to change to INTERACTIVE
        mode_changed_data = await event_synchronizer.wait_for_event(
            EventTopics.SYSTEM_MODE_CHANGE, timeout=1.0
        )
        assert mode_changed_data.get("mode") == SystemMode.INTERACTIVE.value
        
        # Send "ambient" command through CLI
        await mock_io["input_queue"].put("a")  # Using shortcut
        
        # Give time for command processing
        await asyncio.sleep(0.2)
        
        # Wait for the mode to change to AMBIENT
        mode_changed_data = await event_synchronizer.wait_for_event(
            EventTopics.SYSTEM_MODE_CHANGE, timeout=1.0
        )
        assert mode_changed_data.get("mode") == SystemMode.AMBIENT.value
        
        # Send "disengage" command through CLI
        await mock_io["input_queue"].put("d")  # Using shortcut
        
        # Give time for command processing
        await asyncio.sleep(0.2)
        
        # Wait for the mode to change back to IDLE
        mode_changed_data = await event_synchronizer.wait_for_event(
            EventTopics.SYSTEM_MODE_CHANGE, timeout=1.0
        )
        assert mode_changed_data.get("mode") == SystemMode.IDLE.value
    
    @retry(max_attempts=3)
    @pytest.mark.asyncio
    async def test_music_control_commands(
        self,
        event_bus,
        cli_service,
        music_controller,
        mock_io,
        event_synchronizer
    ):
        """Test that music control commands propagate correctly through the event system."""
        # Send "list music" command
        await mock_io["input_queue"].put("list music")
        
        # Wait for the music list event
        await event_synchronizer.wait_for_event(
            EventTopics.MUSIC_COMMAND, timeout=1.0
        )
        
        # Send "play music test track" command
        await mock_io["input_queue"].put("play music test track")
        
        # Wait for the play request event
        play_request_data = await event_synchronizer.wait_for_event(
            EventTopics.MUSIC_COMMAND, timeout=1.0
        )
        assert "play" in play_request_data.get("raw_input", "")
        assert "test track" in play_request_data.get("raw_input", "")
        
        # Send "stop music" command
        await mock_io["input_queue"].put("stop music")
        
        # Wait for the stop request event
        await event_synchronizer.wait_for_event(
            EventTopics.MUSIC_COMMAND, timeout=1.0
        )
    
    @retry(max_attempts=3)
    @pytest.mark.asyncio
    async def test_command_response_handling(
        self,
        event_bus,
        cli_service,
        mock_io
    ):
        """Test that command responses are correctly handled and displayed."""
        # Send a test response through the event bus
        await event_bus.emit(
            EventTopics.CLI_RESPONSE,
            {"message": "Test response message"}
        )
        
        # Small delay to allow output processing
        await asyncio.sleep(0.1)
        
        # Check that the response message was output
        assert any("Test response message" in output for output in mock_io["output_list"])
        
        # Send an error response
        await event_bus.emit(
            EventTopics.CLI_RESPONSE,
            {"error": "Test error message"}
        )
        
        # Small delay to allow output processing
        await asyncio.sleep(0.1)
        
        # Check that the error message was output
        assert any("Test error message" in output for output in mock_io["error_list"])
    
    @retry(max_attempts=3)
    @pytest.mark.asyncio
    async def test_cli_shutdown_command(
        self,
        event_bus,
        cli_service,
        mock_io,
        event_synchronizer
    ):
        """Test that the quit command emits system shutdown event."""
        # Create a mocked shutdown handler
        shutdown_handler = AsyncMock()
        await event_bus.on(EventTopics.SYSTEM_SHUTDOWN, shutdown_handler)
        
        # Send quit command through CLI
        await mock_io["input_queue"].put("quit")
        
        # Give time for command processing
        await asyncio.sleep(0.5)
        
        # Check that system shutdown was emitted
        shutdown_handler.assert_called_once()
        
        # Force stop CLI service for this test
        await cli_service.stop()
        
        # Verify that the CLI service is stopped
        assert cli_service._running == False 