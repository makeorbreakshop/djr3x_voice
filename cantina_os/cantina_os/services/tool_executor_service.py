"""Service for executing LLM-requested tools safely and returning results."""
import asyncio
from typing import Any, Dict, Optional, Callable
from pydantic import BaseModel

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import (
    ServiceStatus,
    ServiceStatusPayload,
    ToolExecutionRequestPayload,
    ToolExecutionResultPayload,
    ToolRegistrationPayload,
)

class ToolExecutorService(BaseService):
    """Service for executing LLM-requested tools safely and returning results."""
    
    def __init__(self, event_bus):
        super().__init__("ToolExecutorService", event_bus)
        self.registered_tools: Dict[str, Callable] = {}
        self.execution_timeout = 30  # seconds
        
    async def _start(self) -> None:
        """Start the service and subscribe to tool-related events."""
        try:
            await super()._start()
            
            # Subscribe to tool execution requests and registration events
            await self.subscribe(
                EventTopics.TOOL_REGISTRATION_REQUEST,
                self._handle_tool_registration
            )
            await self.subscribe(
                EventTopics.TOOL_CALL_REQUEST,
                self._handle_tool_execution_request
            )
            
            self._started = True
            self._status = ServiceStatus.RUNNING
            await self._emit_status(ServiceStatus.RUNNING, "Tool executor service started")
            
        except Exception as e:
            self.logger.error(f"Error starting service: {e}")
            await self._emit_status(ServiceStatus.ERROR, f"Failed to start: {str(e)}")
            raise
        
    async def _stop(self) -> None:
        """Stop the service and clean up resources."""
        try:
            self.registered_tools.clear()
            await self._emit_status(ServiceStatus.STOPPING, "Tool executor service stopping")
        finally:
            await super()._stop()
        
    async def _handle_tool_registration(self, payload: ToolRegistrationPayload) -> None:
        """Handle registration of new tools."""
        try:
            tool_name = payload.tool_name
            tool_func = payload.tool_function
            
            # Validate tool function is callable
            if not callable(tool_func):
                error_msg = "Tool function must be callable"
                await self._emit_status(ServiceStatus.ERROR, f"Failed to register tool: {error_msg}")
                return
                
            self.registered_tools[tool_name] = tool_func
            await self._emit_status(
                ServiceStatus.RUNNING,
                f"Registered tool: {tool_name}"
            )
            
        except Exception as e:
            error_msg = str(e)
            if "callable" in error_msg.lower():
                error_msg = "Tool function must be callable"
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Failed to register tool: {error_msg}"
            )
            
    async def _handle_tool_execution_request(
        self,
        payload: ToolExecutionRequestPayload
    ) -> None:
        """Handle requests to execute registered tools."""
        try:
            tool_name = payload.tool_name
            tool_args = payload.arguments
            request_id = payload.request_id
            
            if tool_name not in self.registered_tools:
                raise ValueError(f"Tool {tool_name} not registered")
                
            # Execute tool with timeout protection
            result = await asyncio.wait_for(
                self._execute_tool(tool_name, tool_args),
                timeout=self.execution_timeout
            )
            
            # Emit successful result
            await self.emit(
                EventTopics.TOOL_CALL_RESULT,
                ToolExecutionResultPayload(
                    request_id=request_id,
                    tool_name=tool_name,
                    success=True,
                    result=result,
                    error=None
                )
            )
            
        except asyncio.TimeoutError:
            await self._emit_tool_error(
                request_id,
                tool_name,
                "Tool execution timed out"
            )
        except Exception as e:
            await self._emit_tool_error(
                request_id,
                tool_name,
                f"Tool execution failed: {str(e)}"
            )
            
    async def _execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        """Execute a tool in an isolated manner."""
        tool_func = self.registered_tools[tool_name]
        
        try:
            # Execute tool (handle both async and sync functions)
            if asyncio.iscoroutinefunction(tool_func):
                result = await tool_func(**arguments)
            else:
                result = await asyncio.to_thread(
                    tool_func,
                    **arguments
                )
            return result
            
        except Exception as e:
            raise RuntimeError(f"Tool execution failed: {str(e)}")
            
    async def _emit_tool_error(
        self,
        request_id: str,
        tool_name: str,
        error_message: str
    ) -> None:
        """Emit a tool execution error event."""
        try:
            await self.emit(
                EventTopics.TOOL_CALL_ERROR,
                ToolExecutionResultPayload(
                    request_id=request_id,
                    tool_name=tool_name,
                    success=False,
                    result=None,
                    error=error_message
                )
            )
            
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Tool execution failed - {tool_name}: {error_message}"
            )
        except Exception as e:
            # Ensure service status is updated even if event emission fails
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Failed to emit tool error - {tool_name}: {error_message}"
            ) 