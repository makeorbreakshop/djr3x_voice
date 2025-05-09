import asyncio
from typing import Callable, Optional

class EventSynchronizer:
    """
    Utility class for managing timing in async tests.
    Helps with waiting for events and managing timeouts.
    """
    
    def __init__(self):
        """Initialize the synchronizer."""
        self._loop = asyncio.get_event_loop()
    
    async def wait_for_event(
        self,
        condition: Callable[[], bool],
        timeout: float = 2.0,
        message: str = "Timeout waiting for event"
    ) -> None:
        """
        Wait for a condition to be met with timeout.
        
        Args:
            condition: Callable that returns True when the condition is met
            timeout: Maximum time to wait in seconds
            message: Error message if timeout occurs
        
        Raises:
            asyncio.TimeoutError: If condition not met within timeout
        """
        start_time = self._loop.time()
        while not condition():
            if (self._loop.time() - start_time) > timeout:
                raise asyncio.TimeoutError(message)
            await asyncio.sleep(0.1)  # Small sleep to prevent CPU spinning
    
    async def wait_for_value(
        self,
        getter: Callable[[], any],
        expected_value: any,
        timeout: float = 2.0,
        message: Optional[str] = None
    ) -> None:
        """
        Wait for a value to match expected value with timeout.
        
        Args:
            getter: Callable that returns the current value
            expected_value: Value to wait for
            timeout: Maximum time to wait in seconds
            message: Optional custom error message
        
        Raises:
            asyncio.TimeoutError: If value not matched within timeout
        """
        if message is None:
            message = f"Timeout waiting for value to become {expected_value}"
            
        await self.wait_for_event(
            lambda: getter() == expected_value,
            timeout=timeout,
            message=message
        )
    
    async def wait_multiple(
        self,
        conditions: list[Callable[[], bool]],
        timeout: float = 2.0,
        message: str = "Timeout waiting for multiple conditions"
    ) -> None:
        """
        Wait for multiple conditions to be met with a single timeout.
        
        Args:
            conditions: List of callables that return True when their condition is met
            timeout: Maximum time to wait in seconds
            message: Error message if timeout occurs
        
        Raises:
            asyncio.TimeoutError: If not all conditions met within timeout
        """
        await self.wait_for_event(
            lambda: all(c() for c in conditions),
            timeout=timeout,
            message=message
        ) 