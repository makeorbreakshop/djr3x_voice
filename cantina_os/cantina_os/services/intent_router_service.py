"""
Intent Router Service

This service routes intents detected by the GPT service to appropriate hardware commands.
It acts as a translation layer between natural language intents and specific command formats.
"""

"""
SERVICE: IntentRouterService
PURPOSE: Routes intents detected by GPT service to appropriate hardware commands, translating natural language into specific command formats
EVENTS_IN: INTENT_DETECTED
EVENTS_OUT: INTENT_EXECUTION_RESULT, CLI_COMMAND
KEY_METHODS: _handle_intent, _handle_play_music_intent, _handle_stop_music_intent, _handle_set_eye_color_intent, _select_smart_track
DEPENDENCIES: Command dispatcher service integration, music library access
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import (
    IntentPayload,
    IntentExecutionResultPayload,
    MusicCommandPayload,
    EyeCommandPayload,
    VisionAnalysisRequestPayload,
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
            "set_eye_color": self._handle_set_eye_color_intent,
            "analyze_scene": self._handle_analyze_scene_intent,
            # Intents reachable only from the Jev fast router. Claude has no tool schema for
            # these (see the audit: "Claude literally cannot start DJ mode"), but the CLI
            # commands they dispatch have existed all along.
            "next_track": self._handle_next_track_intent,
            "dj_mode_on": self._handle_dj_mode_on_intent,
            "dj_mode_off": self._handle_dj_mode_off_intent,
            # Alias: the fast router's name for the same action as set_eye_color.
            "set_eye_animation": self._handle_set_eye_color_intent,
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
        # Bound before the try: the except block below reports on them, and an exception
        # raised while unpacking the payload would otherwise hit an unbound local.
        intent_name = ""
        parameters: Dict[str, Any] = {}
        conversation_id = None
        original_text = ""
        tool_call_id = None
        try:
            self.logger.debug(f"Received intent payload: {payload}")

            intent_name = payload.get("intent_name", "")
            parameters = payload.get("parameters", {})
            conversation_id = payload.get("conversation_id", None)
            original_text = payload.get("original_text", "")
            
            # Get the tool call ID if available (from the original OpenAI tool call)
            # This allows us to link execution results back to the original call
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
                # SKIP for analyze_scene - it handles its own response generation
                if intent_name != "analyze_scene":
                    await self._emit_intent_execution_result(
                        intent_name,
                        parameters,
                        result,
                        tool_call_id,
                        conversation_id,
                        original_text
                    )
                else:
                    self.logger.info(f"Skipping INTENT_EXECUTION_RESULT for {intent_name} (handles own response)")
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
            
            # Create and emit music command via CLI_COMMAND for unified processing
            cli_payload = {
                "command": "play",
                "subcommand": "music", 
                "args": [selected_track],
                "raw_input": f"play music {selected_track}",
                "conversation_id": conversation_id
            }
            
            await self.emit(EventTopics.CLI_COMMAND, cli_payload)
            self.logger.info(f"Emitted CLI_COMMAND event for play music: {selected_track}")
            
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
            # Get available tracks by sending a command to music_controller via unified flow
            tracks_payload = {
                "command": "list", 
                "subcommand": "music", 
                "args": [], 
                "raw_input": "list music"
            }
            await self.emit(EventTopics.CLI_COMMAND, tracks_payload)
            
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
            
            # Create and emit music command via CLI_COMMAND for unified processing
            cli_payload = {
                "command": "stop",
                "subcommand": "music",
                "args": [],
                "raw_input": "stop music",
                "conversation_id": conversation_id
            }
            
            await self.emit(EventTopics.CLI_COMMAND, cli_payload)
            self.logger.info("Emitted CLI_COMMAND event for stop music")
            
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
    
    async def _emit_dj_cli_command(
        self,
        subcommand: str,
        conversation_id: Optional[str]
    ) -> None:
        """Emit a `dj <subcommand>` CLI_COMMAND, which the dispatcher routes to BrainService."""
        await self.emit(EventTopics.CLI_COMMAND, {
            "command": "dj",
            "subcommand": subcommand,
            "args": [],
            "raw_input": f"dj {subcommand}",
            "conversation_id": conversation_id
        })
        self.logger.info(f"Emitted CLI_COMMAND event for dj {subcommand}")

    async def _handle_next_track_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the next_track intent by advancing the DJ-mode queue.

        KNOWN LIMITATION, measured live 2026-09-17: outside DJ mode this does nothing useful.
        `dj next` is the only skip capability in the system, and BrainService refuses it with
        "Cannot skip track, DJ mode is not active". MusicControllerService itself understands
        only two actions - "play" and "stop" (music_controller_service.py:574,577) - so there
        is no track cursor to advance when DJ mode is off.

        Deliberately not papered over here: faking a skip by re-issuing `music play` would pick
        a track by the same path a fresh "play some music" does, which is not what "next" means
        and would hide the gap. The fix belongs in MusicControllerService (a real playlist
        cursor with a "next" action), not in the router. The user does at least get an accurate
        error today rather than silence.
        """
        try:
            await self._emit_dj_cli_command("next", conversation_id)
            return {
                "success": True,
                "action": "next_track",
                "message": "Skipping to the next track"
            }
        except Exception as e:
            self.logger.error(f"Error handling next_track intent: {e}")
            return {"success": False, "error": f"Failed to skip track: {str(e)}"}

    async def _handle_dj_mode_on_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the dj_mode_on intent."""
        try:
            await self._emit_dj_cli_command("start", conversation_id)
            return {
                "success": True,
                "action": "dj_mode_on",
                "message": "DJ mode starting"
            }
        except Exception as e:
            self.logger.error(f"Error handling dj_mode_on intent: {e}")
            return {"success": False, "error": f"Failed to start DJ mode: {str(e)}"}

    async def _handle_dj_mode_off_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the dj_mode_off intent."""
        try:
            await self._emit_dj_cli_command("stop", conversation_id)
            return {
                "success": True,
                "action": "dj_mode_off",
                "message": "DJ mode stopping"
            }
        except Exception as e:
            self.logger.error(f"Error handling dj_mode_off intent: {e}")
            return {"success": False, "error": f"Failed to stop DJ mode: {str(e)}"}

    async def _handle_set_eye_color_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the set_eye_color intent."""
        try:
            color = parameters.get("color", "")
            # FIXED 2026-09-17: the default used to be "solid", which is not a member of
            # EyePattern (idle/startup/engaged/listening/thinking/speaking/flash/happy/sad/
            # angry/surprised/error/custom - eye_light_controller_service.py:48). The eye
            # service's `EyePattern(pattern_name)` therefore raised ValueError and logged
            # "Invalid eye pattern: solid" for every colour request. CUSTOM is the member
            # documented at :62 as being "For custom patterns with specific colors".
            pattern = parameters.get("pattern", "custom")
            intensity = parameters.get("intensity", 1.0)
            
            if not color:
                self.logger.warning("No color specified in set_eye_color intent")
                return {
                    "success": False,
                    "message": "No color specified"
                }
            
            self.logger.info(f"Setting eye color to {color} with pattern {pattern}")
            
            # FIXED 2026-09-17: emit EYE_COMMAND directly instead of laundering this through
            # CLI_COMMAND.
            #
            # The old route built `eye pattern <pattern> <color>` - two args - but the
            # compound command "eye pattern" is registered with max_args=1 (main.py:349), so
            # command_decorators.py:368 rejected every single colour request with
            # "Command 'eye pattern' accepts at most 1 arguments, got 2". Observed live in a
            # full-system run on 2026-09-17: "make your eyes red" dispatched in 209 ms and was
            # then thrown away at the CLI arg check.
            #
            # Even had the arg count passed, EyeCliCommandPayload.from_cli_payload
            # (eye_light_controller_service.py:89) only parses `pattern_name` - there is no
            # colour slot in the CLI grammar at all, so the colour was discarded regardless.
            #
            # _handle_eye_command already accepts a plain dict with "pattern"/"color"/
            # "intensity"/"duration" (eye_light_controller_service.py, dict branch) and passes
            # all four straight to set_pattern(), which does take a colour. That is the path
            # that can actually express this intent, so use it.
            eye_payload = {
                "pattern": pattern,
                "color": color,
                "intensity": intensity,
                "conversation_id": conversation_id,
            }

            await self.emit(EventTopics.EYE_COMMAND, eye_payload)
            self.logger.info(f"Emitted EYE_COMMAND for pattern={pattern} color={color}")
            
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

    async def _handle_analyze_scene_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """
        Handle the analyze_scene intent.

        This intent triggers on-demand vision analysis when the user asks
        "what do you see?", "look at this", etc.

        Args:
            parameters: Intent parameters containing the question
            conversation_id: Conversation context ID

        Returns:
            Result dict (fire-and-forget, actual result comes via VISION_SCENE_CAPTURED)
        """
        try:
            question = parameters.get("question", "What do you see?")

            self.logger.info(f"Vision analysis request: '{question}'")

            # Emit vision analysis request event
            payload = VisionAnalysisRequestPayload(
                question=question,
                conversation_id=conversation_id,
                reason="user_requested"
            )

            await self.emit(EventTopics.VISION_ANALYSIS_REQUEST, payload)
            self.logger.info(f"Emitted VISION_ANALYSIS_REQUEST event")

            # Return fire-and-forget result
            # Actual vision result will come via VISION_SCENE_CAPTURED → ClaudeService
            return {
                "success": True,
                "action": "analyze_scene",
                "question": question,
                "message": f"Analyzing scene: {question}"
            }

        except Exception as e:
            self.logger.error(f"Error handling analyze_scene intent: {e}")
            return {
                "success": False,
                "error": f"Failed to analyze scene: {str(e)}"
            } 