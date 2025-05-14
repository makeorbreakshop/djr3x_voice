"""
Intent Router Service

This service routes intents detected by the GPT service to appropriate hardware commands.
It acts as a translation layer between natural language intents and specific command formats.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    IntentPayload,
    MusicCommandPayload,
    EyeCommandPayload,
    ServiceStatus
)

class IntentRouterService(BaseService):
    """
    Service for routing intents to appropriate hardware commands.
    
    This service:
    1. Listens for INTENT_DETECTED events from the GPT service
    2. Transforms intent parameters into command-specific formats
    3. Emits the appropriate command events to hardware services
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the IntentRouterService."""
        super().__init__("intent_router_service", event_bus, logger)
        self._config = config or {}
        self._intent_handlers = {
            "play_music": self._handle_play_music_intent,
            "stop_music": self._handle_stop_music_intent,
            "set_eye_color": self._handle_set_eye_color_intent
        }
        
    async def _start(self) -> None:
        """Start the service."""
        try:
            self.logger.info("Starting IntentRouterService")
            await self._setup_subscriptions()
            await self._emit_status(ServiceStatus.RUNNING, "IntentRouterService started")
            self.logger.info("IntentRouterService started successfully")
        except Exception as e:
            error_msg = f"Failed to start IntentRouterService: {e}"
            self.logger.error(error_msg)
            await self._emit_status(ServiceStatus.ERROR, error_msg)
            raise
    
    async def _stop(self) -> None:
        """Stop the service."""
        self.logger.info("Stopping IntentRouterService")
        await self._emit_status(ServiceStatus.STOPPED, "IntentRouterService stopped")
    
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        asyncio.create_task(self.subscribe(
            EventTopics.INTENT_DETECTED,
            self._handle_intent
        ))
        self.logger.info("Subscribed to INTENT_DETECTED events")
    
    async def _handle_intent(self, payload: Dict[str, Any]) -> None:
        """Handle an intent detection event."""
        try:
            self.logger.debug(f"Received intent payload: {payload}")
            
            intent_name = payload.get("intent_name", "")
            parameters = payload.get("parameters", {})
            conversation_id = payload.get("conversation_id", None)
            original_text = payload.get("original_text", "")
            
            self.logger.info(f"Handling intent: {intent_name} with parameters: {parameters}")
            
            # Route to the appropriate handler
            if intent_name in self._intent_handlers:
                handler = self._intent_handlers[intent_name]
                self.logger.info(f"Found handler for intent {intent_name}, invoking it now")
                await handler(parameters, conversation_id)
                self.logger.info(f"Handler for intent {intent_name} completed successfully")
            else:
                self.logger.warning(f"No handler for intent: {intent_name}")
        
        except Exception as e:
            self.logger.error(f"Error handling intent: {e}", exc_info=True)
    
    async def _handle_play_music_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> None:
        """Handle the play_music intent."""
        try:
            track = parameters.get("track", "")
            if not track:
                self.logger.warning("No track specified in play_music intent")
                return
            
            self.logger.info(f"Playing music track: {track}")
            
            # Create and emit music command
            music_payload = MusicCommandPayload(
                action="play",
                song_query=track,
                conversation_id=conversation_id
            )
            
            await self.emit(EventTopics.MUSIC_COMMAND, music_payload)
            self.logger.info(f"Emitted MUSIC_COMMAND event with action=play, song={track}")
        
        except Exception as e:
            self.logger.error(f"Error handling play_music intent: {e}")
    
    async def _handle_stop_music_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> None:
        """Handle the stop_music intent."""
        try:
            self.logger.info("Stopping music")
            
            # Create and emit music command
            music_payload = MusicCommandPayload(
                action="stop",
                conversation_id=conversation_id
            )
            
            await self.emit(EventTopics.MUSIC_COMMAND, music_payload)
            self.logger.info("Emitted MUSIC_COMMAND event with action=stop")
        
        except Exception as e:
            self.logger.error(f"Error handling stop_music intent: {e}")
    
    async def _handle_set_eye_color_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> None:
        """Handle the set_eye_color intent."""
        try:
            color = parameters.get("color", "")
            pattern = parameters.get("pattern", "solid")
            intensity = parameters.get("intensity", 1.0)
            
            if not color:
                self.logger.warning("No color specified in set_eye_color intent")
                return
            
            self.logger.info(f"Setting eye color to {color} with pattern {pattern}")
            
            # Create and emit eye command
            eye_payload = EyeCommandPayload(
                pattern=pattern,
                color=color,
                intensity=intensity,
                conversation_id=conversation_id
            )
            
            await self.emit(EventTopics.EYE_COMMAND, eye_payload)
            self.logger.info(f"Emitted EYE_COMMAND event with color={color}, pattern={pattern}")
        
        except Exception as e:
            self.logger.error(f"Error handling set_eye_color intent: {e}") 