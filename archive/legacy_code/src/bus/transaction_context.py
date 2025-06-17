"""
Transaction Context for Event Bus Operations

This module provides transaction-like semantics for event sequences,
ensuring atomic operations and proper rollback capabilities.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable, Awaitable, Union
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel

from .sync_event_bus import SyncEventBus

class TransactionState(Enum):
    """Transaction states."""
    PENDING = "PENDING"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"

@dataclass
class EventRecord:
    """Record of an event emission within a transaction."""
    topic: str
    payload: Dict[str, Any]
    timestamp: float
    compensating_action: Optional[Callable[[], Awaitable[None]]] = None

class TransactionContext:
    """
    Manages a sequence of events as a transaction.
    
    Features:
    - Atomic event sequences
    - Rollback capability
    - Event ordering guarantees
    - Grace period management
    """
    
    def __init__(
        self,
        event_bus: SyncEventBus,
        logger: Optional[logging.Logger] = None,
        grace_period_ms: int = 200
    ):
        """Initialize the transaction context.
        
        Args:
            event_bus: The event bus to use
            logger: Optional logger instance
            grace_period_ms: Grace period between events in milliseconds
        """
        self.event_bus = event_bus
        self.logger = logger or logging.getLogger(__name__)
        self.grace_period_ms = grace_period_ms
        
        self._events: List[EventRecord] = []
        self._state = TransactionState.PENDING
        self._lock = asyncio.Lock()
        self.logger.debug("Transaction context created")
        
    @property
    def state(self) -> TransactionState:
        """Get the current transaction state."""
        return self._state
        
    async def emit(
        self,
        topic: str,
        payload: Union[Dict[str, Any], BaseModel],
        compensating_action: Optional[Callable[[], Awaitable[None]]] = None
    ) -> None:
        """Emit an event within the transaction.
        
        Args:
            topic: Event topic
            payload: Event payload (dict or Pydantic model)
            compensating_action: Optional function to call during rollback
        
        Raises:
            RuntimeError: If transaction is not in PENDING state
        """
        if self._state != TransactionState.PENDING:
            raise RuntimeError(f"Cannot emit events in {self._state} state")
            
        # Convert Pydantic model to dict if needed
        if isinstance(payload, BaseModel):
            self.logger.debug(f"Converting Pydantic model to dict for {topic}")
            payload_dict = payload.model_dump()
        else:
            payload_dict = payload
            
        # Record the event
        event = EventRecord(
            topic=topic,
            payload=payload_dict,
            timestamp=asyncio.get_event_loop().time(),
            compensating_action=compensating_action
        )
        self._events.append(event)
        
        self.logger.debug(f"Emitting transaction event: {topic}")
        
        # Emit the event
        await self.event_bus.emit(topic, payload_dict)
        
        # Apply grace period
        await asyncio.sleep(self.grace_period_ms / 1000)
        
        self.logger.debug(f"Transaction event emitted: {topic} with keys: {list(payload_dict.keys())}")
        
    async def commit(self) -> None:
        """Commit the transaction.
        
        This marks the transaction as completed successfully.
        """
        async with self._lock:
            if self._state != TransactionState.PENDING:
                raise RuntimeError(f"Cannot commit transaction in {self._state} state")
                
            try:
                self._state = TransactionState.COMMITTING
                self.logger.debug(f"Committing transaction with {len(self._events)} events")
                
                # Apply final grace period
                await asyncio.sleep(self.grace_period_ms / 1000)
                
                self._state = TransactionState.COMMITTED
                self.logger.debug("Transaction committed successfully")
                
            except Exception as e:
                self._state = TransactionState.FAILED
                self.logger.error(f"Error committing transaction: {e}")
                raise
                
    async def rollback(self) -> None:
        """Rollback the transaction.
        
        This executes compensating actions in reverse order.
        """
        async with self._lock:
            if self._state not in [TransactionState.PENDING, TransactionState.FAILED]:
                raise RuntimeError(f"Cannot rollback transaction in {self._state} state")
                
            try:
                self._state = TransactionState.ROLLING_BACK
                self.logger.debug(f"Rolling back transaction with {len(self._events)} events")
                
                # Execute compensating actions in reverse order
                for event in reversed(self._events):
                    self.logger.debug(f"Rolling back event: {event.topic}")
                    if event.compensating_action:
                        try:
                            await event.compensating_action()
                            await asyncio.sleep(self.grace_period_ms / 1000)
                            self.logger.debug(f"Compensating action completed for: {event.topic}")
                        except Exception as e:
                            self.logger.error(f"Error in compensating action for {event.topic}: {e}")
                            
                self._state = TransactionState.ROLLED_BACK
                self.logger.debug("Transaction rolled back successfully")
                
            except Exception as e:
                self._state = TransactionState.FAILED
                self.logger.error(f"Error rolling back transaction: {e}")
                raise
                
    async def __aenter__(self) -> 'TransactionContext':
        """Enter the transaction context."""
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the transaction context.
        
        Commits on success, rolls back on error.
        """
        if exc_type is None:
            await self.commit()
        else:
            self.logger.error(f"Transaction failed: {exc_val}")
            await self.rollback() 