"""
Resource Monitor Utility for Testing

This utility provides tools for tracking resource acquisition and cleanup during tests.
It helps identify resource leaks, especially in components that have complex cleanup
requirements like audio systems and serial connections.
"""

import asyncio
import logging
import os
import psutil
import time
from typing import Dict, List, Set, Any, Optional, Callable, Tuple

logger = logging.getLogger(__name__)

class ResourceMonitor:
    """
    A utility for tracking resource acquisition and cleanup during tests.
    
    This helps identify resource leaks by:
    1. Tracking resource creation and disposal
    2. Checking system resources before and after test runs
    3. Verifying cleanup operations are properly executed
    4. Providing diagnostic information for resource leaks
    """
    
    def __init__(self):
        """Initialize the resource monitor."""
        self.resources: Dict[str, Dict[str, Any]] = {}
        self.resource_types: Dict[str, Set[str]] = {}
        self.baseline_metrics: Dict[str, Any] = {}
        self.process = psutil.Process(os.getpid())
    
    def register_resource(
        self, 
        resource_type: str, 
        resource_id: str, 
        resource: Any, 
        cleanup_fn: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Register a resource to be tracked.
        
        Args:
            resource_type: Type of resource (e.g., 'file', 'socket', 'vlc')
            resource_id: Unique identifier for this resource
            resource: The actual resource object
            cleanup_fn: Optional function to call for cleanup
            metadata: Optional additional information about the resource
        """
        if resource_type not in self.resource_types:
            self.resource_types[resource_type] = set()
        
        self.resource_types[resource_type].add(resource_id)
        
        self.resources[f"{resource_type}:{resource_id}"] = {
            'type': resource_type,
            'id': resource_id,
            'resource': resource,
            'cleanup_fn': cleanup_fn,
            'metadata': metadata or {},
            'created_at': time.time(),
            'stack_trace': self._get_stack_trace(),
            'cleaned': False
        }
        
        logger.debug(f"Registered resource: {resource_type}:{resource_id}")
    
    def mark_resource_cleaned(self, resource_type: str, resource_id: str) -> None:
        """
        Mark a resource as cleaned up.
        
        Args:
            resource_type: Type of resource
            resource_id: Unique identifier for this resource
        """
        key = f"{resource_type}:{resource_id}"
        if key in self.resources:
            self.resources[key]['cleaned'] = True
            self.resources[key]['cleaned_at'] = time.time()
            
            if resource_id in self.resource_types.get(resource_type, set()):
                self.resource_types[resource_type].remove(resource_id)
            
            logger.debug(f"Marked resource as cleaned: {key}")
    
    async def cleanup_resource(self, resource_type: str, resource_id: str) -> bool:
        """
        Clean up a specific resource.
        
        Args:
            resource_type: Type of resource
            resource_id: Unique identifier for this resource
            
        Returns:
            True if cleanup was successful, False otherwise
        """
        key = f"{resource_type}:{resource_id}"
        if key not in self.resources:
            logger.warning(f"Attempted to clean up unknown resource: {key}")
            return False
        
        resource_info = self.resources[key]
        if resource_info['cleaned']:
            logger.debug(f"Resource already cleaned: {key}")
            return True
        
        cleanup_fn = resource_info['cleanup_fn']
        if cleanup_fn is None:
            logger.warning(f"No cleanup function for resource: {key}")
            return False
        
        try:
            result = cleanup_fn(resource_info['resource'])
            if asyncio.iscoroutine(result):
                await result
            
            self.mark_resource_cleaned(resource_type, resource_id)
            return True
        except Exception as e:
            logger.error(f"Error cleaning up resource {key}: {e}")
            return False
    
    async def cleanup_all_resources(self, resource_type: Optional[str] = None) -> Dict[str, bool]:
        """
        Clean up all registered resources of a given type, or all if type is None.
        
        Args:
            resource_type: Optional type of resources to clean up
            
        Returns:
            Dictionary mapping resource keys to cleanup success status
        """
        results = {}
        
        # Make a copy of the keys as we'll be modifying the dictionary during iteration
        keys = list(self.resources.keys())
        
        for key in keys:
            resource_info = self.resources[key]
            
            if resource_type is not None and resource_info['type'] != resource_type:
                continue
                
            if resource_info['cleaned']:
                results[key] = True
                continue
                
            results[key] = await self.cleanup_resource(
                resource_info['type'], 
                resource_info['id']
            )
        
        return results
    
    def get_uncleaned_resources(self, resource_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all uncleaned resources of a given type, or all if type is None.
        
        Args:
            resource_type: Optional type of resources to filter by
            
        Returns:
            List of resource info dictionaries
        """
        uncleaned = []
        
        for key, resource_info in self.resources.items():
            if resource_info['cleaned']:
                continue
                
            if resource_type is not None and resource_info['type'] != resource_type:
                continue
                
            uncleaned.append(resource_info)
        
        return uncleaned
    
    def capture_baseline_metrics(self) -> Dict[str, Any]:
        """
        Capture baseline system metrics for comparison.
        
        Returns:
            Dictionary of captured metrics
        """
        self.baseline_metrics = {
            'timestamp': time.time(),
            'memory_info': self.process.memory_info(),
            'open_files': len(self.process.open_files()),
            'connections': len(self.process.connections()),
            'threads': len(self.process.threads()),
            'num_fds': self.process.num_fds() if hasattr(self.process, 'num_fds') else None,
            'resources': self.get_resource_counts()
        }
        
        return self.baseline_metrics
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """
        Get current system metrics.
        
        Returns:
            Dictionary of current metrics
        """
        return {
            'timestamp': time.time(),
            'memory_info': self.process.memory_info(),
            'open_files': len(self.process.open_files()),
            'connections': len(self.process.connections()),
            'threads': len(self.process.threads()),
            'num_fds': self.process.num_fds() if hasattr(self.process, 'num_fds') else None,
            'resources': self.get_resource_counts()
        }
    
    def compare_metrics(self) -> Dict[str, Any]:
        """
        Compare current metrics with baseline.
        
        Returns:
            Dictionary of metric differences
        """
        if not self.baseline_metrics:
            logger.warning("No baseline metrics captured")
            return {}
        
        current = self.get_current_metrics()
        diff = {
            'elapsed_time': current['timestamp'] - self.baseline_metrics['timestamp'],
            'memory_diff_bytes': current['memory_info'].rss - self.baseline_metrics['memory_info'].rss,
            'open_files_diff': current['open_files'] - self.baseline_metrics['open_files'],
            'connections_diff': current['connections'] - self.baseline_metrics['connections'],
            'threads_diff': current['threads'] - self.baseline_metrics['threads'],
        }
        
        if current['num_fds'] is not None and self.baseline_metrics['num_fds'] is not None:
            diff['fds_diff'] = current['num_fds'] - self.baseline_metrics['num_fds']
        
        # Compare resource counts
        resource_diff = {}
        for res_type, count in current['resources'].items():
            baseline_count = self.baseline_metrics['resources'].get(res_type, 0)
            resource_diff[res_type] = count - baseline_count
        
        diff['resource_diff'] = resource_diff
        
        return diff
    
    def get_resource_counts(self) -> Dict[str, int]:
        """
        Get counts of registered resources by type.
        
        Returns:
            Dictionary mapping resource types to counts
        """
        counts = {}
        for res_type, ids in self.resource_types.items():
            counts[res_type] = len(ids)
        return counts
    
    def _get_stack_trace(self) -> List[str]:
        """
        Get a simplified stack trace.
        
        Returns:
            List of stack frame strings
        """
        import traceback
        stack = traceback.extract_stack()
        # Skip the last few frames which are this method and its callers in this class
        return [str(frame) for frame in stack[:-3]]
    
    def report(self) -> str:
        """
        Generate a human-readable report.
        
        Returns:
            Report string
        """
        uncleaned = self.get_uncleaned_resources()
        metrics_diff = self.compare_metrics()
        
        report_lines = [
            "Resource Monitor Report",
            "-" * 40,
            f"Total resources tracked: {len(self.resources)}",
            f"Uncleaned resources: {len(uncleaned)}",
            "",
            "Resource counts by type:",
        ]
        
        for res_type, count in self.get_resource_counts().items():
            report_lines.append(f"  - {res_type}: {count}")
            
        if metrics_diff:
            report_lines.extend([
                "",
                "System metrics changes:",
                f"  - Memory: {metrics_diff.get('memory_diff_bytes', 'N/A') / (1024 * 1024):.2f} MB",
                f"  - Open files: {metrics_diff.get('open_files_diff', 'N/A')}",
                f"  - Connections: {metrics_diff.get('connections_diff', 'N/A')}",
                f"  - Threads: {metrics_diff.get('threads_diff', 'N/A')}",
            ])
            
            if 'fds_diff' in metrics_diff:
                report_lines.append(f"  - File descriptors: {metrics_diff['fds_diff']}")
        
        if uncleaned:
            report_lines.extend([
                "",
                "Uncleaned resources:",
            ])
            
            for res in uncleaned:
                report_lines.append(
                    f"  - {res['type']}:{res['id']} (created {time.time() - res['created_at']:.2f}s ago)"
                )
        
        return "\n".join(report_lines)
    
    def reset(self) -> None:
        """Reset the monitor, clearing all tracked resources."""
        self.resources.clear()
        self.resource_types.clear()
        self.baseline_metrics = {} 