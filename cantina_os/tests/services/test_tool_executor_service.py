"""Unit tests for the ToolExecutorService."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from cantina_os.services.tool_executor_service import ToolExecutorService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    ToolRegistrationPayload,
    ToolExecutionRequestPayload,
    ToolExecutionResultPayload,
    ServiceStatus,
    CommandCallPayload,
    CommandResultPayload,
    BaseEventPayload,
)
from cantina_os.test_helpers import EventSynchronizer

# Mark all tests as async
pytestmark = pytest.mark.asyncio

@pytest.fixture
def event_synchronizer():
    """Create an event synchronizer for timing-sensitive tests."""
    return EventSynchronizer(timeout=5.0)  # Increase timeout to 5 seconds

@pytest.fixture
def event_bus():
    """Create a mock event bus with proper async behavior."""
    bus = AsyncMock()
    
    # Track emitted events for verification
    bus.emitted_events = []
    
    async def mock_emit(topic, payload):
        bus.emitted_events.append((topic, payload))
        await asyncio.sleep(0.01)  # Simulate network delay
    bus.emit = AsyncMock(side_effect=mock_emit)
    
    # Make on() return immediately
    async def mock_on(topic, handler):
        pass
    bus.on = AsyncMock(side_effect=mock_on)
    
    # Add remove_listener method
    def mock_remove_listener(topic, handler):
        pass
    bus.remove_listener = MagicMock(side_effect=mock_remove_listener)
    
    return bus

@pytest.fixture
async def service(event_bus):
    """Create a ToolExecutorService instance without starting it."""
    return ToolExecutorService(event_bus)

@pytest.fixture
async def running_service(service):
    """Create and start a ToolExecutorService instance with proper cleanup."""
    try:
        await service.start()
        yield service
    finally:
        # Ensure service is stopped even if test fails
        if service.is_started:
            await service.stop()

@pytest.fixture
def sample_tool():
    """Create a sample tool function."""
    async def tool_func(arg1: str, arg2: int = 0):
        return f"Processed {arg1} with {arg2}"
    return tool_func

@pytest.fixture
def sync_tool():
    """Create a sample synchronous tool function."""
    def tool_func(arg1: str, arg2: int = 0):
        return f"Sync processed {arg1} with {arg2}"
    return tool_func

@pytest.fixture
def failing_tool():
    """Create a tool that raises an error."""
    async def tool_func():
        raise ValueError("Tool failed")
    return tool_func

async def test_service_lifecycle(service, event_bus, event_synchronizer):
    """Test service startup and shutdown with proper event verification."""
    # Start service
    await service.start()
    
    # Wait for startup events
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.SERVICE_STATUS_UPDATE and "started" in str(e[1]["message"])
    )
    assert service.is_started
    
    # Stop service
    await service.stop()
    
    # Wait for shutdown events
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.SERVICE_STATUS_UPDATE and "stopping" in str(e[1]["message"])
    )
    assert not service.is_started

async def test_tool_registration(running_service, event_bus, sample_tool, event_synchronizer):
    """Test registering a new tool with proper event verification."""
    payload = ToolRegistrationPayload(
        tool_name="test_tool",
        tool_function=sample_tool,
        description="A test tool"
    )
    
    await running_service._handle_tool_registration(payload)
    
    # Wait for registration event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.SERVICE_STATUS_UPDATE and "Registered tool" in str(e[1]["message"])
    )
    
    assert "test_tool" in running_service.registered_tools
    assert running_service.registered_tools["test_tool"] == sample_tool

async def test_tool_registration_invalid(running_service, event_bus, event_synchronizer):
    """Test registering an invalid tool with proper error handling."""
    # Create a non-callable object
    non_callable = "not_a_function"
    
    # Attempt to register invalid tool
    try:
        payload = ToolRegistrationPayload(
            tool_name="invalid_tool",
            tool_function=non_callable,
            description="Invalid tool"
        )
        pytest.fail("Expected validation error was not raised")
    except Exception as e:
        # Verify tool was not registered
        assert "invalid_tool" not in running_service.registered_tools
        assert "callable" in str(e).lower()

async def test_tool_execution_async(running_service, event_bus, sample_tool, event_synchronizer):
    """Test executing an async tool with proper event verification."""
    # Register tool
    await running_service._handle_tool_registration(ToolRegistrationPayload(
        tool_name="test_tool",
        tool_function=sample_tool
    ))
    
    # Execute tool
    request_payload = ToolExecutionRequestPayload(
        request_id="test_req_1",
        tool_name="test_tool",
        arguments={"arg1": "test", "arg2": 42}
    )
    
    await running_service._handle_tool_execution_request(request_payload)
    
    # Wait for result event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.TOOL_CALL_RESULT and e[1]["request_id"] == "test_req_1"
    )
    
    # Verify result
    result_event = next(
        e for e in event_bus.emitted_events 
        if e[0] == EventTopics.TOOL_CALL_RESULT and e[1]["request_id"] == "test_req_1"
    )
    assert result_event[1]["success"]
    assert result_event[1]["result"] == "Processed test with 42"

async def test_tool_execution_sync(running_service, event_bus, sync_tool, event_synchronizer):
    """Test executing a synchronous tool with proper event verification."""
    # Register tool
    await running_service._handle_tool_registration(ToolRegistrationPayload(
        tool_name="sync_tool",
        tool_function=sync_tool
    ))
    
    # Execute tool
    request_payload = ToolExecutionRequestPayload(
        request_id="test_req_2",
        tool_name="sync_tool",
        arguments={"arg1": "test", "arg2": 42}
    )
    
    await running_service._handle_tool_execution_request(request_payload)
    
    # Wait for result event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.TOOL_CALL_RESULT and e[1]["request_id"] == "test_req_2"
    )
    
    # Verify result
    result_event = next(
        e for e in event_bus.emitted_events 
        if e[0] == EventTopics.TOOL_CALL_RESULT and e[1]["request_id"] == "test_req_2"
    )
    assert result_event[1]["success"]
    assert result_event[1]["result"] == "Sync processed test with 42"

async def test_tool_execution_unregistered(running_service, event_bus, event_synchronizer):
    """Test executing an unregistered tool with proper error handling."""
    request_payload = ToolExecutionRequestPayload(
        request_id="test_req_3",
        tool_name="nonexistent_tool",
        arguments={"arg1": "test"}
    )
    
    await running_service._handle_tool_execution_request(request_payload)
    
    # Wait for error event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.TOOL_CALL_ERROR and e[1]["request_id"] == "test_req_3"
    )
    
    error_event = next(
        e for e in event_bus.emitted_events 
        if e[0] == EventTopics.TOOL_CALL_ERROR and e[1]["request_id"] == "test_req_3"
    )
    assert not error_event[1]["success"]
    assert "not registered" in error_event[1]["error"]

@patch('asyncio.wait_for')
async def test_tool_execution_timeout(mock_wait_for, running_service, event_bus, event_synchronizer):
    """Test tool execution timeout with proper error handling."""
    mock_wait_for.side_effect = asyncio.TimeoutError()
    
    async def slow_tool():
        await asyncio.sleep(2)
        return "Done"
    
    # Register tool with short timeout
    running_service.execution_timeout = 0.1
    await running_service._handle_tool_registration(ToolRegistrationPayload(
        tool_name="slow_tool",
        tool_function=slow_tool
    ))
    
    request_payload = ToolExecutionRequestPayload(
        request_id="test_req_4",
        tool_name="slow_tool",
        arguments={}
    )
    
    await running_service._handle_tool_execution_request(request_payload)
    
    # Wait for error event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.TOOL_CALL_ERROR and e[1]["request_id"] == "test_req_4"
    )
    
    error_event = next(
        e for e in event_bus.emitted_events 
        if e[0] == EventTopics.TOOL_CALL_ERROR and e[1]["request_id"] == "test_req_4"
    )
    assert not error_event[1]["success"]
    assert "timed out" in error_event[1]["error"]

async def test_tool_execution_error(running_service, event_bus, failing_tool, event_synchronizer):
    """Test tool execution error handling with proper event verification."""
    await running_service._handle_tool_registration(ToolRegistrationPayload(
        tool_name="failing_tool",
        tool_function=failing_tool
    ))
    
    request_payload = ToolExecutionRequestPayload(
        request_id="test_req_5",
        tool_name="failing_tool",
        arguments={}
    )
    
    await running_service._handle_tool_execution_request(request_payload)
    
    # Wait for error event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.TOOL_CALL_ERROR and e[1]["request_id"] == "test_req_5"
    )
    
    error_event = next(
        e for e in event_bus.emitted_events 
        if e[0] == EventTopics.TOOL_CALL_ERROR and e[1]["request_id"] == "test_req_5"
    )
    assert not error_event[1]["success"]
    assert "Tool failed" in error_event[1]["error"]

async def test_event_bus_error_recovery(running_service, event_bus, sample_tool, event_synchronizer):
    """Test recovery from event bus failures."""
    # Make emit fail temporarily
    event_bus.emit.side_effect = Exception("Network error")
    
    payload = ToolRegistrationPayload(
        tool_name="test_tool",
        tool_function=sample_tool,
        description="A test tool"
    )
    
    # Should not raise exception
    await running_service._handle_tool_registration(payload)
    
    # Tool should still be registered despite event failure
    assert "test_tool" in running_service.registered_tools
    
    # Restore emit and verify service still works
    async def mock_emit(topic, payload):
        event_bus.emitted_events.append((topic, payload))
        await asyncio.sleep(0.01)
    event_bus.emit.side_effect = mock_emit
    
    await running_service._handle_tool_registration(payload)
    
    # Wait for success event
    await event_synchronizer.wait_for_event(
        event_bus.emitted_events,
        lambda e: e[0] == EventTopics.SERVICE_STATUS_UPDATE and "Registered tool" in str(e[1]["message"])
    ) 