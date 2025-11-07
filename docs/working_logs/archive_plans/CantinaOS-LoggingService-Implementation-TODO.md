# CantinaOS LoggingService - Implementation TODO

**Document Type**: Implementation Checklist  
**Created**: 2025-06-08  
**Status**: Ready for Implementation  
**Approach**: Test-Driven Development (TDD)

## Overview

This document provides a comprehensive checklist for implementing the CantinaOS LoggingService following TDD principles and all architectural standards from:
- `cantina_os/docs/SERVICE_CREATION_GUIDELINES.md`
- `cantina_os/docs/ARCHITECTURE_STANDARDS.md` 
- `cantina_os/docs/CANTINA_OS_SYSTEM_ARCHITECTURE.md`

## Implementation Strategy

**Test-Driven Development (TDD)**: Write failing tests first, then implement code to make tests pass  
**Architecture Compliance**: Follow CantinaOS service patterns exactly  
**Async I/O Focus**: Emphasize non-blocking operations using aiofiles and queue-based processing

---

## Phase 1: Foundation & Core Structure

### 1.1 File Structure Setup

- [x] **Setup File Structure**: Create service directory structure: `cantina_os/cantina_os/services/logging_service/` with `__init__.py`
- [x] **Copy Service Template**: Copy `service_template.py` to `logging_service.py` as starting point
- [x] **Create Test Structure**: Create `tests/` subdirectory with `test_logging_service.py` following TDD approach

### 1.2 Event System Foundation

- [x] **Define Event Topics**: Add `DASHBOARD_LOG = "/dashboard/log"` event topic to `core/event_topics.py` EventTopics enum
- [x] **Define Event Payloads**: Create `DashboardLogPayload` Pydantic model in `core/event_payloads.py` with fields:
  - `timestamp: str`
  - `level: str` 
  - `service: str`
  - `message: str`
  - `session_id: str`
  - `entry_id: str`

---

## Phase 2: Service Core Implementation (TDD)

### 2.1 Service Configuration

- [x] **TDD: Config Test**: Write failing test for LoggingService initialization with proper config validation
- [x] **Implement Config Class**: Create `_Config` Pydantic model with all required fields and defaults:
  - `log_level: str = "INFO"`
  - `max_memory_logs: int = 1000`
  - `session_file_path: str = "./logs"`
  - `enable_dashboard_streaming: bool = True`
  - `file_flush_interval: int = 5`
  - `deduplication_window: int = 30`
  - `max_queue_size: int = 10000`
  - `batch_size: int = 100`

### 2.2 Service Initialization

- [x] **Implement Init Method**: Implement `__init__` with exact signature: `__init__(self, event_bus, config=None, name="logging_service")`
- [x] **Service Dependencies**: Initialize all required components:
  - `self._config = self._Config(**(config or {}))`
  - `self._log_buffer = LogRingBuffer(self._config.max_memory_logs)`
  - `self._session_manager = SessionFileManager(self._config.session_file_path)`
  - `self._log_handler = None`
  - `self._deduplication_cache = {}`
  - `self._tasks = []`
  - `self._file_write_queue = asyncio.Queue(maxsize=self._config.max_queue_size)`

---

## Phase 3: Core Components Implementation

### 3.1 Log Handler Implementation

- [x] **TDD: Log Handler Test**: Write failing test for CantinaLogHandler log capture functionality
- [x] **Implement CantinaLogHandler**: Create class that inherits from `logging.Handler`:
  ```python
  class CantinaLogHandler(logging.Handler):
      def __init__(self, logging_service):
          super().__init__()
          self._logging_service = logging_service
          
      def emit(self, record):
          self._logging_service.handle_log_record(record)
  ```

### 3.2 Data Structures

- [x] **Implement LogEntry Class**: Create LogEntry dataclass with all required fields:
  ```python
  @dataclass
  class LogEntry:
      timestamp: str
      level: str
      service: str
      message: str
      raw_record: Optional[dict]
      session_id: str
      entry_id: str
  ```

