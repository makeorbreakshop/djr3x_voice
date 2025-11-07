# DebugService Usage Guide

## Overview
The DebugService provides comprehensive debugging capabilities including async logging, command tracing, performance metrics, and state transition tracking.

## Quick Start

```python
from services.debug import DebugService
from services.base import BaseService

class YourService(BaseService):
    async def initialize(self):
        # Access debug methods through self
        await self.debug_log("Service initializing", level="INFO")
        
        # Trace command execution
        await self.debug_trace_command("your_command", {"param": "value"})
        
        # Track performance
        await self.debug_performance_metric("operation_name", 150)  # 150ms
        
        # Track state transitions
        await self.debug_state_transition("old_state", "new_state")

```

## Available Debug Methods

### 1. Logging
```python
await self.debug_log(message, level="DEBUG")
```
- Levels: DEBUG, INFO, WARNING, ERROR
- Async queue-based logging to prevent blocking
- Component-level log control

### 2. Command Tracing
```python
await self.debug_trace_command(command_name, params)
```
- Tracks command execution
- Records timing information
- Captures parameters and results

### 3. Performance Metrics
```python
await self.debug_performance_metric(operation, duration_ms)
```
- Track operation durations
- Monitor system performance
- Identify bottlenecks

### 4. State Transitions
```python
await self.debug_state_transition(from_state, to_state)
```
- Monitor state changes
- Debug state machine flows
- Track application lifecycle

## CLI Commands

1. Set Debug Level:
```bash
debug level [component_name] [level]
```

2. Enable Command Tracing:
```bash
debug trace [on|off]
```

3. View Performance Metrics:
```bash
debug performance
```

## Best Practices

1. Use appropriate log levels:
   - DEBUG: Detailed information for debugging
   - INFO: General operational information
   - WARNING: Issues that need attention
   - ERROR: Critical problems

2. Include context in log messages:
   - Component name
   - Operation being performed
   - Relevant IDs or parameters

3. Track performance for:
   - Long-running operations
   - Database queries
   - External API calls
   - Resource-intensive computations

4. Monitor state transitions for:
   - User sessions
   - Connection states
   - Application modes
   - Processing stages

## Configuration

The DebugService can be configured through your application's config:

```python
debug_config = {
    "default_level": "INFO",
    "component_levels": {
        "AudioService": "DEBUG",
        "NetworkService": "WARNING"
    },
    "trace_commands": True,
    "collect_metrics": True
}
```

## Error Handling

The DebugService includes built-in error handling:
- Async queue prevents blocking IO errors
- Failed log attempts are captured and reported
- Queue size monitoring prevents memory issues 