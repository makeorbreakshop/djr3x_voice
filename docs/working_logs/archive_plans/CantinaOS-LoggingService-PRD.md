# CantinaOS LoggingService - Product Requirements Document (PRD)

## 1. Executive Summary

### 1.1 Overview
The CantinaOS LoggingService is a centralized logging aggregation and management service that captures all existing Python log output from CantinaOS services and provides structured log data to the web dashboard. This service solves the current dashboard issue where no real-time logs are visible, enabling proper system monitoring and debugging capabilities.

### 1.2 Problem Statement
The current DJ R3X web dashboard shows no real-time system activity logs, making it difficult to:
- Monitor real-time system activity and service behavior
- Debug system performance and service issues
- Track voice interactions, mode changes, and music operations
- Understand system flow during voice conversations and DJ mode
- Access historical session logs for post-session analysis

### 1.3 Solution
A dedicated CantinaOS service that captures all existing Python logging output, provides real-time log streaming to the web dashboard, and maintains session-based log persistence for debugging and analysis.

## 2. User Profile & Use Cases

### 2.1 Primary User
**System Operator/Developer** (Single User - You)
- Monitoring DJ R3X system during development and operation
- Debugging voice interaction flows and service coordination
- Analyzing system performance and service health
- Reviewing session logs for troubleshooting and improvement

### 2.2 Key Use Cases

1. **Real-time System Monitoring**: View live system activity as voice commands are processed
2. **Voice Flow Debugging**: Track transcription → GPT → ElevenLabs → music ducking flow
3. **Service Health Monitoring**: Monitor service startup, errors, and performance
4. **DJ Mode Analysis**: Track automatic music selection and transition logic
5. **Session Review**: Analyze complete interaction sessions for improvement
6. **Performance Troubleshooting**: Identify bottlenecks and error patterns

## 3. Technical Architecture

### 3.1 Service Architecture Overview

The LoggingService follows standard CantinaOS service patterns and integrates seamlessly with the existing event-driven architecture:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   All CantinaOS │────▶│  LoggingService │────▶│   Web Dashboard │
│    Services     │     │  (Log Capture)  │     │  (Log Display)  │
│  (Python Logs)  │     └─────────────────┘     └─────────────────┘
└─────────────────┘              │                        ▲
                                 │                        │
                                 ▼                        │
                        ┌─────────────────┐     ┌─────────────────┐
                        │  Session File   │     │   WebBridge     │
                        │   Persistence   │     │    Service      │
                        └─────────────────┘     └─────────────────┘
```

### 3.2 Integration with Existing CantinaOS Architecture

**Service Registry Integration**:
| Service Name | Purpose | Events Subscribed (Inputs) | Events Published (Outputs) | Configuration | Hardware Dependencies |
|--------------|---------|----------------------------|----------------------------|---------------|----------------------|
| LoggingService | System log aggregation and dashboard streaming | None (uses Python logging.Handler) | DASHBOARD_LOG | log_level, max_memory_logs, session_file_path, enable_dashboard_streaming | None |

**Event Bus Integration**:
- **Published Events**: `DASHBOARD_LOG` - Structured log entries for web dashboard consumption
- **Event Flow**: `Python Logs → LoggingService → DASHBOARD_LOG → WebBridge → Dashboard`

### 3.3 Core Components

#### 3.3.1 CantinaLogHandler
Custom Python logging handler that captures all log records from CantinaOS services:

```python
class CantinaLogHandler(logging.Handler):
    """Custom logging handler that captures all CantinaOS log output."""
    
    def __init__(self, logging_service):
        super().__init__()
        self._logging_service = logging_service
        
    def emit(self, record):
        """Process log record and forward to LoggingService."""
        self._logging_service.handle_log_record(record)
```

#### 3.3.2 Log Processing Engine
Formats and processes log records for storage and dashboard display:

```python
@dataclass
class LogEntry:
    """Structured log entry format."""
    timestamp: str
    level: str  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    service: str  # Extracted from logger name
    message: str
    raw_record: Optional[dict]  # Original log record data
    session_id: str
    entry_id: str
```

#### 3.3.3 Memory Management
Ring buffer system for in-memory log storage:

```python
class LogRingBuffer:
    """Fixed-size ring buffer for in-memory log storage."""
    
    def __init__(self, max_size: int = 1000):
        self._buffer = deque(maxlen=max_size)
        self._lock = asyncio.Lock()
        
    async def add_log(self, log_entry: LogEntry):
        async with self._lock:
            self._buffer.append(log_entry)
