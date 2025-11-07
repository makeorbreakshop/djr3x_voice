"""
Yoda Mode Manager Service

This service manages system operation modes and mode transitions with proper
event synchronization and error handling.
"""

import logging
import asyncio
from typing import Dict, Optional, Any
from enum import Enum

from ..bus.sync_event_bus import SyncEventBus
from ..bus.transaction_context import TransactionContext
from ..base_service import BaseService
from ..models.service_status import ServiceStatus
from ..event_topics import EventTopics
from ..models.payloads import SystemModeChangePayload, ModeTransitionPayload

class SystemMode(Enum):
    """System operation modes."""
    STARTUP = "STARTUP"
    IDLE = "IDLE"
    AMBIENT = "AMBIENT"
    INTERACTIVE = "INTERACTIVE"

class YodaModeManagerService(BaseService):
    """
    Service that manages system operation modes and mode transitions.
    
    Features:
    1. Mode state management
    2. Synchronized mode transitions
    3. Mode change event propagation
    4. Error handling and recovery
    """
    
    def __init__(self, event_bus: SyncEventBus):
        """Initialize the mode manager service."""
        super().__init__("yoda_mode_manager", event_bus)
        self._current_mode = SystemMode.STARTUP
        self._transition_lock = asyncio.Lock()
        self._mode_change_grace_period_ms = 200  # Standardized to 200ms
        
    async def _start(self) -> None:
        """Set up mode management handlers."""
        try:
            # Set up handlers first
            self.logger.debug("Subscribing to mode request events")
            await self.subscribe(EventTopics.SYSTEM_SET_MODE_REQUEST, self._handle_mode_request)
            
            # Add a grace period to ensure subscriptions are ready
            self.logger.debug(f"Waiting for subscription grace period: {self._mode_change_grace_period_ms}ms")
            await asyncio.sleep(self._mode_change_grace_period_ms / 1000)
            
            # Emit initial status
            self.logger.debug("Emitting initial status: STARTING")
            await self._emit_status(
                ServiceStatus.STARTING,
                "Initializing mode manager service"
            )
            
            # Verify event bus subscriptions before continuing
            self.logger.debug("Verifying event bus subscriptions")
            await self.event_bus.verify_subscriptions()
            
            # Set initial mode
            self.logger.debug("Setting initial mode to IDLE")
            await self.set_mode(SystemMode.IDLE)
            
            # Final status update
            self.logger.debug("Emitting final status: RUNNING")
            await self._emit_status(
                ServiceStatus.RUNNING,
                "Mode manager service initialized"
            )
            
        except Exception as e:
            error_msg = f"Error during startup: {e}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise
        
    async def set_mode(self, new_mode: SystemMode) -> None:
        """Set the system mode with proper event sequence.
        
        Args:
            new_mode: The new mode to transition to
            
        Raises:
            ValueError: If the mode is invalid
            RuntimeError: If the mode transition fails
        """
        try:
            # Handle empty mode values during subscription verification
            if not new_mode or (isinstance(new_mode, str) and not new_mode.strip()):
                self.logger.debug("Skipping empty mode value")
                return
                
            # Validate and convert mode
            if not isinstance(new_mode, SystemMode):
                try:
                    self.logger.debug(f"Converting mode string '{new_mode}' to SystemMode enum")
                    new_mode = SystemMode(str(new_mode).upper())
                except (ValueError, AttributeError) as e:
                    error_msg = f"Invalid mode: {new_mode}"
                    self.logger.error(error_msg)
                    await self._emit_status(ServiceStatus.ERROR, error_msg)
                    raise ValueError(error_msg) from e
                    
            if new_mode == self._current_mode:
                self.logger.debug(f"Already in {new_mode.name} mode")
                return
                
            async with self._transition_lock:
                old_mode = self._current_mode
                self.logger.debug(f"Starting mode transition: {old_mode.name} -> {new_mode.name}")
                
                # Initial status update for transition
                await self._emit_status(
                    ServiceStatus.STARTING,
                    f"Starting transition from {old_mode.name} to {new_mode.name}"
                )
                
                # Use transaction context for atomic event sequence
                try:
                    # Create transaction context
                    self.logger.debug("Starting mode transition transaction")
                    async with TransactionContext(self.event_bus, logger=self.logger) as transaction:
                        # Start transition event
                        self.logger.debug(f"Emitting {EventTopics.MODE_TRANSITION_STARTED} event")
                        await transaction.emit(
                            EventTopics.MODE_TRANSITION_STARTED,
                            ModeTransitionPayload(
                                old_mode=old_mode.name,
                                new_mode=new_mode.name,
                                status="started"
                            )
                        )
                        
                        # Update mode
                        self.logger.debug(f"Updating current mode to {new_mode.name}")
                        self._current_mode = new_mode
                        
                        # Complete transition event
                        self.logger.debug(f"Emitting {EventTopics.MODE_TRANSITION_COMPLETE} event")
                        await transaction.emit(
                            EventTopics.MODE_TRANSITION_COMPLETE,
                            ModeTransitionPayload(
                                old_mode=old_mode.name,
                                new_mode=new_mode.name,
                                status="completed"
                            )
                        )
                        
                        # Mode change notification
                        self.logger.debug(f"Emitting {EventTopics.SYSTEM_MODE_CHANGE} event")
                        await transaction.emit(
                            EventTopics.SYSTEM_MODE_CHANGE,
                            SystemModeChangePayload(
                                old_mode=old_mode.name,
                                new_mode=new_mode.name
                            )
                        )
                    
                    # Transaction committed successfully
                    self.logger.debug(f"Mode transition transaction completed successfully")
                    
                    # Final status update
                    self.logger.debug("Emitting final status: RUNNING")
                    await self._emit_status(
                        ServiceStatus.RUNNING,
                        f"Successfully transitioned to {new_mode.name} mode"
                    )
                    
                except Exception as e:
                    # Transaction rolled back or failed
                    error_msg = f"Mode transition failed: {str(e)}"
                    self.logger.error(error_msg)
                    
                    # Revert on failure (transaction already rolled back)
                    self.logger.debug(f"Reverting to previous mode: {old_mode.name}")
                    self._current_mode = old_mode
                    
                    # Emit status error
                    self.logger.debug("Emitting error status")
                    await self._emit_status(
                        ServiceStatus.ERROR,
                        error_msg
                    )
                    
                    # Emit failure event
                    self.logger.debug(f"Emitting {EventTopics.MODE_TRANSITION_FAILED} event")
                    await self.event_bus.emit(
                        EventTopics.MODE_TRANSITION_FAILED,
                        ModeTransitionPayload(
                            old_mode=old_mode.name,
                            new_mode=new_mode.name,
                            status="failed",
                            error=str(e)
                        ).model_dump()
                    )
                    
                    raise RuntimeError(error_msg) from e
                    
        except Exception as e:
            self.logger.error(f"Error setting mode: {e}")
            raise
            
    async def _handle_mode_request(self, payload: Dict[str, Any]) -> None:
        """Handle mode change requests."""
        try:
            requested_mode = payload.get("mode")
            if not requested_mode:
                self.logger.warning("No mode specified in request")
                return
                
            await self.set_mode(requested_mode)
            
        except Exception as e:
            self.logger.error(f"Error handling mode request: {e}")
            raise
            
    @property
    def current_mode(self) -> SystemMode:
        """Get the current system mode."""
        return self._current_mode 