"""Test helper utilities for CantinaOS."""
import asyncio
from typing import Any, Callable, List, Tuple

class EventSynchronizer:
    """Helper class for managing event timing in tests."""
    
    def __init__(self, timeout: float = 1.0):
        """Initialize the synchronizer with a default timeout."""
        self.timeout = timeout
    
    async def wait_for_event(
        self,
        events: List[Tuple[str, Any]],
        condition: Callable[[Tuple[str, Any]], bool],
        timeout: float = None
    ) -> None:
        """
        Wait for an event matching the given condition.
        
        Args:
            events: List of (topic, payload) tuples to check
            condition: Function that takes a (topic, payload) tuple and returns bool
            timeout: Optional timeout override
        
        Raises:
            asyncio.TimeoutError: If no matching event is found within timeout
        """
        timeout = timeout or self.timeout
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Check if any existing events match
            if any(condition(event) for event in events):
                return
            
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for event after {timeout} seconds"
                )
            
            # Wait a bit before checking again
            await asyncio.sleep(0.01)
    
    async def wait_for_n_events(
        self,
        events: List[Tuple[str, Any]],
        condition: Callable[[Tuple[str, Any]], bool],
        n: int,
        timeout: float = None
    ) -> None:
        """
        Wait for N events matching the given condition.
        
        Args:
            events: List of (topic, payload) tuples to check
            condition: Function that takes a (topic, payload) tuple and returns bool
            n: Number of matching events to wait for
            timeout: Optional timeout override
        
        Raises:
            asyncio.TimeoutError: If N matching events are not found within timeout
        """
        timeout = timeout or self.timeout
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Count matching events
            matching_events = sum(1 for event in events if condition(event))
            if matching_events >= n:
                return
            
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for {n} events after {timeout} seconds"
                )
            
            # Wait a bit before checking again
            await asyncio.sleep(0.01)
    
    async def wait_for_sequence(
        self,
        events: List[Tuple[str, Any]],
        conditions: List[Callable[[Tuple[str, Any]], bool]],
        timeout: float = None
    ) -> None:
        """
        Wait for a sequence of events matching the given conditions in order.
        
        Args:
            events: List of (topic, payload) tuples to check
            conditions: List of functions that each take a (topic, payload) tuple
            timeout: Optional timeout override
        
        Raises:
            asyncio.TimeoutError: If sequence is not found within timeout
        """
        timeout = timeout or self.timeout
        start_time = asyncio.get_event_loop().time()
        
        # Track which conditions have been met
        met_conditions = [False] * len(conditions)
        
        while True:
            # Check events in order
            for i, (condition, is_met) in enumerate(zip(conditions, met_conditions)):
                if is_met:
                    continue
                
                # Look for matching event after all previous conditions are met
                if all(met_conditions[:i]):
                    if any(condition(event) for event in events):
                        met_conditions[i] = True
                        break
            
            # Check if all conditions met
            if all(met_conditions):
                return
            
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise asyncio.TimeoutError(
                    f"Timeout waiting for event sequence after {timeout} seconds"
                )
            
            # Wait a bit before checking again
            await asyncio.sleep(0.01) 