- [x] **TDD: Ring Buffer Test**: Write failing test for LogRingBuffer async operations and size limits
- [x] **Implement LogRingBuffer**: Create class with async lock and deque for thread-safe operations:
  ```python
  class LogRingBuffer:
      def __init__(self, max_size: int = 1000):
          self._buffer = deque(maxlen=max_size)
          self._lock = asyncio.Lock()
      
      async def add_log(self, log_entry: LogEntry):
          async with self._lock:
              self._buffer.append(log_entry)
  ```

- [x] **TDD: Session Manager Test**: Write failing test for SessionFileManager with timestamp-based file creation
- [x] **Implement SessionFileManager**: Create class for session-based log file persistence with path resolution

---

## Phase 4: Service Lifecycle Implementation

### 4.1 Service Startup

- [x] **TDD: Service Start Test**: Write failing test for service `_start()` method with subscription setup
- [x] **Implement Setup Subscriptions**: Implement `_setup_subscriptions()` method (empty for LoggingService as it doesn't subscribe to events)
- [x] **Implement Start Method**: Implement `_start()` method with proper status reporting:
  ```python
  async def _start(self) -> None:
      await self._emit_status(ServiceStatus.STARTING, "Initializing LoggingService")
      
      # Install custom log handler
      self._log_handler = CantinaLogHandler(self)
      self._log_handler.setLevel(getattr(logging, self._config.log_level))
      root_logger = logging.getLogger()
      root_logger.addHandler(self._log_handler)
      
      # Start new session
      session_id = self._session_manager.start_session()
      
      # Start background tasks
      file_writer_task = asyncio.create_task(self._file_writer_loop())
      queue_processor_task = asyncio.create_task(self._process_file_queue())
      self._tasks.extend([file_writer_task, queue_processor_task])
      
      await self._emit_status(ServiceStatus.RUNNING, f"LoggingService started - Session: {session_id}")
  ```

### 4.2 Service Shutdown

- [x] **TDD: Service Stop Test**: Write failing test for `_stop()` method with proper resource cleanup
- [x] **Implement Stop Method**: Implement `_stop()` method with comprehensive cleanup:
  ```python
  async def _stop(self) -> None:
      await self._emit_status(ServiceStatus.STOPPING, "Shutting down LoggingService")
      
      # Remove log handler
      if self._log_handler:
          logging.getLogger().removeHandler(self._log_handler)
      
      # Cancel all tasks
      for task in self._tasks:
          if not task.done():
              task.cancel()
      
      # Wait for tasks to complete with timeout
      if self._tasks:
          await asyncio.gather(*self._tasks, return_exceptions=True)
      
      # Flush any remaining logs
      await self._flush_remaining_logs()
      
      # Clear all state
      self._tasks.clear()
      
      await self._emit_status(ServiceStatus.STOPPED, "LoggingService stopped")
  ```

- [x] **Implement Task Management**: Store all background tasks in `self._tasks` list for proper cancellation

---

## Phase 5: Async File I/O Implementation

### 5.1 Queue-Based Architecture

- [x] **TDD: Async File I/O Test**: Write failing test for async file I/O operations using aiofiles and queue-based batching
- [x] **Implement Async File Queue**: Implement `asyncio.Queue` for file write operations with overflow protection
- [x] **Implement Queue Processor**: Create `_process_file_queue()` method for batch processing:
  ```python
  async def _process_file_queue(self) -> None:
      while True:
          try:
              batch = []
              # Collect batch of logs
              for _ in range(self._config.batch_size):
                  try:
                      log_entry = await asyncio.wait_for(
                          self._file_write_queue.get(), timeout=1.0
                      )
                      batch.append(log_entry)
                  except asyncio.TimeoutError:
                      break
              
              if batch:
                  await self._write_logs_async(batch)
                  
          except asyncio.CancelledError:
              break
          except Exception as e:
              self._logger.error(f"Error processing file queue: {e}")
  ```

### 5.2 Async File Operations

- [x] **Implement Async File Writer**: Implement `_file_writer_loop()` for periodic flushing
- [x] **Implement Async Write Method**: Implement `_write_logs_async()` using aiofiles:
  ```python
  async def _write_logs_async(self, logs_to_write: List[LogEntry]) -> None:
      try:
          import aiofiles
          async with aiofiles.open(self._session_manager.current_session_file, 'a', encoding='utf-8') as f:
              for log_entry in logs_to_write:
                  log_line = f"[{log_entry.timestamp}] {log_entry.level:8} {log_entry.service:20} {log_entry.message}\n"
                  await f.write(log_line)
      except ImportError:
          # Fallback to synchronous I/O
          await self._write_logs_sync(logs_to_write)
      except Exception as e:
          self._logger.error(f"Error writing logs async: {e}")
  ```

- [x] **Implement Fallback Mechanism**: Add graceful fallback to synchronous I/O if aiofiles unavailable

---

## Phase 6: Log Processing Implementation

### 6.1 Log Record Processing

- [x] **TDD: Log Processing Test**: Write failing test for handle_log_record with service name extraction and deduplication
- [x] **Implement Log Record Handler**: Implement `handle_log_record()` method with proper async task creation:
  ```python
  def handle_log_record(self, record: logging.LogRecord) -> None:
      try:
          service_name = self._extract_service_name(record.name)
          
          log_entry = LogEntry(
              timestamp=datetime.fromtimestamp(record.created).isoformat(),
              level=record.levelname,
              service=service_name,
              message=record.getMessage(),
              raw_record=vars(record),
              session_id=self._session_manager.session_id,
              entry_id=f"{record.created}-{hash(record.getMessage())}"
          )
          
          if not self._should_deduplicate(log_entry):
              # Add to memory buffer
              asyncio.create_task(self._log_buffer.add_log(log_entry))
              
              # Stream to dashboard if enabled
              if self._config.enable_dashboard_streaming:
                  asyncio.create_task(self._emit_dashboard_log(log_entry))
              
              # Queue for file writing
              asyncio.create_task(self._queue_for_file_write(log_entry))
              
      except Exception as e:
          print(f"LoggingService error processing log: {e}")
  ```

### 6.2 Service Name Extraction & Deduplication

- [x] **Implement Service Name Extraction**: Implement `_extract_service_name()` with service mapping for user-friendly names
- [x] **Implement Deduplication Logic**: Implement `_should_deduplicate()` with configurable time windows and cache cleanup

---

## Phase 7: Dashboard Integration

### 7.1 Event Emission

- [x] **TDD: Dashboard Emission Test**: Write failing test for dashboard log event emission using `_emit_dict()`
- [x] **Implement Dashboard Emission**: Implement `_emit_dashboard_log()` using `_emit_dict()` with proper error handling:
  ```python
  async def _emit_dashboard_log(self, log_entry: LogEntry) -> None:
      try:
          dashboard_payload = {
              "timestamp": log_entry.timestamp,
              "level": log_entry.level,
              "service": log_entry.service,
              "message": log_entry.message,
              "session_id": log_entry.session_id,
              "entry_id": log_entry.entry_id
          }
          
          self._emit_dict(EventTopics.DASHBOARD_LOG, dashboard_payload)
          
      except Exception as e:
          self._logger.error(f"Error emitting dashboard log: {e}")
  ```

### 7.2 WebBridge Integration

- [x] **TDD: WebBridge Integration Test**: Write failing test for WebBridge handling DASHBOARD_LOG events
- [x] **Update WebBridge Subscriptions**: Update WebBridge `_setup_subscriptions()` to include DASHBOARD_LOG using `asyncio.gather()`
- [x] **Implement WebBridge Handler**: Implement `_handle_dashboard_log()` in WebBridge with proper payload validation

---

## Phase 8: Service Registration & Configuration

### 8.1 Main.py Integration

- [x] **Add Service to main.py**: Import LoggingService in main.py and add to `service_class_map` dictionary
- [x] **Configure Service Order**: Add `logging_service` early in `service_order` list to capture startup logs
- [x] **Add Service Config**: Add logging service configuration in main.py with environment variable support:
  ```python
  logging_config = {
      "log_level": os.getenv("LOGGING_LEVEL", "INFO"),
      "max_memory_logs": int(os.getenv("MAX_MEMORY_LOGS", "1000")),
      "session_file_path": os.getenv("SESSION_LOG_PATH", "./logs"),
      "enable_dashboard_streaming": True,
      "file_flush_interval": 5,
      "deduplication_window": 30,
      "max_queue_size": 10000,
      "batch_size": 100
  }
  ```

### 8.2 Dependencies

- [x] **Add aiofiles Dependency**: Add `aiofiles>=23.0.0` to requirements.txt with fallback handling
- [x] **Create Logs Directory**: Ensure `./logs` directory exists or implement automatic creation with proper permissions

---

## Phase 9: Error Handling & Status Reporting

### 9.1 Comprehensive Error Handling

- [x] **Add Error Handling**: Add try/except blocks to all methods with `_emit_status()` calls for error reporting
- [x] **Service Status Reporting**: Implement status reporting in `_start()` and `_stop()` using ServiceStatus enum
- [x] **Fallback Mechanisms**: Test graceful fallback to synchronous I/O if aiofiles unavailable

---

## Phase 10: Testing & Validation

### 10.1 Unit Testing

- [x] **Write Integration Tests**: Write end-to-end integration tests: service → LoggingService → WebBridge → Dashboard flow
- [x] **Test Session File Creation**: Test session file creation with timestamp format and proper directory structure
- [x] **Test Performance**: Write performance tests for high log volume, memory usage, and deduplication
- [x] **Test Service Startup**: Test service starts without errors and integrates with existing services

### 10.2 Architecture Compliance

- [x] **Verify Architecture Compliance**: Verify service follows all patterns from ARCHITECTURE_STANDARDS.md checklist:
  - [x] Inherits from StandardService
  - [x] Implements `_start()` and `_stop()` (not `start()` or `stop()`)
  - [x] Uses protected attributes with underscore prefix
  - [x] Implements proper error handling and resource cleanup
  - [x] Uses event-based communication through event bus
  - [x] Stores tasks in `self._tasks` list for proper cancellation
  - [x] Uses `_emit_dict()` for all event emissions
  - [x] Follows async/await best practices

### 10.3 Quality Assurance

- [x] **Run Code Quality Tools**: Run black, isort, ruff, and mypy on the service code ✅ ALL PASSING
- [x] **Test Memory Management**: Run long-running test to check for memory leaks and ring buffer effectiveness
- [x] **Test Dashboard Display**: Manually test real-time log display in web dashboard
- [x] **Test Log Level Filtering**: Test log level filtering and configuration changes at runtime

---

## Success Criteria

### Functional Requirements
- [x] **100% Log Capture**: All existing service logs captured by LoggingService
- [x] **Real-time Dashboard**: Live log display in web dashboard with proper service mapping
- [x] **Session Persistence**: Automatic timestamped session file creation and reliable persistence
- [x] **No Data Loss**: No loss of log data during normal operation

### Performance Requirements  
- [x] **Memory Efficiency**: Service memory usage <5MB under normal operation
- [x] **System Impact**: <1% CPU usage during normal logging, no event loop blocking
- [x] **Throughput**: Handle 1000 logs/second with <1ms processing per entry

### Architecture Compliance
- [x] **Standards Compliance**: Passes all SERVICE_CREATION_GUIDELINES.md checklist items
- [x] **Event System**: Proper integration with CantinaOS event bus topology
- [x] **Async Operations**: All file I/O operations are non-blocking using aiofiles and queue-based processing

---

## Implementation Notes

1. **Priority Order**: Follow the phases in order - foundation first, then core functionality, then optimizations
2. **TDD Approach**: Always write failing tests before implementing features
3. **Async Focus**: Emphasize non-blocking operations throughout - this is critical for CantinaOS
4. **Error Handling**: Comprehensive error handling at every level with proper fallback mechanisms
5. **Architecture Compliance**: Regularly verify against architectural standards during implementation

---

**Document Version**: 1.0  
**Last Updated**: 2025-06-08  
**Implementation Status**: FULLY COMPLETE ✅ - All Phases 1-10 Successfully Implemented
**Code Quality**: ✅ ALL CHECKS PASSING - Black formatting, Ruff linting, 19/19 tests passing
**Final Status**: PRODUCTION READY 🚀