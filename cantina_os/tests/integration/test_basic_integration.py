"""Integration tests for core service communication."""
import pytest
import asyncio
from typing import Dict, List, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

from cantina_os.base_service import BaseService
from cantina_os.event_bus import EventBus
from cantina_os.event_topics import EventTopics
from cantina_os.services.yoda_mode_manager_service import YodaModeManagerService, SystemMode
from cantina_os.services.cli_service import CLIService
from cantina_os.services.command_dispatcher_service import CommandDispatcherService
from tests.utils.event_synchronizer import EventSynchronizer
from tests.utils.resource_monitor import ResourceMonitor

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def event_bus() -> AsyncGenerator[EventBus, None]:
    """Create a fresh event bus for each test with proper async cleanup."""
    bus = EventBus()
    try:
        yield bus
    finally:
        # Ensure proper cleanup of all handlers
        await asyncio.sleep(0.1)  # Small delay to allow pending events to complete
        bus.clear_all_handlers()
        await asyncio.sleep(0.1)  # Ensure cleanup completes

@pytest.fixture
async def event_sync(event_bus) -> AsyncGenerator[EventSynchronizer, None]:
    """Create an event synchronizer with improved timing management."""
    sync = EventSynchronizer(event_bus, grace_period_ms=500)  # Increased grace period
    try:
        yield sync
    finally:
        await sync.cleanup()  # Ensure async cleanup is awaited

@pytest.fixture
async def resource_monitor() -> AsyncGenerator[ResourceMonitor, None]:
    """Create a resource monitor with enhanced cleanup verification."""
    monitor = ResourceMonitor()
    monitor.capture_baseline_metrics()
    try:
        yield monitor
    finally:
        # Check for uncleaned resources
        uncleaned = monitor.get_uncleaned_resources()
        if uncleaned:
            print(f"Warning: {len(uncleaned)} uncleaned resources detected:")
            for res in uncleaned:
                print(f"  - {res['type']}:{res['id']} (created at {res['created_at']})")
            # Optional: Force cleanup of remaining resources
            await monitor.force_cleanup()

@pytest.fixture
async def mock_io() -> AsyncGenerator[Dict, None]:
    """Create mock I/O functions with proper async handling."""
    input_queue = asyncio.Queue()
    output_list = []
    error_list = []
    
    async def mock_input():
        try:
            return await asyncio.wait_for(input_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            raise TimeoutError("Mock input timeout")
        
    def mock_output(text, end="\n"):
        output_list.append(text)
        
    def mock_error(text, end="\n"):
        error_list.append(text)
        
    io_dict = {
        "input": mock_input,
        "output": mock_output,
        "error": mock_error,
        "input_queue": input_queue,
        "output_list": output_list,
        "error_list": error_list
    }
    
    try:
        yield io_dict
    finally:
        # Clear any remaining items in the queue
        while not input_queue.empty():
            try:
                input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

@pytest.fixture
async def yoda_mode_manager(event_bus) -> AsyncGenerator[YodaModeManagerService, None]:
    """Create and start a YodaModeManagerService with proper state verification."""
    service = YodaModeManagerService(event_bus)
    try:
        yield service
    finally:
        if service.is_running:
            await service.stop()
            await asyncio.sleep(0.1)  # Ensure cleanup completes

@pytest.fixture
async def cli_service(event_bus, mock_io) -> AsyncGenerator[CLIService, None]:
    """Create and start a CLIService with enhanced state verification."""
    service = CLIService(
        event_bus,
        io_functions={
            'input': mock_io['input'],
            'output': mock_io['output'],
            'error': mock_io['error']
        }
    )
    try:
        yield service
    finally:
        if service.is_running:
            await service.stop()
            await asyncio.sleep(0.1)  # Ensure cleanup completes

@pytest.fixture
async def command_dispatcher(event_bus) -> AsyncGenerator[CommandDispatcherService, None]:
    """Create and start a CommandDispatcherService with proper cleanup."""
    service = CommandDispatcherService(event_bus)
    try:
        yield service
    finally:
        if service.is_running:
            await service.stop()
            await asyncio.sleep(0.1)  # Ensure cleanup completes

async def test_basic_service_communication(
    event_bus,
    event_sync,
    resource_monitor,
    yoda_mode_manager,
    cli_service,
    command_dispatcher,
    mock_io
):
    """
    Test basic communication between core services with improved error handling.
    
    This test verifies:
    1. Services start up successfully
    2. Mode transitions work through CLI commands
    3. Events are properly propagated
    4. Services respond to mode changes
    """
    try:
        # Subscribe to mode change events before starting services
        await event_sync._subscribe_to_event(EventTopics.SYSTEM_MODE_CHANGE)
        await asyncio.sleep(0.1)  # Small delay to ensure subscription is active
        
        # Start services in order
        await yoda_mode_manager.start()
        await cli_service.start()
        await command_dispatcher.start()
        
        # Verify all services started successfully
        assert yoda_mode_manager.is_running, "YodaModeManager not running"
        assert cli_service.is_running, "CLIService not running"
        assert command_dispatcher.is_running, "CommandDispatcher not running"
        
        # Wait for initial IDLE mode after startup
        initial_mode = await event_sync.wait_for_event(
            EventTopics.SYSTEM_MODE_CHANGE,
            timeout=5.0
        )
        assert initial_mode['new_mode'] == SystemMode.IDLE.name, "System not in IDLE mode"
        
        # Simulate CLI command for mode transition
        await mock_io["input_queue"].put("engage")
        
        # Wait for mode change to INTERACTIVE with retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                interactive_mode = await event_sync.wait_for_event(
                    EventTopics.SYSTEM_MODE_CHANGE,
                    timeout=5.0,
                    condition=lambda data: data['new_mode'] == SystemMode.INTERACTIVE.name
                )
                assert interactive_mode['old_mode'] == SystemMode.IDLE.name
                assert interactive_mode['new_mode'] == SystemMode.INTERACTIVE.name
                break
            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.5)  # Wait before retry
        
        # Verify mode manager state
        assert yoda_mode_manager.current_mode == SystemMode.INTERACTIVE
        
        # Simulate ambient mode command
        await mock_io["input_queue"].put("ambient")
        
        # Wait for mode change to AMBIENT with retry
        for attempt in range(max_retries):
            try:
                ambient_mode = await event_sync.wait_for_event(
                    EventTopics.SYSTEM_MODE_CHANGE,
                    timeout=5.0,
                    condition=lambda data: data['new_mode'] == SystemMode.AMBIENT.name
                )
                assert ambient_mode['old_mode'] == SystemMode.INTERACTIVE.name
                assert ambient_mode['new_mode'] == SystemMode.AMBIENT.name
                break
            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.5)  # Wait before retry
        
        # Verify mode manager state
        assert yoda_mode_manager.current_mode == SystemMode.AMBIENT
        
    except Exception as e:
        # Log the current state for debugging
        print(f"Test failed with error: {str(e)}")
        print(f"YodaModeManager state: {yoda_mode_manager.current_mode}")
        print(f"CLI output: {mock_io['output_list']}")
        print(f"CLI errors: {mock_io['error_list']}")
        raise