```

#### 3.3.4 Session File Manager
Handles persistent log file storage per session:

```python
class SessionFileManager:
    """Manages session-based log file persistence."""
    
    def __init__(self, base_path: str):
        self._base_path = Path(base_path)
        self._current_session_file = None
        self._session_id = None
        
    def start_session(self) -> str:
        """Start new logging session with timestamped file."""
        self._session_id = f"cantina-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self._current_session_file = self._base_path / f"{self._session_id}.log"
        return self._session_id
```

## 4. Service Implementation

### 4.1 LoggingService Class Structure

```python
class LoggingService(StandardService):
    """Centralized logging service for CantinaOS system monitoring."""
    
    class _Config(BaseModel):
        """LoggingService configuration."""
        log_level: str = "INFO"
        max_memory_logs: int = 1000
        session_file_path: str = "./logs"
        enable_dashboard_streaming: bool = True
        file_flush_interval: int = 5  # seconds
        deduplication_window: int = 30  # seconds
        
    def __init__(self, event_bus, config=None, name="logging_service"):
        super().__init__(event_bus, config, name=name)
        self._config = self._Config(**(config or {}))
        self._log_buffer = LogRingBuffer(self._config.max_memory_logs)
        self._session_manager = SessionFileManager(self._config.session_file_path)
        self._log_handler = None
        self._deduplication_cache = {}
        self._file_write_task = None
```

### 4.2 Core Service Methods

#### 4.2.1 Service Lifecycle
```python
async def _start(self) -> None:
    """Start the logging service and install log handler."""
    # Install custom log handler to capture all Python logs
    self._log_handler = CantinaLogHandler(self)
    self._log_handler.setLevel(getattr(logging, self._config.log_level))
    
    # Add to root logger to capture all service logs
    root_logger = logging.getLogger()
    root_logger.addHandler(self._log_handler)
    
    # Start new session
    session_id = self._session_manager.start_session()
    
    # Start background file writing task
    self._file_write_task = asyncio.create_task(self._file_writer_loop())
    
    self._logger.info(f"LoggingService started - Session: {session_id}")

async def _stop(self) -> None:
    """Stop the logging service and cleanup resources."""
    # Remove log handler
    if self._log_handler:
        logging.getLogger().removeHandler(self._log_handler)
    
    # Stop file writing task
    if self._file_write_task:
        self._file_write_task.cancel()
        try:
            await self._file_write_task
        except asyncio.CancelledError:
            pass
    
    # Flush any remaining logs to file
    await self._flush_session_file()
```

#### 4.2.2 Log Processing
```python
def handle_log_record(self, record: logging.LogRecord) -> None:
    """Process incoming log record from Python logging system."""
    try:
        # Extract service name from logger name
        service_name = self._extract_service_name(record.name)
        
        # Create structured log entry
        log_entry = LogEntry(
            timestamp=datetime.fromtimestamp(record.created).isoformat(),
            level=record.levelname,
            service=service_name,
            message=record.getMessage(),
            raw_record=vars(record),
            session_id=self._session_manager.session_id,
            entry_id=f"{record.created}-{hash(record.getMessage())}"
        )
        
        # Smart deduplication
        if not self._should_deduplicate(log_entry):
            # Add to memory buffer
            asyncio.create_task(self._log_buffer.add_log(log_entry))
            
            # Stream to dashboard if enabled
            if self._config.enable_dashboard_streaming:
                asyncio.create_task(self._emit_dashboard_log(log_entry))
            
            # Queue for file writing
            asyncio.create_task(self._queue_for_file_write(log_entry))
            
    except Exception as e:
        # Fallback logging to prevent infinite loops
        print(f"LoggingService error processing log: {e}")

def _extract_service_name(self, logger_name: str) -> str:
    """Extract service name from Python logger name."""
    # Map logger names to service names
    service_map = {
        "deepgram_direct_mic": "Voice Input",
        "gpt_service": "AI Assistant", 
        "elevenlabs_service": "Speech Synthesis",
        "music_controller": "Music Controller",
        "eye_light_controller": "Eye Lights",
        "yoda_mode_manager": "Mode Manager",
        "web_bridge": "Web Bridge"
    }
    
    # Extract base service name
    base_name = logger_name.split('.')[0] if '.' in logger_name else logger_name
    return service_map.get(base_name, base_name.title())
