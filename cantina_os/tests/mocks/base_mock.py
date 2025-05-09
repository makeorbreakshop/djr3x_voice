"""Base class for mock services used in testing."""
from typing import Any, Dict, List, Optional, Tuple
from abc import ABC, abstractmethod

class BaseMockService(ABC):
    """Base class for all mock services.
    
    This provides common functionality and interface requirements for mock services.
    Mock services are used to simulate external dependencies during testing.
    """
    
    def __init__(self) -> None:
        """Initialize the mock service."""
        self._calls: List[Tuple[str, Any]] = []
        self._responses: Dict[str, Any] = {}
        
    def record_call(self, method: str, *args, **kwargs) -> None:
        """Record a method call with its arguments."""
        self._calls.append((method, (args, kwargs)))
        
    def get_calls(self, method: Optional[str] = None) -> List[Tuple[str, Any]]:
        """Get recorded calls, optionally filtered by method name."""
        if method is None:
            return self._calls
        return [(m, args) for m, args in self._calls if m == method]
        
    def set_response(self, key: str, response: Any) -> None:
        """Set a mock response for a given key."""
        self._responses[key] = response
        
    def get_response(self, key: str) -> Optional[Any]:
        """Get the mock response for a given key."""
        return self._responses.get(key)
        
    def clear_calls(self) -> None:
        """Clear recorded calls."""
        self._calls.clear()
        
    def clear_responses(self) -> None:
        """Clear mock responses."""
        self._responses.clear()
        
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the mock service."""
        self.record_call('initialize')
        
    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the mock service."""
        self.record_call('shutdown')
        
    async def start(self) -> None:
        """Start the mock service."""
        await self.initialize()
        
    async def stop(self) -> None:
        """Stop the mock service."""
        await self.shutdown() 