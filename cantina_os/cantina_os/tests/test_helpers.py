"""
Test helpers for CantinaOS integration tests.

These helpers provide common functionality for testing command flow,
event handling, and service interactions.
"""

import asyncio
from typing import Dict, Any, List, Tuple, Optional
from ..core.event_topics import EventTopics


class EventCollector:
    """Helper class to collect and verify events during tests."""
    
    def __init__(self):
        self.events: List[Tuple[str, Dict[str, Any]]] = []
    
    def add_event(self, topic: str, payload: Dict[str, Any]):
        """Record an event."""
        self.events.append((topic, payload))
    
    def clear(self):
        """Clear recorded events."""
        self.events = []
    
    def find_events(self, topic: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Find all events with given topic."""
        return [(t, p) for t, p in self.events if t == topic]
    
    def find_success_response(self) -> Optional[Dict[str, Any]]:
        """Find first successful CLI response."""
        for topic, payload in self.events:
            if topic == EventTopics.CLI_RESPONSE and payload.get("success", False):
                return payload
        return None
    
    def find_error_response(self) -> Optional[Dict[str, Any]]:
        """Find first error CLI response."""
        for topic, payload in self.events:
            if topic == EventTopics.CLI_RESPONSE and not payload.get("success", False):
                return payload
        return None
    
    def verify_command_success(self, expected_message: str = None) -> bool:
        """
        Verify a command succeeded and optionally check response message.
        
        Args:
            expected_message: Optional substring to check in response message
            
        Returns:
            True if command succeeded and message matches (if provided)
        """
        response = self.find_success_response()
        if not response:
            return False
            
        if expected_message:
            return expected_message.lower() in response.get("message", "").lower()
            
        return True
    
    def verify_command_error(self, expected_error: str = None) -> bool:
        """
        Verify a command failed and optionally check error message.
        
        Args:
            expected_error: Optional substring to check in error message
            
        Returns:
            True if command failed and error matches (if provided)
        """
        response = self.find_error_response()
        if not response:
            return False
            
        if expected_error:
            return expected_error.lower() in response.get("message", "").lower()
            
        return True


class CommandTestMixin:
    """Mixin providing command testing helpers for test classes."""
    
    def __init__(self):
        self.event_collector = EventCollector()
    
    async def send_command(self, command: str, subcommand: str = None, args: List[str] = None):
        """
        Send a command and collect response.
        
        Args:
            command: Base command (e.g., "eye")
            subcommand: Optional subcommand (e.g., "test")
            args: Optional list of arguments
        """
        payload = {
            "command": command,
            "subcommand": subcommand,
            "args": args or [],
            "raw_input": f"{command} {subcommand or ''} {' '.join(args or [])}"
        }
        
        await self.event_bus.emit(EventTopics.CLI_COMMAND, payload)
    
    def verify_success(self, expected_message: str = None) -> bool:
        """Verify command succeeded with optional message check."""
        return self.event_collector.verify_command_success(expected_message)
    
    def verify_error(self, expected_error: str = None) -> bool:
        """Verify command failed with optional error check."""
        return self.event_collector.verify_command_error(expected_error)


class AsyncTestTimeout:
    """Context manager for handling async test timeouts."""
    
    def __init__(self, timeout: float = 1.0):
        self.timeout = timeout
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is asyncio.TimeoutError:
            raise AssertionError(f"Test timed out after {self.timeout} seconds")
        return None


async def wait_for_condition(condition, timeout: float = 1.0, interval: float = 0.1):
    """
    Wait for a condition to become true.
    
    Args:
        condition: Callable that returns bool
        timeout: Maximum time to wait in seconds
        interval: Check interval in seconds
        
    Returns:
        True if condition met, False if timed out
    """
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False 