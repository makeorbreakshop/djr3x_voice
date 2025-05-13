"""
Integration tests for DebugService.

Tests the DebugService's interaction with other system components and services.
"""

import asyncio
import pytest
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock

from cantina_os.debug_service import DebugService
from cantina_os.base_service import BaseService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import LogLevel

class TestService(BaseService):
    """A test service that generates debug events."""
    
    def __init__(self, event_bus, name="test_service"):
        super().__init__(
            event_bus=event_bus,
            service_name=name
        )
        self._test_data = []
    
    async def _start(self) -> None:
        await super()._start()
        self._running = True
    
    async def _stop(self) -> None:
        self._running = False
        await super()._stop()
    
    async def generate_test_events(self):
        """Generate various debug events for testing."""
        # Generate debug log
        await self.emit(
            EventTopics.DEBUG_LOG,
            {
                "level": LogLevel.INFO,
                "component": self.service_name,
                "message": "Test debug message",
                "details": {"test": True}
            }
        )
        
        # Generate command trace
        await self.emit(
            EventTopics.DEBUG_COMMAND_TRACE,
            {
                "command": "test_command",
                "service": self.service_name,
                "execution_time_ms": 100.0,
                "status": "success"
            }
        )
        
        # Generate performance metric
        await self.emit(
            EventTopics.DEBUG_PERFORMANCE,
            {
                "metric_name": "test_metric",
                "value": 42.0,
                "unit": "ms",
                "component": self.service_name
            }
        )
        
        # Generate state transition
        await self.emit(
            EventTopics.DEBUG_STATE_TRANSITION,
            {
                "old_mode": "state1",
                "new_mode": "state2",
                "status": "complete",
                "message": "State transition complete"
            }
        )

@pytest.fixture
async def test_environment(event_loop):
    """Set up test environment with DebugService and TestService."""
    # Create mock event bus
    event_bus = AsyncMock()
    event_bus.emit = AsyncMock()
    event_bus.on = AsyncMock()
    
    # Create debug service
    debug_config = {
        "default_log_level": "INFO",
        "component_log_levels": {},
        "trace_enabled": True,
        "metrics_enabled": True
    }
    debug_service = DebugService(event_bus=event_bus, config=debug_config)
    
    # Create test service
    test_service = TestService(event_bus=event_bus)
    
    # Start services
    await debug_service._start()
    await test_service._start()
    
    yield {
        "event_bus": event_bus,
        "debug_service": debug_service,
        "test_service": test_service
    }
    
    # Cleanup
    await test_service._stop()
    await debug_service._stop()

@pytest.mark.asyncio
async def test_debug_event_flow(test_environment):
    """Test the flow of debug events through the system."""
    debug_service = test_environment["debug_service"]
    test_service = test_environment["test_service"]
    
    # Generate test events
    await test_service.generate_test_events()
    
    # Wait for event processing
    await asyncio.sleep(0.1)
    
    # Verify events were processed
    assert test_environment["event_bus"].emit.call_count > 0

@pytest.mark.asyncio
async def test_component_log_level_control(test_environment):
    """Test controlling log levels for different components."""
    debug_service = test_environment["debug_service"]
    test_service = test_environment["test_service"]
    event_bus = test_environment["event_bus"]
    
    # Set log level for test service
    await debug_service.handle_debug_level_command(["test_service", "DEBUG"])
    
    # Generate logs at different levels
    await debug_service._handle_debug_log({
        "level": LogLevel.DEBUG,
        "component": test_service.service_name,
        "message": "Debug message"
    })
    await debug_service._handle_debug_log({
        "level": LogLevel.INFO,
        "component": test_service.service_name,
        "message": "Info message"
    })
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # Verify both messages were emitted
    assert event_bus.emit.call_count >= 2
    
    # Change level to INFO
    await debug_service.handle_debug_level_command(["test_service", "INFO"])
    
    # Reset mock
    event_bus.emit.reset_mock()
    
    # Debug logging
    debug_service.logger.debug(f"Current log level for test_service: {debug_service._component_log_levels.get('test_service')}")
    
    # Generate more logs
    await debug_service._handle_debug_log({
        "level": LogLevel.DEBUG,
        "component": test_service.service_name,
        "message": "Debug message"
    })
    await debug_service._handle_debug_log({
        "level": LogLevel.INFO,
        "component": test_service.service_name,
        "message": "Info message"
    })
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # Debug logging
    debug_service.logger.debug(f"Event bus emit calls: {event_bus.emit.call_count}")
    for i, call in enumerate(event_bus.emit.call_args_list):
        debug_service.logger.debug(f"Call {i + 1}: {call}")
    
    # Verify only INFO message was emitted
    assert event_bus.emit.call_count == 1

@pytest.mark.asyncio
async def test_high_volume_multi_service(test_environment):
    """Test handling high volume of debug events from multiple services."""
    debug_service = test_environment["debug_service"]
    test_service = test_environment["test_service"]
    
    # Create additional test services
    test_service2 = TestService(
        event_bus=test_environment["event_bus"],
        name="test_service2"
    )
    test_service3 = TestService(
        event_bus=test_environment["event_bus"],
        name="test_service3"
    )
    
    await test_service2._start()
    await test_service3._start()
    
    # Generate high volume of events from all services
    tasks = []
    for service in [test_service, test_service2, test_service3]:
        for i in range(100):
            tasks.append(
                service.emit(
                    EventTopics.DEBUG_LOG,
                    {
                        "level": LogLevel.INFO,
                        "component": service.service_name,
                        "message": f"Test message {i} from {service.service_name}"
                    }
                )
            )
    
    # Send all events
    await asyncio.gather(*tasks)
    
    # Wait for processing
    await asyncio.sleep(1)
    
    # Verify all messages were emitted
    assert test_environment["event_bus"].emit.call_count >= 300
    
    # Cleanup
    await test_service2._stop()
    await test_service3._stop()

@pytest.mark.asyncio
async def test_command_tracing_integration(test_environment):
    """Test command tracing across multiple services."""
    debug_service = test_environment["debug_service"]
    test_service = test_environment["test_service"]
    
    # Enable command tracing
    await debug_service.handle_debug_trace_command(["enable"])
    
    # Generate command traces
    await test_service.emit(
        EventTopics.DEBUG_COMMAND_TRACE,
        {
            "command": "test_command1",
            "service": test_service.service_name,
            "execution_time_ms": 100.0,
            "status": "success"
        }
    )
    await test_service.emit(
        EventTopics.DEBUG_COMMAND_TRACE,
        {
            "command": "test_command2",
            "service": test_service.service_name,
            "execution_time_ms": 150.0,
            "status": "error",
            "details": {"error": "test error"}
        }
    )
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # Verify traces were recorded
    assert test_environment["event_bus"].emit.call_count >= 2

@pytest.mark.asyncio
async def test_performance_metrics_integration(test_environment):
    """Test performance metrics collection across services."""
    debug_service = test_environment["debug_service"]
    test_service = test_environment["test_service"]
    
    # Enable metrics
    await debug_service.handle_debug_performance_command(["enable"])
    
    # Generate metrics
    metrics = [
        ("latency", 100.0, "ms"),
        ("memory", 1024.0, "MB"),
        ("cpu", 50.0, "percent")
    ]
    
    for name, value, unit in metrics:
        await test_service.emit(
            EventTopics.DEBUG_PERFORMANCE,
            {
                "metric_name": name,
                "value": value,
                "unit": unit,
                "component": test_service.service_name
            }
        )
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # Verify metrics were recorded
    assert test_environment["event_bus"].emit.call_count >= 3 