```

#### 4.2.3 Dashboard Integration
```python
async def _emit_dashboard_log(self, log_entry: LogEntry) -> None:
    """Emit log entry to dashboard via event bus."""
    try:
        dashboard_payload = {
            "timestamp": log_entry.timestamp,
            "level": log_entry.level,
            "service": log_entry.service,
            "message": log_entry.message,
            "session_id": log_entry.session_id,
            "entry_id": log_entry.entry_id
        }
        
        # Emit to WebBridge for dashboard consumption
        self._emit_dict(EventTopics.DASHBOARD_LOG, dashboard_payload)
        
    except Exception as e:
        self._logger.error(f"Error emitting dashboard log: {e}")

def _should_deduplicate(self, log_entry: LogEntry) -> bool:
    """Smart deduplication to prevent log flooding."""
    # Create deduplication key
    dedup_key = f"{log_entry.service}:{log_entry.level}:{log_entry.message}"
    
    now = time.time()
    
    # Check if we've seen this log recently
    if dedup_key in self._deduplication_cache:
        last_seen = self._deduplication_cache[dedup_key]
        if now - last_seen < self._config.deduplication_window:
            return True  # Deduplicate
    
    # Update cache
    self._deduplication_cache[dedup_key] = now
    
    # Clean old entries from cache
    cutoff = now - self._config.deduplication_window
    self._deduplication_cache = {
        k: v for k, v in self._deduplication_cache.items() 
        if v > cutoff
    }
    
    return False  # Don't deduplicate
```

#### 4.2.4 File Persistence
```python
async def _file_writer_loop(self) -> None:
    """Background task for writing logs to session file."""
    while True:
        try:
            await asyncio.sleep(self._config.file_flush_interval)
            await self._flush_session_file()
        except asyncio.CancelledError:
            break
        except Exception as e:
            self._logger.error(f"Error in file writer loop: {e}")

async def _flush_session_file(self) -> None:
    """Flush pending logs to session file."""
    if not self._session_manager.current_session_file:
        return
        
    try:
        # Get all logs from buffer
        logs_to_write = []
        async with self._log_buffer._lock:
            logs_to_write = list(self._log_buffer._buffer)
        
        # Write to file
        with open(self._session_manager.current_session_file, 'a', encoding='utf-8') as f:
            for log_entry in logs_to_write:
                log_line = f"[{log_entry.timestamp}] {log_entry.level:8} {log_entry.service:20} {log_entry.message}\n"
                f.write(log_line)
                
    except Exception as e:
        self._logger.error(f"Error writing session file: {e}")
```

## 5. WebBridge Integration

### 5.1 Event Topic Addition

Add new event topic to `EventTopics` enum:
```python
class EventTopics(Enum):
    # ... existing topics
    DASHBOARD_LOG = "/dashboard/log"
```

### 5.2 Event Payload Definition

Add new payload model to `event_payloads.py`:
```python
class DashboardLogPayload(BaseEventPayload):
    """Payload for dashboard log events."""
    timestamp: str
    level: str
    service: str
    message: str
    session_id: str
    entry_id: str
```

### 5.3 WebBridge Service Updates

Update WebBridge to handle log events:
```python
class WebBridgeService(BaseService):
    async def _setup_subscriptions(self) -> None:
        await asyncio.gather(
            # ... existing subscriptions
            self.subscribe(EventTopics.DASHBOARD_LOG, self._handle_dashboard_log)
        )
        
    async def _handle_dashboard_log(self, payload: Dict[str, Any]) -> None:
        """Handle dashboard log events from LoggingService."""
        try:
            log_payload = DashboardLogPayload(**payload)
            
            # Send to all connected web clients
            await self._broadcast_to_clients({
                "event": "cantina_event",
                "data": {
                    "timestamp": log_payload.timestamp,
                    "level": log_payload.level,
                    "service": log_payload.service,
                    "message": log_payload.message,
                    "topic": "system_log"
                }
            })
            
        except Exception as e:
            self.logger.error(f"Error handling dashboard log: {e}")
```

## 6. Service Registration and Configuration

### 6.1 Main.py Integration

Add LoggingService to CantinaOS main.py:

```python
# In cantina_os/main.py
from services.logging_service import LoggingService

async def initialize_services():
    """Initialize all CantinaOS services."""
    
    # ... existing service initialization
    
    # Logging service configuration
    logging_config = {
        "log_level": os.getenv("LOGGING_LEVEL", "INFO"),
        "max_memory_logs": int(os.getenv("MAX_MEMORY_LOGS", "1000")),
        "session_file_path": os.getenv("SESSION_LOG_PATH", "./logs"),
        "enable_dashboard_streaming": True,
        "file_flush_interval": 5,
        "deduplication_window": 30
    }
    
    logging_service = LoggingService(
        event_bus=event_bus,
        config=logging_config,
        logger=logger.getChild("logging_service")
    )
    
    services.append(logging_service)
    
    # Service order - LoggingService should start early to capture startup logs
    service_order = [
        "debug_service",
        "logging_service",  # Start early to capture all logs
        "memory_service",
        "command_dispatcher",
        # ... other services
    ]
    
    return services
