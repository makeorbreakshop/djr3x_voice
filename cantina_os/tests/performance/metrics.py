"""Utilities for measuring and tracking performance metrics."""
import time
import asyncio
import psutil
import statistics
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from contextlib import contextmanager

@dataclass
class LatencyMetric:
    """Container for latency measurements."""
    operation: str
    start_time: float
    end_time: float
    duration_ms: float

@dataclass
class MemoryMetric:
    """Container for memory usage measurements."""
    timestamp: float
    rss_mb: float  # Resident Set Size in MB
    vms_mb: float  # Virtual Memory Size in MB
    cpu_percent: float

class PerformanceMetrics:
    """Utility class for collecting and analyzing performance metrics."""
    
    def __init__(self) -> None:
        """Initialize the performance metrics collector."""
        self.latency_measurements: List[LatencyMetric] = []
        self.memory_measurements: List[MemoryMetric] = []
        self._monitoring_task: Optional[asyncio.Task] = None
        self._monitoring_interval: float = 1.0  # seconds
        
    @contextmanager
    def measure_latency(self, operation: str) -> None:
        """Context manager for measuring operation latency."""
        start_time = time.time()
        try:
            yield
        finally:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000
            metric = LatencyMetric(
                operation=operation,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms
            )
            self.latency_measurements.append(metric)
            
    async def start_monitoring(self, interval: float = 1.0) -> None:
        """Start continuous memory and CPU monitoring."""
        self._monitoring_interval = interval
        self._monitoring_task = asyncio.create_task(self._monitor_resources())
        
    async def stop_monitoring(self) -> None:
        """Stop resource monitoring."""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            self._monitoring_task = None
            
    async def _monitor_resources(self) -> None:
        """Continuously monitor system resources."""
        process = psutil.Process()
        try:
            while True:
                memory_info = process.memory_info()
                metric = MemoryMetric(
                    timestamp=time.time(),
                    rss_mb=memory_info.rss / (1024 * 1024),  # Convert to MB
                    vms_mb=memory_info.vms / (1024 * 1024),  # Convert to MB
                    cpu_percent=process.cpu_percent()
                )
                self.memory_measurements.append(metric)
                await asyncio.sleep(self._monitoring_interval)
        except asyncio.CancelledError:
            pass
            
    def get_latency_stats(self, operation: Optional[str] = None) -> Dict[str, float]:
        """Calculate latency statistics for the specified operation."""
        measurements = self.latency_measurements
        if operation:
            measurements = [m for m in measurements if m.operation == operation]
            
        if not measurements:
            return {}
            
        durations = [m.duration_ms for m in measurements]
        return {
            'min_ms': min(durations),
            'max_ms': max(durations),
            'avg_ms': statistics.mean(durations),
            'median_ms': statistics.median(durations),
            'stddev_ms': statistics.stdev(durations) if len(durations) > 1 else 0,
            'p95_ms': statistics.quantiles(durations, n=20)[18] if len(durations) >= 20 else max(durations),
            'p99_ms': statistics.quantiles(durations, n=100)[98] if len(durations) >= 100 else max(durations)
        }
        
    def get_memory_stats(self) -> Dict[str, Dict[str, float]]:
        """Calculate memory usage statistics."""
        if not self.memory_measurements:
            return {}
            
        rss_values = [m.rss_mb for m in self.memory_measurements]
        vms_values = [m.vms_mb for m in self.memory_measurements]
        cpu_values = [m.cpu_percent for m in self.memory_measurements]
        
        return {
            'rss_mb': {
                'min': min(rss_values),
                'max': max(rss_values),
                'avg': statistics.mean(rss_values)
            },
            'vms_mb': {
                'min': min(vms_values),
                'max': max(vms_values),
                'avg': statistics.mean(vms_values)
            },
            'cpu_percent': {
                'min': min(cpu_values),
                'max': max(cpu_values),
                'avg': statistics.mean(cpu_values)
            }
        }
        
    def clear_metrics(self) -> None:
        """Clear all collected metrics."""
        self.latency_measurements.clear()
        self.memory_measurements.clear() 