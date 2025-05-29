"""Mock event bus for testing command flow and service interactions."""

import pytest
from typing import Dict, Any, List, Callable, Awaitable
from ...core.event_topics import EventTopics


class MockEventBus:
    """Mock event bus for testing command flow."""
    
    def __init__(self):
        self.emitted_events: List[tuple[str, Dict[str, Any]]] = []
        self.subscriptions: Dict[str, List[Callable[[Dict[str, Any]], Awaitable[None]]]] = {}
    
    async def emit(self, topic: str, payload: Dict[str, Any] = None):
        """Record emitted events and call subscribed handlers."""
        self.emitted_events.append((topic, payload))
        
        # Call any subscribed handlers
        if topic in self.subscriptions:
            for handler in self.subscriptions[topic]:
                await handler(payload)
    
    async def subscribe(self, topic: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]):
        """Record subscriptions for verification."""
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
        self.subscriptions[topic].append(handler)
    
    def clear(self):
        """Clear recorded events and subscriptions."""
        self.emitted_events = []
        self.subscriptions = {}
    
    def find_events(self, topic: str) -> List[tuple[str, Dict[str, Any]]]:
        """Find all events with given topic."""
        return [(t, p) for t, p in self.emitted_events if t == topic]
    
    def find_last_event(self, topic: str) -> tuple[str, Dict[str, Any]] | None:
        """Find most recent event with given topic."""
        events = self.find_events(topic)
        return events[-1] if events else None


@pytest.fixture
async def mock_event_bus():
    """Fixture providing a mock event bus."""
    bus = MockEventBus()
    yield bus
    bus.clear()


@pytest.fixture
async def eye_controller(mock_event_bus):
    """Fixture providing an EyeLightControllerService instance."""
    from ...services.eye_light_controller import EyeLightControllerService
    service = EyeLightControllerService(event_bus=mock_event_bus)
    await service.start()
    yield service
    await service.stop() 