```

### 6.2 Configuration Management

Environment variables for LoggingService:
```bash
# Optional configuration
LOGGING_LEVEL=INFO
MAX_MEMORY_LOGS=1000
SESSION_LOG_PATH=./logs
```

## 7. Feature Requirements

### 7.1 Core Features (MVP)

**Log Capture and Processing**
- ✅ Capture all existing Python log output from CantinaOS services
- ✅ Structure logs with timestamp, level, service, and message
- ✅ In-memory ring buffer storage (1000 entries)
- ✅ Smart deduplication to prevent log flooding

**Dashboard Integration**
- ✅ Real-time log streaming to web dashboard
- ✅ Integration with existing WebBridge service
- ✅ Proper event topic usage following CantinaOS standards

**Session Persistence**
- ✅ Automatic session file creation with timestamps
- ✅ Background file writing with configurable flush intervals
- ✅ Local file storage for post-session analysis

### 7.2 Enhanced Features (V2)

**Advanced Filtering**
- ✅ Service-level filtering for dashboard display
- ✅ Log level filtering (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- ✅ Search functionality within log messages

**Performance Optimization**
- ✅ Configurable deduplication windows
- ✅ Rate limiting for high-frequency debug logs
- ✅ Efficient memory management with ring buffers

**Analysis Tools**
- ✅ Session log file format optimized for analysis
- ✅ Log entry correlation with session IDs
- ✅ Error pattern detection and alerting

### 7.3 Developer Features (V3)

**Log Analysis**
- ✅ Session comparison tools
- ✅ Performance metrics extraction from logs
- ✅ Error trend analysis

**Configuration Management**
- ✅ Runtime log level adjustment
- ✅ Dynamic service filtering
- ✅ Log retention policy management

## 8. Technical Specifications

### 8.1 Async I/O Requirements

**Asynchronous File Operations**
- Uses `aiofiles` for non-blocking file I/O operations
- Queue-based architecture prevents blocking the event loop
- Batch processing (up to 100 logs per batch) for efficiency
- Automatic overflow protection with queue size limits
- Graceful fallback to synchronous I/O if `aiofiles` unavailable

**Dependencies**
- `aiofiles>=23.0.0` for async file operations
- Fallback to standard file I/O if dependency unavailable

### 8.2 Performance Requirements

**Memory Usage**
- Ring buffer: Maximum 1000 log entries (~1MB typical)
- Deduplication cache: Automatic cleanup, <100KB
- Total service memory footprint: <5MB

**File I/O Performance**
- Background file writing to prevent blocking
- Configurable flush intervals (default 5 seconds)
- Asynchronous file operations

**Event Processing**
- Log processing: <1ms per log entry
- Dashboard emission: <5ms per log entry
- Maximum throughput: 1000 logs/second

### 8.3 Scalability Considerations

**Log Volume Management**
- Ring buffer automatically manages memory usage
- File size rotation (future enhancement)
- Configurable log levels to control volume

**Dashboard Performance**
- Rate limiting for dashboard log streaming
- Client-side log buffering and filtering
- Selective event emission based on dashboard needs

## 9. Implementation Plan

### 9.1 Development Phases

**Phase 1: Core Service Implementation (1 week)**
- Create LoggingService class following CantinaOS standards
- Implement CantinaLogHandler for Python log capture
- Basic memory buffer and file persistence
- Service registration in main.py

**Phase 2: Dashboard Integration (1 week)**
- Add DASHBOARD_LOG event topic and payload
- Update WebBridge to handle log events
- Frontend integration with existing SystemTab log display
- Real-time log streaming functionality

**Phase 3: Enhanced Features (1 week)**
- Smart deduplication implementation
- Service name extraction and mapping
- Session file management with automatic rotation
- Performance optimization and testing

**Phase 4: Polish and Testing (1 week)**
- Comprehensive error handling
- Memory usage optimization
- Integration testing with all CantinaOS services
- Documentation and deployment

### 9.2 Technical Milestones

1. **Python Log Capture Working** - All service logs captured by LoggingService
2. **Dashboard Integration Complete** - Real-time logs flowing to web dashboard
3. **Session Persistence Active** - Log files automatically saved locally
4. **Performance Optimized** - Deduplication and rate limiting functional
5. **Full System Integration** - Service running alongside all CantinaOS components

## 10. Testing Strategy

### 10.1 Unit Testing

**Service Testing**
```python
class TestLoggingService:
    async def test_log_capture(self):
        """Test Python log capture functionality."""
        logging_service = LoggingService(mock_event_bus, {})
        await logging_service._start()
        
        # Emit test log
        test_logger = logging.getLogger("test_service")
        test_logger.info("Test log message")
        
        # Verify capture
        assert len(logging_service._log_buffer._buffer) == 1
        assert logging_service._log_buffer._buffer[0].message == "Test log message"
        
    async def test_dashboard_emission(self):
        """Test dashboard log event emission."""
        mock_bus = AsyncMock()
        logging_service = LoggingService(mock_bus, {})
        
        # Process test log
        record = logging.LogRecord(
            name="test_service", level=logging.INFO, 
            pathname="", lineno=0, msg="Test message",
            args=(), exc_info=None
        )
        
        logging_service.handle_log_record(record)
        
        # Verify event emission
        mock_bus.emit.assert_called_with(
            EventTopics.DASHBOARD_LOG,
            Any[dict]
        )
