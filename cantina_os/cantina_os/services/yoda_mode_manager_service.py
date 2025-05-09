"""
Yoda Mode Manager Service for CantinaOS

This service manages system operation modes and mode transitions.
It has been refactored to focus solely on mode management, removing direct CLI handling.
"""

import logging
import asyncio
from typing import Dict, Optional, Any
from enum import Enum

from pyee.asyncio import AsyncIOEventEmitter

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    ServiceStatus,
    SystemModeChangePayload,
    ModeTransitionPayload,
    LogLevel
)

class SystemMode(Enum):
    """System operation modes."""
    STARTUP = "STARTUP"
    IDLE = "IDLE"
    AMBIENT = "AMBIENT"
    INTERACTIVE = "INTERACTIVE"

class YodaModeManagerService(BaseService):
    """
    Service that manages system operation modes.
    
    Features:
    - Mode state management
    - Mode transition validation
    - Mode change event emission
    - Mode history tracking
    """
    
    def __init__(
        self,
        event_bus: AsyncIOEventEmitter,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the mode manager service.
        
        Args:
            event_bus: Event bus instance
            config: Optional configuration dictionary
            logger: Optional logger instance
        """
        super().__init__("yoda_mode_manager", event_bus, logger)
        
        # Current mode state
        self._current_mode = SystemMode.STARTUP
        
        # Mode transition grace period (ms)
        self._mode_change_grace_period_ms = config.get('MODE_CHANGE_GRACE_PERIOD_MS', 100) if config else 100
        
        # Track event emission timing
        self._last_event_time = 0
        self._event_sequence = []
        
        self.logger.debug("YodaModeManagerService initialized")
        
    async def _start(self) -> None:
        """Initialize the service."""
        self.logger.info("Starting mode manager service")
        start_time = asyncio.get_event_loop().time()
        
        try:
            # Subscribe to mode change requests
            self.logger.debug("Subscribing to mode change requests")
            await self.subscribe(EventTopics.SYSTEM_SET_MODE_REQUEST, self._handle_mode_request)
            self.logger.debug(f"Subscription completed at +{asyncio.get_event_loop().time() - start_time:.3f}s")
            
            # Emit initial status update
            self.logger.debug("Emitting initial status update")
            await self._emit_status(
                ServiceStatus.STARTING,
                "Transitioning to IDLE mode"
            )
            self.logger.debug(f"Status update emitted at +{asyncio.get_event_loop().time() - start_time:.3f}s")
            
            # Add grace period before mode transition
            self.logger.debug("Adding pre-transition grace period")
            await asyncio.sleep(0.1)
            
            # Transition to IDLE mode after startup
            self.logger.debug("Starting transition to IDLE mode")
            await self.set_mode(SystemMode.IDLE)
            self.logger.debug(f"Mode transition completed at +{asyncio.get_event_loop().time() - start_time:.3f}s")
            
            # Verify mode transition completed
            if self._current_mode != SystemMode.IDLE:
                raise RuntimeError("Failed to transition to IDLE mode")
                
            # Add grace period for state propagation
            self.logger.debug("Adding post-transition grace period")
            await asyncio.sleep(self._mode_change_grace_period_ms / 1000)
            
            # Emit final status update
            self.logger.debug("Emitting final status update")
            await self._emit_status(
                ServiceStatus.RUNNING,
                "Service started and in IDLE mode"
            )
            self.logger.debug(f"Startup sequence completed at +{asyncio.get_event_loop().time() - start_time:.3f}s")
            
            # Log event sequence
            self.logger.info("Startup event sequence:")
            for i, (event, timestamp) in enumerate(self._event_sequence):
                self.logger.info(f"  {i+1}. {event} at +{timestamp:.3f}s")
            
        except Exception as e:
            error_msg = f"Error during service startup: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            raise
        
    async def _stop(self) -> None:
        """Clean up resources."""
        self.logger.info("Stopping mode manager service")
        start_time = asyncio.get_event_loop().time()
        
        try:
            # Always transition to IDLE on shutdown
            if self._current_mode != SystemMode.IDLE:
                self.logger.debug("Transitioning to IDLE mode before shutdown")
                await self.set_mode(SystemMode.IDLE)
                self.logger.debug(f"Final mode transition completed at +{asyncio.get_event_loop().time() - start_time:.3f}s")
                
            # Add grace period for state propagation
            self.logger.debug("Adding shutdown grace period")
            await asyncio.sleep(self._mode_change_grace_period_ms / 1000)
            
            # Log final event sequence
            self.logger.info("Shutdown event sequence:")
            for i, (event, timestamp) in enumerate(self._event_sequence[-5:]):
                self.logger.info(f"  {i+1}. {event} at +{timestamp:.3f}s")
            
        except Exception as e:
            error_msg = f"Error during service shutdown: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            raise
            
    async def set_mode(self, new_mode: SystemMode) -> None:
        """Set the system mode.
        
        Args:
            new_mode: The new mode to transition to
        """
        start_time = asyncio.get_event_loop().time()
        self.logger.debug(f"Attempting to set mode to: {new_mode}")
        
        if not isinstance(new_mode, SystemMode):
            try:
                self.logger.debug(f"Converting mode string '{new_mode}' to SystemMode enum")
                new_mode = SystemMode(new_mode.upper())
            except (ValueError, AttributeError) as e:
                error_msg = f"Invalid mode: {new_mode}"
                self.logger.error(error_msg)
                await self._emit_status(
                    ServiceStatus.ERROR,
                    error_msg,
                    severity=LogLevel.ERROR
                )
                # Send CLI error response
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    {
                        "message": f"Error: Invalid mode '{new_mode}'",
                        "is_error": True
                    }
                )
                return
                
        if new_mode == self._current_mode:
            self.logger.debug(f"Already in {new_mode.name} mode")
            # Send CLI notification that we're already in this mode
            await self.emit(
                EventTopics.CLI_RESPONSE,
                {
                    "message": f"Already in {new_mode.name} mode.",
                    "is_error": False
                }
            )
            return
            
        old_mode = self._current_mode
        self.logger.debug(f"Starting mode transition: {old_mode.name} -> {new_mode.name}")
        
        try:
            # Emit mode transition started event
            self.logger.debug("Emitting mode transition started event")
            await self.emit(
                EventTopics.MODE_TRANSITION_STARTED,
                ModeTransitionPayload(
                    old_mode=old_mode.name,
                    new_mode=new_mode.name,
                    status="started"
                )
            )
            self._event_sequence.append(("MODE_TRANSITION_STARTED", asyncio.get_event_loop().time() - start_time))
            
            # Add grace period before state change
            self.logger.debug("Waiting for pre-transition grace period")
            await asyncio.sleep(self._mode_change_grace_period_ms / 1000)
            
            # Update current mode
            self._current_mode = new_mode
            self.logger.debug(f"Updated current mode to {new_mode.name}")
            
            # Add grace period for state updates
            self.logger.debug(f"Waiting for post-transition grace period: {self._mode_change_grace_period_ms}ms")
            await asyncio.sleep(self._mode_change_grace_period_ms / 1000)
            
            # Emit mode change event using BaseService.emit
            self.logger.debug("Emitting mode change event")
            await self.emit(
                EventTopics.SYSTEM_MODE_CHANGE,
                SystemModeChangePayload(
                    old_mode=old_mode.name,
                    new_mode=new_mode.name
                )
            )
            self._event_sequence.append(("SYSTEM_MODE_CHANGE", asyncio.get_event_loop().time() - start_time))
            
            # Emit transition completed event
            self.logger.debug("Emitting mode transition completed event")
            await self.emit(
                EventTopics.MODE_TRANSITION_COMPLETE,
                ModeTransitionPayload(
                    old_mode=old_mode.name,
                    new_mode=new_mode.name,
                    status="completed"
                )
            )
            self._event_sequence.append(("MODE_TRANSITION_COMPLETE", asyncio.get_event_loop().time() - start_time))
            
            self.logger.debug(f"Mode transition sequence completed at +{asyncio.get_event_loop().time() - start_time:.3f}s")
            
            # Remove CLI notification from here - this is the responsibility of ModeCommandHandlerService
            # Let ModeCommandHandlerService handle all user-facing messages
            
        except Exception as e:
            error_msg = f"Error during mode transition: {str(e)}"
            self.logger.error(error_msg)
            # Attempt to revert mode
            self._current_mode = old_mode
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            # Emit transition failed event
            await self.emit(
                EventTopics.MODE_TRANSITION_COMPLETE,
                ModeTransitionPayload(
                    old_mode=old_mode.name,
                    new_mode=new_mode.name,
                    status="failed",
                    error=str(e)
                )
            )
            self._event_sequence.append(("MODE_TRANSITION_FAILED", asyncio.get_event_loop().time() - start_time))
            
            # Don't send CLI error notifications directly
            # Let ModeCommandHandlerService handle this based on mode transition events
            raise
        
    async def _handle_mode_request(self, payload: Dict[str, Any]) -> None:
        """Handle a mode change request.
        
        Args:
            payload: The mode change request payload
        """
        try:
            requested_mode = payload.get("mode", "").upper()
            self.logger.debug(f"Received mode change request: {requested_mode}")
            await self.set_mode(requested_mode)
        except Exception as e:
            error_msg = f"Error handling mode request: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg,
                severity=LogLevel.ERROR
            )
            
    @property
    def current_mode(self) -> SystemMode:
        """Get the current system mode."""
        return self._current_mode 