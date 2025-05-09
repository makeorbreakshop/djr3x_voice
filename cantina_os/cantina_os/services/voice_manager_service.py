import asyncio
import logging
from typing import Dict, Any, Optional

from cantina_os.base_service import BaseService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatusPayload, BaseEventPayload, ServiceStatus

logger = logging.getLogger(__name__)

class VoiceManagerService(BaseService):
    """
    Service that manages voice interaction, including:
    - Speech recognition
    - Natural language processing
    - Voice synthesis
    """
    
    def __init__(self, event_bus, logger_instance=None):
        super().__init__("voice_manager", event_bus, logger_instance)
        self._state = "disabled"  # Initial state
        self._tasks = []
        self._subscriptions = []  # Track subscriptions for cleanup
        self._running = False  # Use _running instead of is_running property

    @property
    def current_state(self):
        """Get the current state of the voice manager."""
        return self._state

    async def _start(self):
        """Start voice manager service and initialize components."""
        logger.info("Starting VoiceManagerService")
        
        try:
            # Initialize resources first
            await self._initialize_resources()
            
            # Subscribe to relevant events - IMPORTANT: properly await these calls
            subscription = await self.event_bus.on(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_changed)
            self._subscriptions.append((EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_changed))
            
            # Set initial service state
            await self._update_state("disabled")
            
            # Only mark as running after all initialization is complete
            self._running = True  # Use _running attribute
            
            # Emit service started event
            await self.emit(
                EventTopics.SERVICE_STATUS_UPDATE,
                ServiceStatusPayload(
                    service_name=self.service_name,
                    status=ServiceStatus.RUNNING
                )
            )
            
            logger.info("VoiceManagerService started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error starting VoiceManagerService: {str(e)}")
            await self._cleanup_resources()
            return False

    async def _initialize_resources(self):
        """Initialize any resources needed by the service."""
        # Initialize resources here if needed
        pass

    async def _stop(self):
        """Stop voice manager service and clean up resources."""
        logger.info("Stopping VoiceManagerService")
        
        # First mark as not running to prevent new operations
        self._running = False  # Use _running attribute
        
        # Cancel all pending tasks with proper error handling
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=0.5)  # Add timeout for task cancellation
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    # Expected during cancellation
                    pass
                except Exception as e:
                    logger.error(f"Error cancelling task: {str(e)}")
        
        # Clear task list
        self._tasks.clear()
        
        # Update state to disabled
        await self._update_state("disabled")
        
        # Remove event subscriptions with proper cleanup
        await self._remove_subscriptions()
        
        # Clean up any other resources
        await self._cleanup_resources()
        
        # Emit service stopped event
        await self.emit(
            EventTopics.SERVICE_STATUS_UPDATE,
            ServiceStatusPayload(
                service_name=self.service_name,
                status=ServiceStatus.STOPPED
            )
        )
        
        # Add a small grace period for event propagation
        await asyncio.sleep(0.1)
        
        logger.info("VoiceManagerService stopped successfully")
        return True

    async def _cleanup_resources(self):
        """Clean up any resources used by the service."""
        # Clean up resources here if needed
        pass

    async def _handle_mode_changed(self, payload: Dict[str, Any]):
        """Handle mode changes to enable/disable voice interaction."""
        if not self._running:  # Use _running attribute
            return
            
        new_mode = payload.get("new_mode")
        logger.info(f"Handling mode change to {new_mode} in VoiceManagerService")
        
        if new_mode == "INTERACTIVE":
            await self._update_state("enabled")
        else:
            await self._update_state("disabled")

    async def _update_state(self, new_state: str):
        """Update the service state and emit state change event."""
        if self._state == new_state:
            return
            
        old_state = self._state
        self._state = new_state
        
        logger.info(f"VoiceManagerService state changed: {old_state} -> {new_state}")
        
        # Emit state change event - using SERVICE_STATE_CHANGED topic
        await self.emit(
            EventTopics.SERVICE_STATE_CHANGED,  # Use SERVICE_STATE_CHANGED topic
            {
                "service_name": self.service_name,
                "state": new_state,
                "previous_state": old_state
            }
        )
        
        # Allow a short grace period for event propagation
        await asyncio.sleep(0.05) 