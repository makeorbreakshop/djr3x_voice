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

from .service_template import StandardService
from .core.event_topics import EventTopics
from .core.event_payloads import (
    LogLevel, DebugLogPayload, CommandTracePayload,
    PerformanceMetricPayload, DebugConfigPayload
)

class DebugServiceConfig(BaseModel):
    """Configuration for the DebugService."""
    default_log_level: LogLevel = LogLevel.INFO
    component_log_levels: Dict[str, LogLevel] = {}
    trace_enabled: bool = True
    metrics_enabled: bool = True
    log_file: Optional[str] = None

class DebugService(StandardService):
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
        super().__init__(
            event_bus=event_bus,
            config=config,
            logger=logger,
            name="debug"
        )
        
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
        await super()._start()
        
        # Start log processing task
        self._log_task = asyncio.create_task(self._process_log_queue())
        
        self.logger.info("DebugService started")
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Debug event subscriptions
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_LOG,
            self._handle_debug_log
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.DEBUG_COMMAND,
            self._handle_debug_command
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
        
        # Subscribe to LLM responses
        asyncio.create_task(self.subscribe(
            EventTopics.LLM_RESPONSE,
            self._handle_llm_response
        ))
        
        # Also track transcription events
        asyncio.create_task(self.subscribe(
            EventTopics.TRANSCRIPTION_FINAL,
            self._handle_transcription
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.TRANSCRIPTION_INTERIM,
            self._handle_transcription
        ))
        
        # Track mouse-click triggered transcript events
        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STOPPED,
            self._handle_voice_listening_stopped
        ))
        
        self.logger.debug("Debug event subscriptions set up")
    
    async def _process_log_queue(self) -> None:
        """Process logs from the queue asynchronously."""
        while True:
            try:
                # Get log entry from queue
                log_entry = await self._log_queue.get()
                
                try:
                    # Get log level and component
                    component = log_entry.get("component", "unknown")
                    level = LogLevel(log_entry.get("level", LogLevel.INFO))
                    message = log_entry.get("message", "")
                    details = log_entry.get("details", {})
                    
                    # Get component log level
                    component_level = self._component_log_levels.get(
                        component,
                        self._default_log_level
                    )
                    
                    # Debug logging
                    self.logger.debug(
                        f"Processing log entry - Level: {level} (value: {level.value}), "
                        f"Component Level: {component_level} (value: {component_level.value}), "
                        f"Message: {message}"
                    )
                    
                    # Convert levels to numeric values for comparison
                    level_value = level.value
                    component_level_value = component_level.value
                    
                    # Only process if log level is sufficient
                    if level_value >= component_level_value:
                        # Format log message
                        timestamp = datetime.now().isoformat()
                        formatted_message = f"[{timestamp}] [{component}] {message}"
                        if details:
                            formatted_message += f" | {details}"
                        
                        # Write to log file if configured
                        if self._config.log_file:
                            with open(self._config.log_file, "a") as f:
                                f.write(formatted_message + "\n")
                        
                        # Emit log event
                        await self.emit(
                            EventTopics.DEBUG_LOG,
                            {
                                "level": level,
                                "component": component,
                                "message": formatted_message,
                                "timestamp": timestamp,
                                "details": details
                            }
                        )
                        self.logger.debug("Emitted log event")
                    else:
                        self.logger.debug("Filtered out log event")
                
                finally:
                    # Always mark task as done, even if processing failed
                    self._log_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error processing log: {str(e)}")
    
    async def _handle_debug_log(self, payload: Dict[str, Any]) -> None:
        """Handle debug log events."""
        try:
            await self._log_queue.put(payload)
        except Exception as e:
            self.logger.error(f"Error handling debug log: {str(e)}")
    
    async def _handle_command_trace(self, payload: Dict[str, Any]) -> None:
        """Handle command trace events."""
        if not self._trace_enabled:
            return
            
        try:
            await self.emit(
                EventTopics.DEBUG_COMMAND_TRACE,
                CommandTracePayload(**payload).dict()
            )
        except Exception as e:
            self.logger.error(f"Error handling command trace: {str(e)}")
    
    async def _handle_performance_metric(self, payload: Dict[str, Any]) -> None:
        """Handle performance metric events."""
        if not self._metrics_enabled:
            return
            
        try:
            metric = PerformanceMetricPayload(**payload)
            self._metrics[metric.metric_name] = {
                "value": metric.value,
                "unit": metric.unit,
                "timestamp": datetime.now().isoformat()
            }
            
            await self.emit(
                EventTopics.DEBUG_PERFORMANCE,
                metric.dict()
            )
        except Exception as e:
            self.logger.error(f"Error handling performance metric: {str(e)}")
    
    async def _handle_state_transition(self, payload: Dict[str, Any]) -> None:
        """Handle state transition events."""
        try:
            await self.emit(
                EventTopics.DEBUG_STATE_TRANSITION,
                payload
            )
        except Exception as e:
            self.logger.error(f"Error handling state transition: {str(e)}")
    
    async def _handle_debug_config(self, payload: Dict[str, Any]) -> None:
        """Handle debug configuration updates."""
        try:
            component = payload.get("component")
            log_level = payload.get("log_level")
            enable_tracing = payload.get("enable_tracing")
            enable_metrics = payload.get("enable_metrics")
            
            if component and log_level:
                self._component_log_levels[component] = LogLevel(log_level)
            
            if enable_tracing is not None:
                self._trace_enabled = enable_tracing
            
            if enable_metrics is not None:
                self._metrics_enabled = enable_metrics
                
            self.logger.info(f"Debug configuration updated: {payload}")
            
        except Exception as e:
            self.logger.error(f"Error handling debug config: {str(e)}")
    
    async def _handle_debug_command(self, payload: Dict[str, Any]) -> None:
        """Handle debug commands from CLI.
        
        Args:
            payload: Command payload with command and args
        """
        try:
            command = payload.get("command", "")
            args = payload.get("args", [])
            
            self.logger.debug(f"Handling debug command: {command} with args: {args}")
            
            # Route to the appropriate handler based on command
            response = None
            
            # The first arg is the debug subcommand (level, trace, performance)
            if command == "debug" and len(args) >= 1:
                subcommand = args[0].lower()
                subcommand_args = args[1:]
                
                if subcommand == "level":
                    response = await self.handle_debug_level_command(subcommand_args)
                elif subcommand == "trace":
                    response = await self.handle_debug_trace_command(subcommand_args)
                elif subcommand == "performance":
                    response = await self.handle_debug_performance_command(subcommand_args)
                else:
                    response = {"message": f"Unknown debug subcommand: {subcommand}"}
            else:
                response = {"message": "Invalid debug command format"}
                
            # Send response if any
            if response:
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    response
                )
                
        except Exception as e:
            self.logger.error(f"Error handling debug command: {str(e)}")
            await self.emit(
                EventTopics.CLI_RESPONSE,
                {"message": f"Error: {str(e)}", "is_error": True}
            )
    
    async def handle_debug_level_command(self, args: List[str]) -> Dict[str, Any]:
        """Handle debug level command."""
        if len(args) != 2:
            return {"message": "Usage: debug level <component|all> <level> (DEBUG/INFO/WARNING/ERROR)"}
        
        component, level = args
        try:
            # Convert level to uppercase and validate it's a valid LogLevel
            level_upper = level.upper()
            if not hasattr(LogLevel, level_upper):
                return {"message": f"Invalid log level: {level}. Use DEBUG/INFO/WARNING/ERROR"}
            
            # Get the log level enum value
            new_level = LogLevel[level_upper]
            
            # Special handling for "all" component
            if component.lower() == "all":
                old_level = self._default_log_level
                self._default_log_level = new_level
                
                # Clear component-specific levels to ensure all use the default
                self._component_log_levels.clear()
                
                self.logger.debug(
                    f"Changed default log level from {old_level} (value: {old_level.value}) "
                    f"to {new_level} (value: {new_level.value})"
                )
                
                return {"message": f"Successfully set ALL components log level to {level_upper}"}
            else:
                # Set the component log level
                old_level = self._component_log_levels.get(component, self._default_log_level)
                self._component_log_levels[component] = new_level
                
                self.logger.debug(
                    f"Changed log level for {component} from {old_level} (value: {old_level.value}) "
                    f"to {new_level} (value: {new_level.value})"
                )
                
                return {"message": f"Successfully set {component} log level to {level_upper}"}
        except Exception as e:
            return {"message": f"Error setting log level: {str(e)}"}
    
    async def handle_debug_trace_command(self, args: List[str]) -> Dict[str, Any]:
        """Handle debug trace command."""
        if not args:
            return {"message": "Usage: debug trace <enable|disable>"}
        
        action = args[0].lower()
        if action == "enable":
            self._trace_enabled = True
            return {"message": "Command tracing enabled"}
        elif action == "disable":
            self._trace_enabled = False
            return {"message": "Command tracing disabled"}
        else:
            return {"message": "Invalid action. Use 'enable' or 'disable'"}
    
    async def handle_debug_performance_command(self, args: List[str]) -> Dict[str, Any]:
        """Handle debug performance command."""
        if not args:
            return {"message": "Usage: debug performance <enable|disable|show>"}
        
        action = args[0].lower()
        if action == "enable":
            self._metrics_enabled = True
            return {"message": "Performance metrics enabled"}
        elif action == "disable":
            self._metrics_enabled = False
            return {"message": "Performance metrics disabled"}
        elif action == "show":
            return {"message": str(self._metrics)}
        else:
            return {"message": "Invalid action. Use 'enable', 'disable', or 'show'"}
    
    async def _stop(self) -> None:
        """Stop the debug service."""
        # Cancel log processing task
        if self._log_task:
            self._log_task.cancel()
            try:
                await self._log_task
            except asyncio.CancelledError:
                pass
        
        # Process remaining logs
        while not self._log_queue.empty():
            try:
                log_entry = self._log_queue.get_nowait()
                self._log_queue.task_done()
            except asyncio.QueueEmpty:
                break
        
        # Clear metrics
        self._metrics.clear()
        
        await super()._stop()
        self.logger.info("DebugService stopped")

    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """Handle LLM response events and print them to the console.
        
        Args:
            payload: The LLM response payload
        """
        try:
            self.logger.debug(f"Received LLM_RESPONSE event with payload type: {type(payload).__name__}")
            
            # Handle both Pydantic models and dictionaries
            if hasattr(payload, 'model_dump'):
                self.logger.debug("Converting Pydantic model to dict using model_dump()")
                payload_dict = payload.model_dump()
            elif hasattr(payload, 'dict'):
                self.logger.debug("Converting Pydantic model to dict using dict()")
                payload_dict = payload.dict()
            else:
                self.logger.debug("Payload is already a dictionary or other type")
                payload_dict = payload
                
            # Now work with the dictionary version
            if not payload_dict:
                self.logger.warning("Received empty payload in LLM_RESPONSE event")
                return
                
            if "text" not in payload_dict:
                self.logger.warning(f"Missing 'text' field in payload: {str(payload_dict)[:200]}...")
                # Try to extract known field patterns
                if isinstance(payload_dict, dict) and "content" in payload_dict:
                    self.logger.debug("Found 'content' field instead of 'text', using that")
                    text = payload_dict.get("content", "")
                else:
                    self.logger.warning("No recognized text field found in payload")
                    return
            else:
                text = payload_dict.get("text", "")
                
            conversation_id = payload_dict.get("conversation_id", "Unknown")
            is_complete = payload_dict.get("is_complete", True)
            
            # Print all payload keys for debugging
            self.logger.debug(f"Payload keys: {list(payload_dict.keys())}")
            
            # Initialize tracking set if it doesn't exist
            if not hasattr(self, '_seen_conversation_ids'):
                self._seen_conversation_ids = set()
            
            # Check if this is a new response or continuation
            is_new_response = conversation_id not in self._seen_conversation_ids
            
            # Only print complete responses or the first chunk of a streaming response
            if is_complete or is_new_response:
                if is_complete:
                    # For complete responses, clear the ID from seen set
                    self._seen_conversation_ids.discard(conversation_id)
                    preface = "COMPLETE LLM RESPONSE"
                else:
                    # For streaming chunks, add the ID to seen set
                    self._seen_conversation_ids.add(conversation_id)
                    preface = "STREAMING LLM RESPONSE (first chunk)"
                
                # Truncate conversation ID for display
                conv_id_short = conversation_id[:8] if conversation_id and isinstance(conversation_id, str) else "Unknown"
                
                # Print the response with clear formatting directly to stdout
                # We use print instead of logger to ensure this appears regardless of log level
                print("\n" + "="*80)
                print(f"🤖 {preface} (conv_id: {conv_id_short}...):")
                print("-"*80)
                print(text)
                print("="*80 + "\n")
                
                # Also log at debug level for log files
                self.logger.debug(f"LLM Response for conversation {conv_id_short}: {text[:100]}...")
                
        except Exception as e:
            self.logger.error(f"Error handling LLM response: {str(e)}")
            import traceback
            self.logger.error(f"Stack trace: {traceback.format_exc()}")
            # Do not let exceptions in this handler affect system operation 

    async def _handle_voice_listening_stopped(self, payload: Dict[str, Any]) -> None:
        """Debug the full transcript sent from the mouse click.
        
        Args:
            payload: The payload containing the full transcript
        """
        try:
            transcript = payload.get("transcript", "")
            if transcript:
                self.logger.info(f"===== MOUSE CLICK FULL TRANSCRIPT =====")
                self.logger.info(f"{transcript}")
                self.logger.info(f"=======================================")
            else:
                self.logger.info("Mouse click received with no transcript")
        except Exception as e:
            self.logger.error(f"Error handling voice_listening_stopped event: {str(e)}")
            
    async def _handle_transcription(self, payload: Dict[str, Any]) -> None:
        """Debug transcription events to track what's being captured.
        
        Args:
            payload: The transcription payload
        """
        try:
            is_final = False
            text = ""
            
            if isinstance(payload, dict):
                is_final = payload.get("is_final", False)
                text = payload.get("text", "")
            elif hasattr(payload, "is_final") and hasattr(payload, "text"):
                is_final = payload.is_final
                text = payload.text
                
            event_type = "FINAL" if is_final else "INTERIM"
            
            if text:
                self.logger.debug(f"TRANSCRIPT [{event_type}]: {text}")
        except Exception as e:
            self.logger.error(f"Error handling transcription event: {str(e)}") 