"""
Tests for TransactionContext

These tests verify the transaction-like behavior of event sequences,
including proper rollback and compensating actions.
"""

import pytest
import asyncio
from typing import List, Dict, Any

from src.bus.sync_event_bus import SyncEventBus
from src.bus.transaction_context import TransactionContext, TransactionState

@pytest.fixture
async def event_bus():
    """Create a SyncEventBus instance."""
    bus = SyncEventBus()
    yield bus
    bus.clear()

@pytest.fixture
async def transaction_context(event_bus):
    """Create a TransactionContext instance."""
    context = TransactionContext(event_bus)
    return context

@pytest.mark.asyncio
async def test_successful_transaction(event_bus, transaction_context):
    """Test successful transaction completion."""
    events: List[Dict[str, Any]] = []
    
    # Subscribe to test events
    await event_bus.on("test/event1", lambda p: events.append({"topic": "test/event1", **p}))
    await event_bus.on("test/event2", lambda p: events.append({"topic": "test/event2", **p}))
    
    # Execute transaction
    async with transaction_context as tx:
        await tx.emit("test/event1", {"data": "first"})
        await tx.emit("test/event2", {"data": "second"})
        
    # Verify transaction state
    assert transaction_context.state == TransactionState.COMMITTED
    
    # Verify events were emitted in order
    assert len(events) == 2
    assert events[0]["topic"] == "test/event1"
    assert events[0]["data"] == "first"
    assert events[1]["topic"] == "test/event2"
    assert events[1]["data"] == "second"

@pytest.mark.asyncio
async def test_transaction_rollback(event_bus, transaction_context):
    """Test transaction rollback on error."""
    events: List[Dict[str, Any]] = []
    compensating_actions: List[str] = []
    
    # Subscribe to test events
    await event_bus.on("test/event", lambda p: events.append({"topic": "test/event", **p}))
    
    # Define compensating action
    async def compensate():
        compensating_actions.append("compensated")
    
    # Execute transaction that will fail
    with pytest.raises(ValueError):
        async with transaction_context as tx:
            await tx.emit("test/event", {"data": "first"}, compensating_action=compensate)
            raise ValueError("Test error")
            
    # Verify transaction state
    assert transaction_context.state == TransactionState.ROLLED_BACK
    
    # Verify compensating action was called
    assert len(compensating_actions) == 1
    assert compensating_actions[0] == "compensated"

@pytest.mark.asyncio
async def test_nested_transactions(event_bus):
    """Test nested transaction handling."""
    events: List[Dict[str, Any]] = []
    
    # Subscribe to test events
    await event_bus.on("test/event", lambda p: events.append({"topic": "test/event", **p}))
    
    # Create nested transactions
    outer_tx = TransactionContext(event_bus)
    inner_tx = TransactionContext(event_bus)
    
    # Execute nested transactions
    async with outer_tx as tx1:
        await tx1.emit("test/event", {"level": "outer"})
        
        async with inner_tx as tx2:
            await tx2.emit("test/event", {"level": "inner"})
            
    # Verify both transactions committed
    assert outer_tx.state == TransactionState.COMMITTED
    assert inner_tx.state == TransactionState.COMMITTED
    
    # Verify events were emitted in order
    assert len(events) == 2
    assert events[0]["level"] == "outer"
    assert events[1]["level"] == "inner"

@pytest.mark.asyncio
async def test_transaction_timeout(event_bus):
    """Test transaction timeout handling."""
    # Create transaction with short grace period
    tx = TransactionContext(event_bus, grace_period_ms=50)
    
    # Subscribe to test event with delay
    async def delayed_handler(payload):
        await asyncio.sleep(0.1)  # 100ms delay
        
    await event_bus.on("test/event", delayed_handler)
    
    # Execute transaction
    async with tx:
        await tx.emit("test/event", {"data": "test"})
        
    # Verify transaction completed despite handler delay
    assert tx.state == TransactionState.COMMITTED

@pytest.mark.asyncio
async def test_multiple_compensating_actions(event_bus, transaction_context):
    """Test multiple compensating actions are executed in reverse order."""
    compensating_order: List[str] = []
    
    # Define compensating actions
    async def compensate1():
        compensating_order.append("first")
        
    async def compensate2():
        compensating_order.append("second")
        
    async def compensate3():
        compensating_order.append("third")
    
    # Execute transaction that will fail
    with pytest.raises(ValueError):
        async with transaction_context as tx:
            await tx.emit("test/event1", {"data": "1"}, compensating_action=compensate1)
            await tx.emit("test/event2", {"data": "2"}, compensating_action=compensate2)
            await tx.emit("test/event3", {"data": "3"}, compensating_action=compensate3)
            raise ValueError("Test error")
            
    # Verify compensating actions were called in reverse order
    assert len(compensating_order) == 3
    assert compensating_order == ["third", "second", "first"]

@pytest.mark.asyncio
async def test_invalid_transaction_states(event_bus, transaction_context):
    """Test invalid transaction state transitions."""
    # Try to emit after commit
    await transaction_context.commit()
    with pytest.raises(RuntimeError):
        await transaction_context.emit("test/event", {"data": "test"})
        
    # Try to commit after commit
    with pytest.raises(RuntimeError):
        await transaction_context.commit()
        
    # Try to rollback after commit
    with pytest.raises(RuntimeError):
        await transaction_context.rollback() 