"""
Tests for compound command registration patterns.

These tests verify that:
1. Command decorators work correctly
2. Commands are properly registered with CommandDispatcher
3. Payload standardization works as expected
4. Registration patterns are consistent across services
"""

import pytest
from typing import Dict, Any
from ...core.event_topics import EventTopics
from ...utils.command_decorators import (
    compound_command,
    validate_compound_command,
    command_error_handler,
    CompoundCommandRegistry
)
from ..test_helpers import CommandTestMixin, AsyncTestTimeout, wait_for_condition
from ..mocks.mock_event_bus import MockEventBus


class TestService:
    """Test service for verifying command registration."""
    
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self._default_command_topic = EventTopics.CLI_COMMAND
        self.command_called = False
        self.last_payload = None
    
    @compound_command("test command")
    @validate_compound_command(min_args=1, max_args=2, required_args=["arg1"])
    @command_error_handler
    async def handle_test_command(self, payload: Dict[str, Any]):
        """Test command handler."""
        self.command_called = True
        self.last_payload = payload
        await self.event_bus.emit(EventTopics.CLI_RESPONSE, {
            "success": True,
            "message": f"Test command handled with args: {payload['args']}"
        })


class TestCommandRegistration(CommandTestMixin):
    """Tests for compound command registration patterns."""
    
    @pytest.fixture(autouse=True)
    async def setup(self):
        """Set up test environment."""
        self.event_bus = MockEventBus()
        self.test_service = TestService(self.event_bus)
        self.event_collector.clear()
        
        # Hook event collector into mock bus
        original_emit = self.event_bus.emit
        async def collect_and_emit(topic: str, payload: Dict[str, Any] = None):
            self.event_collector.add_event(topic, payload)
            await original_emit(topic, payload)
        self.event_bus.emit = collect_and_emit
    
    @pytest.mark.asyncio
    async def test_command_registration(self):
        """Test that commands are properly registered and discoverable."""
        # Get command registry
        from ...utils.command_decorators import _command_registry
        
        # Verify test command is registered
        handler_info = _command_registry.get_handler_info("test command")
        assert handler_info is not None
        assert handler_info["handler_method"] == "handle_test_command"
        
        # Verify command list includes our test command
        commands = _command_registry.list_commands()
        assert "test command" in commands
    
    @pytest.mark.asyncio
    async def test_command_payload_standardization(self):
        """Test that command payloads are properly standardized."""
        async with AsyncTestTimeout(1.0):
            # Send command with various payload formats
            payloads = [
                # Standard format
                {
                    "command": "test",
                    "subcommand": "command",
                    "args": ["arg1_value"],
                    "raw_input": "test command arg1_value"
                },
                # Action-based format
                {
                    "action": "test",
                    "args": ["arg1_value"],
                    "raw_input": "test command arg1_value"
                },
                # Minimal format
                {
                    "command": "test command",
                    "args": ["arg1_value"]
                }
            ]
            
            for payload in payloads:
                # Clear previous state
                self.test_service.command_called = False
                self.test_service.last_payload = None
                
                # Send command
                await self.event_bus.emit(EventTopics.CLI_COMMAND, payload)
                
                # Wait for command to be processed
                await wait_for_condition(lambda: self.test_service.command_called)
                
                # Verify standardized payload
                assert self.test_service.last_payload is not None
                assert "command" in self.test_service.last_payload
                assert "args" in self.test_service.last_payload
                assert "raw_input" in self.test_service.last_payload
                assert len(self.test_service.last_payload["args"]) == 1
    
    @pytest.mark.asyncio
    async def test_command_validation(self):
        """Test that command validation works correctly."""
        async with AsyncTestTimeout(1.0):
            # Test with missing required arg
            await self.send_command("test", "command", [])
            assert self.verify_error("required argument: arg1")
            
            # Test with too many args
            await self.send_command("test", "command", ["arg1", "arg2", "arg3"])
            assert self.verify_error("maximum 2 arguments")
            
            # Test with correct args
            await self.send_command("test", "command", ["arg1_value"])
            assert self.verify_success("Test command handled")
    
    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test that command error handling works correctly."""
        async with AsyncTestTimeout(1.0):
            # Break the command handler
            original_handler = self.test_service.handle_test_command
            async def broken_handler(self, payload):
                raise Exception("Test error")
            self.test_service.handle_test_command = broken_handler.__get__(self.test_service)
            
            try:
                # Send command
                await self.send_command("test", "command", ["arg1_value"])
                
                # Verify error is caught and reported
                assert self.verify_error("Test error")
                
            finally:
                # Restore original handler
                self.test_service.handle_test_command = original_handler 