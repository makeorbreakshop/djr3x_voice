"""
DebugService for CantinaOS

This service provides centralized debugging, logging, and system observability capabilities.
It handles asynchronous logging, command tracing, performance metrics, and state transition tracking.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from pyee.asyncio import AsyncIOEventEmitter
from pydantic import BaseModel

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    LogLevel, DebugLogPayload, CommandTracePayload,
    PerformanceMetricPayload, DebugConfigPayload, DebugCommandPayload
)

class DebugServiceConfig(BaseModel):
    """Configuration for the DebugService."""
    default_log_level: LogLevel = LogLevel.INFO
    component_log_levels: Dict[str, LogLevel] = {}
    trace_enabled: bool = True
    metrics_enabled: bool = True
    log_file: Optional[str] = None

class DebugService(BaseService):
    """
    DebugService handles centralized debugging and logging capabilities.
    
    Features:
    - Asynchronous log processing
    - Component-level log control
    - Command tracing
    - Performance metrics
    - State transition tracking
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Dict[str, Any],
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the DebugService.
        
        Args:
            event_bus: Event bus instance
            config: Configuration dictionary
            logger: Optional logger instance
        """
        super().__init__("debug", event_bus, logger)
        
        # Store event bus reference
        self.event_bus = event_bus
        
        # Initialize configuration
        self._config = DebugServiceConfig(**config)
        
        # Initialize state
        self._default_log_level = self._config.default_log_level
        self._component_log_levels = self._config.component_log_levels.copy()
        self._trace_enabled = self._config.trace_enabled
        self._metrics_enabled = self._config.metrics_enabled
        
        # Initialize log queue
        self._log_queue = asyncio.Queue()
        self._log_task = None
        
        # Initialize metrics storage
        self._metrics = {}
        
        self.logger.debug("DebugService initialized")
    
    async def _start(self) -> None:
        """Start the debug service."""
        try:
            # Start log processing task
            self._log_task = asyncio.create_task(self._process_log_queue())
            
            # Subscribe to debug events
            await self._setup_subscriptions()
            
            self.logger.info("DebugService started successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to start DebugService: {e}")
            raise
            
    async def _stop(self) -> None:
        """Stop the debug service."""
        try:
            # Cancel log processing task
            if self._log_task:
                self._log_task.cancel()
                try:
                    await self._log_task
                except asyncio.CancelledError:
                    pass
                    
            # Clear metrics
            self._metrics.clear()
            
            self.logger.info("DebugService stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping DebugService: {e}")
            raise
            
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Debug event subscriptions
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_LOG,
            self._handle_debug_log
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_COMMAND,
            self.handle_debug_level_command
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_COMMAND_TRACE,
            self._handle_command_trace
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_PERFORMANCE,
            self._handle_performance_metric
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_STATE_TRANSITION,
            self._handle_state_transition
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_CONFIG,
            self._handle_debug_config
        ))
        
        # Add subscription to LLM responses to display them even if ElevenLabs isn't working
        asyncio.create_task(self.subscribe(
            EventTopics.LLM_RESPONSE,
            self._handle_llm_response
        ))
        
        self.logger.debug("Debug event subscriptions set up")
        
    async def _process_log_queue(self) -> None:
        """Process logs from the queue."""
        while True:
            try:
                log_entry = await self._log_queue.get()
                # Process log entry based on configuration
                await self._write_log(log_entry)
                self._log_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error processing log entry: {e}")
                
    async def _write_log(self, log_entry: Dict[str, Any]) -> None:
        """Write a log entry to the appropriate destination."""
        try:
            # Format log message
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            component = log_entry.get("component", "unknown")
            level = log_entry.get("level", LogLevel.INFO)
            message = log_entry.get("message", "")
            
            log_line = f"{timestamp} - {component} - {level.name} - {message}"
            
            # Write to file if configured
            if self._config.log_file:
                with open(self._config.log_file, "a") as f:
                    f.write(log_line + "\n")
                    
            # Always write to console
            print(log_line)
            
        except Exception as e:
            self.logger.error(f"Error writing log entry: {e}")
            
    async def _handle_debug_log(self, payload: DebugLogPayload) -> None:
        """Handle debug log events."""
        try:
            component = payload.component
            level = payload.level
            
            # Check if we should log this level
            component_level = self._component_log_levels.get(
                component,
                self._default_log_level
            )
            
            if level.value >= component_level.value:
                await self._log_queue.put({
                    "timestamp": datetime.now(),
                    "component": component,
                    "level": level,
                    "message": payload.message
                })
                
        except Exception as e:
            self.logger.error(f"Error handling debug log: {e}")
            
    async def _handle_command_trace(self, payload: CommandTracePayload) -> None:
        """Handle command tracing events."""
        if not self._trace_enabled:
            return
            
        try:
            await self._log_queue.put({
                "timestamp": datetime.now(),
                "component": "command_trace",
                "level": LogLevel.DEBUG,
                "message": f"Command: {payload.command} - Params: {payload.params}"
            })
        except Exception as e:
            self.logger.error(f"Error handling command trace: {e}")
            
    async def _handle_performance_metric(self, payload: PerformanceMetricPayload) -> None:
        """Handle performance metric events."""
        if not self._metrics_enabled:
            return
            
        try:
            operation = payload.operation
            duration = payload.duration_ms
            
            if operation not in self._metrics:
                self._metrics[operation] = {
                    "count": 0,
                    "total_ms": 0,
                    "min_ms": float("inf"),
                    "max_ms": float("-inf")
                }
                
            metrics = self._metrics[operation]
            metrics["count"] += 1
            metrics["total_ms"] += duration
            metrics["min_ms"] = min(metrics["min_ms"], duration)
            metrics["max_ms"] = max(metrics["max_ms"], duration)
            
            # Log if duration exceeds threshold
            threshold = self._config.dict().get("performance_thresholds", {}).get(operation)
            if threshold and duration > threshold:
                await self._log_queue.put({
                    "timestamp": datetime.now(),
                    "component": "performance",
                    "level": LogLevel.WARNING,
                    "message": f"Operation {operation} took {duration}ms (threshold: {threshold}ms)"
                })
                
        except Exception as e:
            self.logger.error(f"Error handling performance metric: {e}")
            
    async def _handle_state_transition(self, payload: Dict[str, Any]) -> None:
        """Handle state transition events."""
        try:
            await self._log_queue.put({
                "timestamp": datetime.now(),
                "component": "state_transition",
                "level": LogLevel.INFO,
                "message": f"State transition: {payload['from_state']} -> {payload['to_state']}"
            })
        except Exception as e:
            self.logger.error(f"Error handling state transition: {e}")
            
    async def _handle_debug_config(self, payload: DebugConfigPayload) -> None:
        """Handle debug configuration updates."""
        try:
            if payload.default_level is not None:
                self._default_log_level = payload.default_level
                
            if payload.component_levels:
                self._component_log_levels.update(payload.component_levels)
                
            if payload.trace_enabled is not None:
                self._trace_enabled = payload.trace_enabled
                
            if payload.metrics_enabled is not None:
                self._metrics_enabled = payload.metrics_enabled
                
            await self._log_queue.put({
                "timestamp": datetime.now(),
                "component": "debug_config",
                "level": LogLevel.INFO,
                "message": "Debug configuration updated"
            })
            
        except Exception as e:
            self.logger.error(f"Error handling config update: {e}")
            
    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """Handle LLM response events and print them to the console.
        
        Args:
            payload: The LLM response payload
        """
        try:
            # Extract text from payload
            if not payload or "text" not in payload:
                self.logger.warning("Received empty text in LLM_RESPONSE event")
                return
                
            text = payload.get("text", "")
            conversation_id = payload.get("conversation_id", "Unknown")
            is_complete = payload.get("is_complete", True)
            
            # Only print complete responses or the first chunk of a streaming response
            if is_complete or not hasattr(self, '_seen_conversation_ids') or conversation_id not in self._seen_conversation_ids:
                # Track conversation IDs we've seen to avoid printing duplicate chunks
                if not hasattr(self, '_seen_conversation_ids'):
                    self._seen_conversation_ids = set()
                
                if is_complete:
                    # For complete responses, clear the ID from seen set
                    self._seen_conversation_ids.discard(conversation_id)
                else:
                    # For streaming chunks, add the ID to seen set
                    self._seen_conversation_ids.add(conversation_id)
                
                # Print the response with clear formatting
                print("\n" + "="*50)
                print(f"🤖 LLM RESPONSE (conv_id: {conversation_id[:8]}...):")
                print("-"*50)
                print(text)
                print("="*50 + "\n")
                
        except Exception as e:
            self.logger.error(f"Error handling LLM response: {str(e)}")

    async def handle_debug_level_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle debug level command.
        
        Args:
            payload: DebugCommandPayload as dictionary
            
        Returns:
            Dict containing response message
        """
        try:
            # Convert payload to DebugCommandPayload model
            debug_payload = DebugCommandPayload(**payload)
            
            # Convert LogLevel enum to Python's logging level
            python_log_level = {
                LogLevel.DEBUG: logging.DEBUG,
                LogLevel.INFO: logging.INFO,
                LogLevel.WARNING: logging.WARNING,
                LogLevel.ERROR: logging.ERROR,
                LogLevel.CRITICAL: logging.CRITICAL
            }.get(debug_payload.level, logging.INFO)
            
            if debug_payload.component.lower() == "all":
                # Set the default log level for all components
                old_default = self._default_log_level
                self._default_log_level = debug_payload.level
                # Clear component-specific levels to ensure all use the default
                self._component_log_levels.clear()
                
                # Get the root logger
                root_logger = logging.getLogger()
                
                # Update root logger level
                root_logger.setLevel(python_log_level)
                
                # Update all handlers on the root logger
                for handler in root_logger.handlers:
                    handler.setLevel(python_log_level)
                
                # Update all existing loggers and their handlers
                for logger_name in logging.root.manager.loggerDict:
                    logger = logging.getLogger(logger_name)
                    logger.setLevel(python_log_level)
                    
                    # Also update all handlers for this logger
                    for handler in logger.handlers:
                        handler.setLevel(python_log_level)
                
                # Create a temporary StreamHandler to override the default handler
                if debug_payload.level in [LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL]:
                    # The trick is to add a high-level filter to the root logger's handlers
                    for handler in root_logger.handlers:
                        if isinstance(handler, logging.StreamHandler):
                            # Remove any existing filters we might have added
                            handler.filters = [f for f in handler.filters if not hasattr(f, '_dj_r3x_level_filter')]
                            
                            # Add a new filter that blocks messages below our level
                            class LevelFilter(logging.Filter):
                                def __init__(self, level):
                                    super().__init__()
                                    self.level = level
                                    self._dj_r3x_level_filter = True
                                    
                                def filter(self, record):
                                    return record.levelno >= self.level
                            
                            handler.addFilter(LevelFilter(python_log_level))
                
                self.logger.debug(
                    f"Changed default log level from {old_default} (value: {old_default.value}) "
                    f"to {debug_payload.level} (value: {debug_payload.level.value})"
                )
                return {"message": f"Successfully set ALL components log level to {debug_payload.level.name}"}
            else:
                # Set specific component log level
                old_level = self._component_log_levels.get(debug_payload.component, self._default_log_level)
                self._component_log_levels[debug_payload.component] = debug_payload.level
                
                # Try to update the specific logger if it exists
                logger = logging.getLogger(debug_payload.component)
                logger.setLevel(python_log_level)
                
                # Update handlers for this logger
                for handler in logger.handlers:
                    handler.setLevel(python_log_level)
                
                self.logger.debug(
                    f"Changed log level for {debug_payload.component} from {old_level} (value: {old_level.value}) "
                    f"to {debug_payload.level} (value: {debug_payload.level.value})"
                )
                return {"message": f"Successfully set {debug_payload.component} log level to {debug_payload.level.name}"}
                
        except Exception as e:
            error_msg = f"Error setting log level: {str(e)}"
            self.logger.error(error_msg)
            return {"message": error_msg, "is_error": True} 