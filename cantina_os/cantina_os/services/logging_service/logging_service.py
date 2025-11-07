"""
CantinaOS LoggingService

Centralized logging aggregation service that captures all Python log output
and provides structured log data to the web dashboard and persistent storage.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel

from ...base_service import BaseService
from ...core.event_topics import EventTopics
from ...event_payloads import ServiceStatus


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


class LogRingBuffer:
    """Fixed-size ring buffer for in-memory log storage."""

    def __init__(self, max_size: int = 1000):
        self._buffer = deque(maxlen=max_size)
        self._lock = asyncio.Lock()

    async def add_log(self, log_entry: LogEntry):
        """Add a log entry to the buffer."""
        async with self._lock:
            self._buffer.append(log_entry)


class SessionFileManager:
    """Manages session-based log file persistence."""

    def __init__(self, base_path: str):
        self._base_path = Path(base_path)
        self._current_session_file = None
        self._session_id = None

        # Create logs directory if it doesn't exist
        self._base_path.mkdir(parents=True, exist_ok=True)

    def start_session(self) -> str:
        """Start new logging session with timestamped file."""
        self._session_id = f"cantina-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self._current_session_file = self._base_path / f"{self._session_id}.log"
        return self._session_id

    @property
    def session_id(self) -> str:
        """Get the current session ID."""
        return self._session_id

    @property
    def current_session_file(self) -> Path:
        """Get the current session file path."""
        return self._current_session_file


class CantinaLogHandler(logging.Handler):
    """Custom logging handler that captures all CantinaOS log output."""

    def __init__(self, logging_service):
        super().__init__()
        self._logging_service = logging_service

    def emit(self, record):
        """Process log record and forward to LoggingService."""
        self._logging_service.handle_log_record(record)


class LoggingService(BaseService):
    """Centralized logging service for CantinaOS system monitoring."""

    class _Config(BaseModel):
        """LoggingService configuration."""

        log_level: str = "INFO"
        max_memory_logs: int = 1000
        session_file_path: str = "./logs"
        enable_dashboard_streaming: bool = True
        file_flush_interval: int = 5  # seconds
        deduplication_window: int = 30  # seconds
        max_queue_size: int = 10000
        batch_size: int = 100

    def __init__(self, event_bus, config=None, name="logging_service"):
        super().__init__(
            service_name=name,
            event_bus=event_bus,
            logger=logging.getLogger("cantina_os.logging_service"),
        )
        self._config = self._Config(**(config or {}))
        self._log_buffer = LogRingBuffer(self._config.max_memory_logs)
        self._session_manager = SessionFileManager(self._config.session_file_path)
        self._log_handler = None
        self._deduplication_cache = {}
        self._tasks = []
        self._file_write_queue = asyncio.Queue(maxsize=self._config.max_queue_size)
        
        # Circuit breaker for excessive logging
        self._log_count_window = 0
        self._log_count_start = time.time()
        self._circuit_breaker_active = False
        self._max_logs_per_second = 50  # Emergency limit

    async def _start(self) -> None:
        """Start the logging service and install log handler."""
        await self._emit_status(ServiceStatus.STARTING, "Initializing LoggingService")

        # Install custom log handler to capture all Python logs
        self._log_handler = CantinaLogHandler(self)
        self._log_handler.setLevel(getattr(logging, self._config.log_level))

        # Add to root logger to capture all service logs
        root_logger = logging.getLogger()
        root_logger.addHandler(self._log_handler)

        # Start new session
        session_id = self._session_manager.start_session()

        # Start background tasks
        file_writer_task = asyncio.create_task(self._file_writer_loop())
        queue_processor_task = asyncio.create_task(self._process_file_queue())
        self._tasks.extend([file_writer_task, queue_processor_task])

        await self._emit_status(
            ServiceStatus.RUNNING, f"LoggingService started - Session: {session_id}"
        )
        print(f"LoggingService started - Session: {session_id}")

    async def _stop(self) -> None:
        """Stop the logging service and cleanup resources."""
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
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True), timeout=5.0
                )
            except asyncio.TimeoutError:
                print("LoggingService: Timeout waiting for tasks to complete")

        # Flush any remaining logs to file
        await self._flush_remaining_logs()

        # Clear all state
        self._tasks.clear()

        await self._emit_status(ServiceStatus.STOPPED, "LoggingService stopped")

    def handle_log_record(self, record: logging.LogRecord) -> None:
        """Process incoming log record from Python logging system."""
        try:
            # Filter out problematic loggers to prevent feedback loops
            if self._should_filter_logger(record.name):
                return
                
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
                entry_id=f"{record.created}-{hash(record.getMessage())}",
            )

            # Smart deduplication
            if not self._should_deduplicate(log_entry):
                # Use thread-safe synchronous operations from logging handler
                try:
                    # Add to file write queue synchronously (thread-safe)
                    self._file_write_queue.put_nowait(log_entry)
                    
                    # Memory buffer is only for dashboard/in-memory access
                    # File writing is handled entirely by the async queue processor
                    if hasattr(self._log_buffer, '_buffer'):
                        self._log_buffer._buffer.append(log_entry)
                    
                    # Dashboard emission now handled by background async task
                        
                except Exception as queue_error:
                    print(f"LoggingService: Error queuing log entry: {queue_error}")

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
            "web_bridge": "Web Bridge",
            "logging_service": "Logging Service",
        }

        # Extract base service name
        base_name = logger_name.split(".")[0] if "." in logger_name else logger_name
        return service_map.get(base_name, base_name.title())

    def _should_filter_logger(self, logger_name: str) -> bool:
        """Filter out problematic loggers that cause feedback loops."""
        # List of logger names/patterns to filter out
        filtered_loggers = [
            "cantina_os.logging_service",  # CRITICAL: Filter out own logs to prevent recursion
            # "cantina_os.services.web_bridge",  # Temporarily allow for debugging
            # "cantina_os.services.web_bridge_service",  # Temporarily allow for debugging
            "socketio",           # Socket.IO library logs
            "engineio",           # Engine.IO library logs  
            "websocket",          # WebSocket library logs
            "aiohttp",            # HTTP client logs that might include WebSocket traffic
            "urllib3",            # HTTP library that might log WebSocket requests
            "uvicorn",            # FastAPI server logs
            "uvloop",             # Event loop logs
            "asyncio",            # Asyncio logs that can cause recursion
        ]
        
        # AGGRESSIVE FILTERING: Check if logger name matches or contains any filtered pattern
        logger_lower = logger_name.lower()
        for filtered in filtered_loggers:
            if filtered in logger_lower:
                return True
        
        # ADDITIONAL FILTER: Block any logger that contains websocket-related terms
        websocket_terms = ["socket", "client", "connection", "packet", "ping", "pong"]
        for term in websocket_terms:
            if term in logger_lower and any(ws in logger_lower for ws in ["socket", "engine", "web"]):
                return True
                
        return False

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
            k: v for k, v in self._deduplication_cache.items() if v > cutoff
        }

        return False  # Don't deduplicate

    async def _emit_dashboard_log(self, log_entry: LogEntry) -> None:
        """Emit log entry to dashboard via event bus."""
        try:
            dashboard_payload = {
                "timestamp": log_entry.timestamp,
                "level": log_entry.level,
                "service": log_entry.service,
                "message": log_entry.message,
                "session_id": log_entry.session_id,
                "entry_id": log_entry.entry_id,
            }

            # Emit to WebBridge for dashboard consumption
            await self.emit(EventTopics.DASHBOARD_LOG, dashboard_payload)

        except Exception as e:
            print(f"LoggingService: Error emitting dashboard log: {e}")

    async def _queue_for_file_write(self, log_entry: LogEntry) -> None:
        """Queue log entry for file writing."""
        try:
            await self._file_write_queue.put(log_entry)
        except asyncio.QueueFull:
            # Log queue overflow - could implement fallback here
            print("LoggingService: File write queue full, dropping log entry")

    async def _process_file_queue(self) -> None:
        """Background task for batch processing file write queue."""
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
                    
                    # Also emit to dashboard for each log in batch
                    if self._config.enable_dashboard_streaming:
                        for log_entry in batch:
                            await self._emit_dashboard_log(log_entry)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"LoggingService: Error processing file queue: {e}")

    async def _file_writer_loop(self) -> None:
        """Background task for writing logs to session file."""
        while True:
            try:
                await asyncio.sleep(self._config.file_flush_interval)
                await self._flush_session_file()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"LoggingService: Error in file writer loop: {e}")

    async def _write_logs_async(self, logs_to_write: List[LogEntry]) -> None:
        """Write logs to file using async I/O."""
        try:
            # Try to use aiofiles for async file operations
            import aiofiles

            async with aiofiles.open(
                self._session_manager.current_session_file, "a", encoding="utf-8"
            ) as f:
                for log_entry in logs_to_write:
                    log_line = f"[{log_entry.timestamp}] {log_entry.level:8} {log_entry.service:20} {log_entry.message}\n"
                    await f.write(log_line)
        except ImportError:
            # Fallback to synchronous I/O
            await self._write_logs_sync(logs_to_write)
        except Exception as e:
            print(f"LoggingService: Error writing logs async: {e}")

    async def _write_logs_sync(self, logs_to_write: List[LogEntry]) -> None:
        """Fallback synchronous file writing."""
        try:
            with open(
                self._session_manager.current_session_file, "a", encoding="utf-8"
            ) as f:
                for log_entry in logs_to_write:
                    log_line = f"[{log_entry.timestamp}] {log_entry.level:8} {log_entry.service:20} {log_entry.message}\n"
                    f.write(log_line)
        except Exception as e:
            print(f"LoggingService: Error writing logs sync: {e}")

    async def _flush_session_file(self) -> None:
        """Memory buffer maintenance - file writing is handled by async queue processor."""
        if not self._session_manager.current_session_file:
            return

        try:
            # This method now only maintains the memory buffer
            # All file writing is handled by _process_file_queue() to prevent duplicates
            # Clean up old entries from memory buffer if needed
            async with self._log_buffer._lock:
                buffer_size = len(self._log_buffer._buffer)
                if buffer_size > self._config.max_memory_logs:
                    # Remove old entries beyond the max limit
                    excess = buffer_size - self._config.max_memory_logs
                    for _ in range(excess):
                        self._log_buffer._buffer.popleft()

        except Exception as e:
            print(f"LoggingService: Error maintaining memory buffer: {e}")

    async def _flush_remaining_logs(self) -> None:
        """Flush any remaining logs during shutdown."""
        try:
            # Process any remaining items in the async queue only
            # This prevents duplicate writing since memory buffer was already processed
            remaining_logs = []
            while not self._file_write_queue.empty():
                try:
                    log_entry = self._file_write_queue.get_nowait()
                    remaining_logs.append(log_entry)
                except asyncio.QueueEmpty:
                    break

            if remaining_logs:
                await self._write_logs_async(remaining_logs)

            # Note: No longer calling _flush_session_file() to prevent duplicate writing

        except Exception as e:
            print(f"LoggingService: Error flushing remaining logs: {e}")
