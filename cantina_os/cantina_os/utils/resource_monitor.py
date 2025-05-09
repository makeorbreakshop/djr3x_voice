import asyncio
from typing import Dict, List, Any, Optional
import psutil
import logging

logger = logging.getLogger(__name__)

class ResourceMonitor:
    """
    Utility class for tracking resource usage and cleanup in tests.
    Monitors memory, file handles, and other system resources.
    """
    
    def __init__(self):
        """Initialize the resource monitor."""
        self._process = psutil.Process()
        self._initial_fds = self._get_open_fds()
        self._initial_memory = self._get_memory_usage()
        self._tracked_resources: Dict[str, Any] = {}
        
    def _get_open_fds(self) -> int:
        """Get current number of open file descriptors."""
        try:
            return len(self._process.open_files())
        except Exception as e:
            logger.warning(f"Failed to get open file descriptors: {e}")
            return 0
            
    def _get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        try:
            return self._process.memory_info().rss / 1024 / 1024
        except Exception as e:
            logger.warning(f"Failed to get memory usage: {e}")
            return 0.0
            
    def track_resource(self, resource_id: str, resource: Any) -> None:
        """
        Track a resource for cleanup verification.
        
        Args:
            resource_id: Unique identifier for the resource
            resource: The resource object to track
        """
        self._tracked_resources[resource_id] = resource
        
    def untrack_resource(self, resource_id: str) -> None:
        """
        Stop tracking a resource.
        
        Args:
            resource_id: Identifier of resource to stop tracking
        """
        self._tracked_resources.pop(resource_id, None)
        
    async def verify_cleanup(self, timeout: float = 2.0) -> None:
        """
        Verify all resources have been properly cleaned up.
        
        Args:
            timeout: Maximum time to wait for cleanup in seconds
            
        Raises:
            AssertionError: If resources not properly cleaned up
        """
        # Wait a bit for any pending cleanup
        await asyncio.sleep(0.1)
        
        # Check tracked resources
        remaining = list(self._tracked_resources.keys())
        if remaining:
            raise AssertionError(
                f"Resources not properly cleaned up: {remaining}"
            )
            
        # Check file descriptors
        current_fds = self._get_open_fds()
        if current_fds > self._initial_fds:
            raise AssertionError(
                f"File descriptor leak detected: {current_fds - self._initial_fds} unclosed"
            )
            
        # Check memory usage (with some tolerance for Python's GC)
        current_memory = self._get_memory_usage()
        if current_memory > (self._initial_memory * 1.5):  # 50% tolerance
            raise AssertionError(
                f"Possible memory leak: {current_memory:.1f}MB vs initial {self._initial_memory:.1f}MB"
            )
            
    def get_resource(self, resource_id: str) -> Optional[Any]:
        """
        Get a tracked resource by ID.
        
        Args:
            resource_id: Identifier of resource to retrieve
            
        Returns:
            The resource if found, None otherwise
        """
        return self._tracked_resources.get(resource_id) 