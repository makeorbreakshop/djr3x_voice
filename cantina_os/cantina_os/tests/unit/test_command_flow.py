"""
Integration tests for command flow through the CantinaOS system.

These tests verify that commands flow correctly through:
1. CommandDispatcher
2. Service registration
3. Payload standardization
4. Error handling
"""

import pytest
import asyncio
from typing import Dict, Any
from ...core.event_topics import EventTopics
from ...event_bus import EventBus
from ...services.eye_light_controller import EyeLightControllerService
from ...utils.command_decorators import compound_command, validate_compound_command, command_error_handler
from ..test_helpers import CommandTestMixin, EventCollector, AsyncTestTimeout, wait_for_condition


class TestCommandFlow(CommandTestMixin):
    """Integration tests for command flow through the system."""
    
    @pytest.fixture(autouse=True)
    async def setup(self, mock_event_bus, eye_controller):
        """Set up test environment."""
        self.event_bus = mock_event_bus
        self.eye_controller = eye_controller
        self.event_collector = EventCollector()
        
        # Hook event collector into mock bus
        original_emit = self.event_bus.emit
        async def collect_and_emit(topic: str, payload: Dict[str, Any] = None):
            self.event_collector.add_event(topic, payload)
            await original_emit(topic, payload)
        self.event_bus.emit = collect_and_emit
        
        yield
        
        # Cleanup
        self.event_collector.clear()
    
    @pytest.mark.asyncio
    async def test_eye_test_command_flow(self):
        """Test that 'eye test' command flows correctly through the system."""
        
        async with AsyncTestTimeout(1.0):
            # Send command
            await self.send_command("eye", "test")
            
            # Wait for response
            await wait_for_condition(
                lambda: len(self.event_collector.find_events(EventTopics.CLI_RESPONSE)) > 0
            )
            
            # Verify success
            assert self.verify_success("Running eye test")
    
    @pytest.mark.asyncio
    async def test_eye_test_command_validation(self):
        """Test that invalid 'eye test' commands are properly validated and rejected."""
        
        async with AsyncTestTimeout(1.0):
            # Send command with invalid args
            await self.send_command("eye", "test", ["unexpected_arg"])
            
            # Wait for response
            await wait_for_condition(
                lambda: len(self.event_collector.find_events(EventTopics.CLI_RESPONSE)) > 0
            )
            
            # Verify error
            assert self.verify_error("unexpected arguments")
    
    @pytest.mark.asyncio
    async def test_eye_test_command_error_handling(self):
        """Test that errors in eye test command handling are properly caught and reported."""
        
        # Simulate hardware error
        original_test = self.eye_controller._run_eye_test
        self.eye_controller._run_eye_test = lambda: (_ for _ in ()).throw(Exception("Hardware error"))
        
        try:
            async with AsyncTestTimeout(1.0):
                # Send command
                await self.send_command("eye", "test")
                
                # Wait for response
                await wait_for_condition(
                    lambda: len(self.event_collector.find_events(EventTopics.CLI_RESPONSE)) > 0
                )
                
                # Verify error
                assert self.verify_error("hardware error")
                
        finally:
            # Restore original test method
            self.eye_controller._run_eye_test = original_test 