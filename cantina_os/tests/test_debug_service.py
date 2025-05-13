"""
Unit tests for DebugService.

Tests the core functionality of the DebugService class.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from cantina_os.debug_service import DebugService, DebugServiceConfig
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import (
    LogLevel,
    DebugLogPayload,
    CommandTracePayload,
    PerformanceMetricPayload,
    DebugConfigPayload
)

@pytest.fixture
def event_bus():
    """Create a mock event bus."""
    bus = AsyncMock()
    bus.emit = AsyncMock()
    bus.on = AsyncMock()
    return bus

@pytest.fixture
def config():
    """Create a test configuration."""
    return {
        "default_log_level": "INFO",
        "component_log_levels": {
            "test_component": "DEBUG"
        },
        "trace_enabled": True,
        "metrics_enabled": True,
        "log_file": None
    }

@pytest.fixture
def debug_service(event_bus, config):
    """Create a DebugService instance for testing."""
    service = DebugService(event_bus=event_bus, config=config)
    return service

@pytest.mark.asyncio
async def test_debug_service_initialization(debug_service, config):
    """Test DebugService initialization."""
    # Start the service
    await debug_service._start()
    
    assert debug_service._default_log_level == LogLevel.INFO
    assert debug_service._component_log_levels["test_component"] == LogLevel.DEBUG
    assert debug_service._trace_enabled is True
    assert debug_service._metrics_enabled is True
    assert debug_service._log_queue is not None
    assert debug_service._log_task is not None

@pytest.mark.asyncio
async def test_debug_log_handling(debug_service):
    """Test debug log event handling."""
    await debug_service._start()
    
    # Create a test log payload
    payload = {
        "level": LogLevel.INFO,
        "component": "test_component",
        "message": "Test log message",
        "details": {"key": "value"}
    }
    
    # Send a debug log event
    await debug_service._handle_debug_log(payload)
    
    # Verify log was processed
    assert debug_service._log_queue.qsize() > 0

@pytest.mark.asyncio
async def test_command_tracing(debug_service):
    """Test command tracing functionality."""
    await debug_service._start()
    
    # Create a test command trace
    payload = {
        "command": "test_command",
        "service": "test_service",
        "execution_time_ms": 100.0,
        "status": "success",
        "details": {"args": ["test"]}
    }
    
    # Send command trace event
    await debug_service._handle_command_trace(payload)
    
    # Verify trace was recorded
    assert debug_service.event_bus.emit.called

@pytest.mark.asyncio
async def test_performance_metrics(debug_service):
    """Test performance metrics collection."""
    await debug_service._start()
    
    # Create test metric
    payload = {
        "metric_name": "test_metric",
        "value": 42.0,
        "unit": "ms",
        "component": "test_component",
        "details": {"type": "latency"}
    }
    
    # Send metric event
    await debug_service._handle_performance_metric(payload)
    
    # Verify metric was recorded
    assert debug_service.event_bus.emit.called

@pytest.mark.asyncio
async def test_state_transition_tracking(debug_service):
    """Test state transition tracking."""
    await debug_service._start()
    
    # Create test transition
    payload = {
        "old_mode": "IDLE",
        "new_mode": "INTERACTIVE",
        "status": "complete",
        "message": "Mode transition complete"
    }
    
    # Send transition event
    await debug_service._handle_state_transition(payload)
    
    # Verify transition was tracked
    assert debug_service.event_bus.emit.called

@pytest.mark.asyncio
async def test_debug_level_command(debug_service):
    """Test debug level command handling."""
    await debug_service._start()
    
    # Test setting component log level
    result = await debug_service.handle_debug_level_command(["test_component", "DEBUG"])
    assert "success" in result["message"].lower()
    assert debug_service._component_log_levels["test_component"] == LogLevel.DEBUG

@pytest.mark.asyncio
async def test_debug_trace_command(debug_service):
    """Test debug trace command handling."""
    await debug_service._start()
    
    # Test enabling/disabling tracing
    result = await debug_service.handle_debug_trace_command(["enable"])
    assert debug_service._trace_enabled is True
    assert "enabled" in result["message"].lower()

@pytest.mark.asyncio
async def test_debug_performance_command(debug_service):
    """Test debug performance command handling."""
    await debug_service._start()
    
    # Test enabling/disabling metrics
    result = await debug_service.handle_debug_performance_command(["enable"])
    assert debug_service._metrics_enabled is True
    assert "enabled" in result["message"].lower()

@pytest.mark.asyncio
async def test_high_volume_logging(debug_service):
    """Test handling of high-volume logging."""
    await debug_service._start()
    
    # Generate many log messages rapidly
    messages = [f"Test message {i}" for i in range(1000)]
    
    # Send all messages
    for msg in messages:
        payload = {
            "level": LogLevel.INFO,
            "component": "test_component",
            "message": msg
        }
        await debug_service._handle_debug_log(payload)
    
    # Verify queue handling
    assert debug_service._log_queue.qsize() > 0
    
    # Wait for processing
    await asyncio.sleep(1)
    
    # Verify messages were processed
    assert debug_service._log_queue.qsize() == 0

@pytest.mark.asyncio
async def test_service_cleanup(debug_service):
    """Test proper cleanup during service shutdown."""
    await debug_service._start()
    
    # Add some data
    await debug_service._handle_debug_log({
        "level": LogLevel.INFO,
        "component": "test",
        "message": "test"
    })
    
    # Stop the service
    await debug_service._stop()
    
    # Verify cleanup
    assert debug_service._log_task.done()
    assert debug_service._log_queue.qsize() == 0

@pytest.mark.asyncio
async def test_config_updates(debug_service):
    """Test configuration updates through debug config events."""
    await debug_service._start()
    
    # Send config update
    payload = {
        "component": "test_component",
        "log_level": LogLevel.DEBUG,
        "enable_tracing": False,
        "enable_metrics": True
    }
    
    await debug_service._handle_debug_config(payload)
    
    # Verify config was updated
    assert debug_service._component_log_levels["test_component"] == LogLevel.DEBUG
    assert debug_service._trace_enabled is False
    assert debug_service._metrics_enabled is True 