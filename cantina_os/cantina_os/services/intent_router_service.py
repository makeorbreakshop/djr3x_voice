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
    IntentExecutionResultPayload,
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
    4. Emits intent execution results for verbal feedback
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
            
            # Get the tool call ID if available (from the original OpenAI tool call)
            # This allows us to link execution results back to the original call
            tool_call_id = None
            tool_calls = payload.get("tool_calls", [])
            if tool_calls and len(tool_calls) > 0:
                tool_call_id = tool_calls[0].get("id")
            
            self.logger.info(f"Handling intent: {intent_name} with parameters: {parameters}")
            
            # Route to the appropriate handler
            result = {"success": False, "message": "Intent not handled"}
            if intent_name in self._intent_handlers:
                handler = self._intent_handlers[intent_name]
                self.logger.info(f"Found handler for intent {intent_name}, invoking it now")
                
                # Handlers now return result information
                result = await handler(parameters, conversation_id)
                self.logger.info(f"Handler for intent {intent_name} completed with result: {result}")
                
                # Emit intent execution result for verbal feedback
                await self._emit_intent_execution_result(
                    intent_name, 
                    parameters, 
                    result, 
                    tool_call_id, 
                    conversation_id,
                    original_text
                )
            else:
                self.logger.warning(f"No handler for intent: {intent_name}")
                
                # Emit execution result for unknown intent
                await self._emit_intent_execution_result(
                    intent_name,
                    parameters,
                    {"success": False, "message": f"No handler for intent: {intent_name}"}, 
                    tool_call_id,
                    conversation_id,
                    original_text
                )
        
        except Exception as e:
            self.logger.error(f"Error handling intent: {e}", exc_info=True)
            
            # Emit execution result for error
            try:
                await self._emit_intent_execution_result(
                    intent_name,
                    parameters,
                    {"success": False, "message": f"Error: {str(e)}"}, 
                    tool_call_id,
                    conversation_id,
                    original_text
                )
            except Exception as emit_error:
                self.logger.error(f"Error emitting execution result: {emit_error}")
    
    async def _emit_intent_execution_result(
        self,
        intent_name: str,
        parameters: Dict[str, Any],
        result: Dict[str, Any],
        tool_call_id: Optional[str],
        conversation_id: Optional[str],
        original_text: Optional[str]
    ) -> None:
        """
        Emit an intent execution result event for verbal feedback.
        
        This event is consumed by the GPTService to generate a natural language
        response about the action that was taken.
        
        Args:
            intent_name: Name of the executed intent
            parameters: Parameters used for execution
            result: Result of the execution 
            tool_call_id: Original tool call ID from OpenAI
            conversation_id: Conversation context ID
            original_text: Original text that triggered the intent
        """
        try:
            self.logger.info(f"Emitting execution result for intent: {intent_name}")
            
            # Determine success based on result
            success = result.get("success", True)
            error_message = result.get("error") or result.get("message") if not success else None
            
            # Create the payload
            payload = IntentExecutionResultPayload(
                intent_name=intent_name,
                parameters=parameters,
                result=result,
                success=success,
                error_message=error_message,
                tool_call_id=tool_call_id,
                original_text=original_text,
                conversation_id=conversation_id
            )
            
            # Emit the event
            await self.emit(EventTopics.INTENT_EXECUTION_RESULT, payload)
            self.logger.info(f"Successfully emitted execution result for {intent_name}")
            
        except Exception as e:
            self.logger.error(f"Error emitting intent execution result: {e}")
    
    async def _handle_play_music_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the play_music intent."""
        try:
            track = parameters.get("track", "")
            if not track:
                self.logger.warning("No track specified in play_music intent")
                return {"success": False, "message": "No track specified"}
            
            self.logger.info(f"Play music request received for: {track}")
            
            # Smart track selection
            selected_track = await self._select_smart_track(track)
            
            if not selected_track:
                self.logger.warning(f"Could not find a suitable track matching: {track}")
                return {
                    "success": False, 
                    "message": f"Could not find a suitable track matching: {track}"
                }
                
            self.logger.info(f"Smart track selection: '{track}' → '{selected_track}'")
            
            # Create and emit music command
            music_payload = MusicCommandPayload(
                action="play",
                song_query=selected_track,
                conversation_id=conversation_id
            )
            
            await self.emit(EventTopics.MUSIC_COMMAND, music_payload)
            self.logger.info(f"Emitted MUSIC_COMMAND event with action=play, song={selected_track}")
            
            # Return success result with information about what was played
            return {
                "success": True,
                "track": selected_track,
                "original_request": track,
                "action": "play",
                "message": f"Now playing: {selected_track}"
            }
        
        except Exception as e:
            self.logger.error(f"Error handling play_music intent: {e}")
            return {
                "success": False,
                "error": f"Failed to play music: {str(e)}"
            }
    
    async def _select_smart_track(self, track_request: str) -> Optional[str]:
        """
        Smart track selection based on the user's request.
        
        This function takes a natural language request like "cantina music" or "some jazz"
        and attempts to find a suitable track in the music library.
        
        Args:
            track_request: The user's track request
            
        Returns:
            A valid track number or name, or None if no match found
        """
        try:
            # Get available tracks by sending a command to music_controller
            tracks_payload = {"command": "list", "subcommand": None, "args": [], "raw_input": "list music"}
            await self.emit(EventTopics.MUSIC_COMMAND, tracks_payload)
            
            # TODO: Ideally we would get the track list directly, but for now we'll use some defaults
            # For testing we'll simulate some available tracks
            available_tracks = [
                "1", "2", "3",  # Track numbers
                "cantina_band", "droid_march", "imperial_march", "jedi_rocks",  # Track names that might exist
            ]
            
            # Check if the request is a valid track number
            if track_request.isdigit() and track_request in available_tracks:
                return track_request
                
            # Check for genre/theme words in the request
            request_lower = track_request.lower()
            
            # Keywords mapping to specific tracks
            keyword_mapping = {
                "cantina": "cantina_band",
                "imperial": "imperial_march",
                "march": "imperial_march",
                "droid": "droid_march",
                "jedi": "jedi_rocks",
                "rock": "jedi_rocks"
            }
            
            # Check if any keywords match the request
            for keyword, suggested_track in keyword_mapping.items():
                if keyword in request_lower and suggested_track in available_tracks:
                    return suggested_track
            
            # If it's a generic request, pick a default or random track
            if any(word in request_lower for word in ["music", "song", "track", "anything", "some"]):
                # Let's default to cantina music for generic requests
                if "cantina_band" in available_tracks:
                    return "cantina_band"
                # Or pick the first available track
                if available_tracks:
                    return available_tracks[0]
            
            # Fall back to using the original request
            # This allows requests like "track 1" to work
            return track_request
            
        except Exception as e:
            self.logger.error(f"Error in smart track selection: {e}")
            # Fall back to the original request if something goes wrong
            return track_request
    
    async def _handle_stop_music_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
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
            
            # Return success result
            return {
                "success": True,
                "action": "stop",
                "message": "Music stopped"
            }
        
        except Exception as e:
            self.logger.error(f"Error handling stop_music intent: {e}")
            return {
                "success": False,
                "error": f"Failed to stop music: {str(e)}"
            }
    
    async def _handle_set_eye_color_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the set_eye_color intent."""
        try:
            color = parameters.get("color", "")
            pattern = parameters.get("pattern", "solid")
            intensity = parameters.get("intensity", 1.0)
            
            if not color:
                self.logger.warning("No color specified in set_eye_color intent")
                return {
                    "success": False,
                    "message": "No color specified"
                }
            
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
            
            # Return success result
            return {
                "success": True,
                "color": color,
                "pattern": pattern,
                "intensity": intensity,
                "message": f"Set eyes to {color} with {pattern} pattern"
            }
        
        except Exception as e:
            self.logger.error(f"Error handling set_eye_color intent: {e}")
            return {
                "success": False,
                "error": f"Failed to set eye color: {str(e)}"
            } 