```

### 10.2 Integration Testing

**End-to-End Testing**
- Test log flow from service → LoggingService → WebBridge → Dashboard
- Verify session file creation and content
- Test deduplication with repeated log messages
- Performance testing with high log volume

## 11. Success Metrics

### 11.1 Functional Success

**Log Capture**
- 100% of existing service logs captured
- All log levels properly categorized
- No loss of log data during normal operation

**Dashboard Integration**
- Real-time log display in web dashboard
- Proper service name mapping and display
- Functional filtering by service and log level

**Session Management**
- Automatic session file creation on startup
- Readable log format for post-session analysis
- Reliable file persistence without data loss

### 11.2 Performance Success

**Memory Efficiency**
- Service memory usage <5MB under normal operation
- Ring buffer prevents unlimited memory growth
- Deduplication reduces redundant log storage

**System Impact**
- <1% CPU usage during normal logging
- No blocking of main event loop
- Minimal impact on existing service performance

## 12. Risk Assessment & Mitigation

### 12.1 Technical Risks

**Log Volume Overflow**
- Risk: High-frequency debug logs overwhelming system
- Mitigation: Smart deduplication and configurable log levels
- Fallback: Automatic rate limiting and selective filtering

**Memory Usage Growth**
- Risk: Unbounded memory growth from log accumulation
- Mitigation: Fixed-size ring buffer with automatic cleanup
- Fallback: Configurable buffer size and garbage collection

**File I/O Blocking**
- Risk: File writing operations blocking event loop
- Mitigation: Fully asynchronous file operations using aiofiles and queue-based batching
- Fallback: In-memory-only mode if file system unavailable, graceful degradation to synchronous I/O if aiofiles unavailable

### 12.2 Integration Risks

**Service Startup Race Conditions**
- Risk: LoggingService missing early startup logs
- Mitigation: Start LoggingService early in service initialization
- Fallback: Buffer early logs and replay once service starts

**Event Bus Overload**
- Risk: Dashboard log events overwhelming event bus
- Mitigation: Rate limiting and selective emission
- Fallback: Disable dashboard streaming, maintain file logging

## 13. Future Enhancements

### 13.1 Advanced Analysis Features

**Log Analytics**
- Pattern recognition for error trends
- Performance metric extraction from logs
- Automated alert generation for critical issues

**Session Comparison**
- Compare log patterns between sessions
- Identify performance regressions
- Track improvement over time

### 13.2 Enhanced Dashboard Features

**Interactive Log Analysis**
- Real-time log search and filtering
- Log context expansion (show surrounding entries)
- Export functionality for specific log ranges

**Visual Log Analysis**
- Timeline visualization of system events
- Service interaction diagrams from log data
- Performance metrics charts derived from logs

## 14. Conclusion

The CantinaOS LoggingService provides a comprehensive solution for system monitoring and debugging by capturing all existing Python log output and making it available through the web dashboard. By following CantinaOS architectural standards and integrating seamlessly with the existing event-driven system, this service enables real-time system visibility without requiring changes to existing services.

**Key Benefits**:
- Immediate visibility into system activity through web dashboard
- Historical session logs for debugging and analysis
- Smart deduplication prevents log flooding
- Minimal performance impact on existing services
- Follows CantinaOS service patterns and standards

The implementation provides both real-time monitoring capabilities and persistent session logs, solving the current dashboard logging gap while maintaining system performance and reliability.

---

**Document Version**: 1.0  
**Last Updated**: 2025-06-08  
**Next Review**: Upon completion of Phase 1 implementation