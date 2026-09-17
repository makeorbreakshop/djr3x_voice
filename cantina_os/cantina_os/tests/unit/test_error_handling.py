"""
Tests for error handling patterns in the command system.

These tests verify that:
1. Command errors are properly caught and reported
2. Error messages are helpful and consistent
3. Services handle errors gracefully
4. Error responses follow standard format
"""

import pytest
from typing import Dict, Any
from ...core.event_topics import EventTopics
from ...utils.command_decorators import (
    compound_command,
    validate_compound_command,
    command_error_handler
)
from ..test_helpers import CommandTestMixin, AsyncTestTimeout, wait_for_condition
from ..mocks.mock_event_bus import MockEventBus


class ErrorTestService:
    """Test service for verifying error handling patterns."""
    
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self._default_command_topic = EventTopics.CLI_COMMAND
    
    @compound_command("validation error")
    @validate_compound_command(min_args=2, required_args=["arg1", "arg2"])
    @command_error_handler
    async def handle_validation_error(self, payload: Dict[str, Any]):
        """Command that requires validation."""
        await self.event_bus.emit(EventTopics.CLI_RESPONSE, {
            "success": True,
            "message": "Validation passed"
        })
    
    @compound_command("runtime error")
    @command_error_handler
    async def handle_runtime_error(self, payload: Dict[str, Any]):
        """Command that raises a runtime error."""
        raise RuntimeError("Something went wrong")
    
    @compound_command("value error")
    @command_error_handler
    async def handle_value_error(self, payload: Dict[str, Any]):
        """Command that raises a value error."""
        raise ValueError("Invalid value")
    
    @compound_command("type error")
    @command_error_handler
    async def handle_type_error(self, payload: Dict[str, Any]):
        """Command that raises a type error."""
        raise TypeError("Wrong type")
    
    @compound_command("custom error")
    @command_error_handler
    async def handle_custom_error(self, payload: Dict[str, Any]):
        """Command that raises a custom error."""
        class CustomError(Exception):
            pass
        raise CustomError("Custom error message")


class TestErrorHandling(CommandTestMixin):
    """Tests for error handling patterns."""
    
    @pytest.fixture(autouse=True)
    async def setup(self):
        """Set up test environment."""
        self.event_bus = MockEventBus()
        self.test_service = ErrorTestService(self.event_bus)
        self.event_collector.clear()
        
        # Hook event collector into mock bus
        original_emit = self.event_bus.emit
        async def collect_and_emit(topic: str, payload: Dict[str, Any] = None):
            self.event_collector.add_event(topic, payload)
            await original_emit(topic, payload)
        self.event_bus.emit = collect_and_emit
    
    @pytest.mark.asyncio
    async def test_validation_errors(self):
        """Test that validation errors are properly handled."""
        async with AsyncTestTimeout(1.0):
            # Test with missing args
            await self.send_command("validation", "error")
            assert self.verify_error("required argument")
            assert self.verify_error("arg1")
            assert self.verify_error("arg2")
            
            # Test with one missing arg
            await self.send_command("validation", "error", ["value1"])
            assert self.verify_error("required argument: arg2")
            
            # Test with correct args
            await self.send_command("validation", "error", ["value1", "value2"])
            assert self.verify_success("Validation passed")
    
    @pytest.mark.asyncio
    async def test_runtime_errors(self):
        """Test that runtime errors are properly handled."""
        async with AsyncTestTimeout(1.0):
            await self.send_command("runtime", "error")
            assert self.verify_error("Something went wrong")
            
            # Verify error response format
            error_response = self.event_collector.find_error_response()
            assert error_response is not None
            assert "success" in error_response
            assert not error_response["success"]
            assert "message" in error_response
            assert "error_type" in error_response
            assert error_response["error_type"] == "RuntimeError"
    
    @pytest.mark.asyncio
    async def test_value_errors(self):
        """Test that value errors are properly handled."""
        async with AsyncTestTimeout(1.0):
            await self.send_command("value", "error")
            assert self.verify_error("Invalid value")
            
            # Verify error response format
            error_response = self.event_collector.find_error_response()
            assert error_response is not None
            assert error_response["error_type"] == "ValueError"
    
    @pytest.mark.asyncio
    async def test_type_errors(self):
        """Test that type errors are properly handled."""
        async with AsyncTestTimeout(1.0):
            await self.send_command("type", "error")
            assert self.verify_error("Wrong type")
            
            # Verify error response format
            error_response = self.event_collector.find_error_response()
            assert error_response is not None
            assert error_response["error_type"] == "TypeError"
    
    @pytest.mark.asyncio
    async def test_custom_errors(self):
        """Test that custom errors are properly handled."""
        async with AsyncTestTimeout(1.0):
            await self.send_command("custom", "error")
            assert self.verify_error("Custom error message")
            
            # Verify error response format
            error_response = self.event_collector.find_error_response()
            assert error_response is not None
            assert error_response["error_type"] == "CustomError"
    
    @pytest.mark.asyncio
    async def test_error_response_format(self):
        """Test that all error responses follow standard format."""
        async with AsyncTestTimeout(1.0):
            # Test all error types
            commands = [
                ("validation", "error"),
                ("runtime", "error"),
                ("value", "error"),
                ("type", "error"),
                ("custom", "error")
            ]
            
            for command, subcommand in commands:
                await self.send_command(command, subcommand)
                
                # Verify standard error response format
                error_response = self.event_collector.find_error_response()
                assert error_response is not None
                assert isinstance(error_response, dict)
                assert "success" in error_response
                assert not error_response["success"]
                assert "message" in error_response
                assert isinstance(error_response["message"], str)
                assert "error_type" in error_response
                assert isinstance(error_response["error_type"], str) 