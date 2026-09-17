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
from ..core.fast_router_gate import GATE
from ..core.track_request import naming_phrase
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
        #: Set by MUSIC_PLAYBACK_STARTED, cleared before each play dispatch. This is how the
        #: execution result learns the track that *actually* started, which is rarely the one
        #: that was requested: a generic "play some music" carries no track at all, and
        #: MusicControllerService picks.
        self._playback_started: Optional[asyncio.Event] = None
        self._last_started_track: Optional[str] = None

        #: How long a play dispatch waits for MUSIC_PLAYBACK_STARTED before confirming the
        #: request instead of the result. Kept below ClaudeService's own outcome wait
        #: (FAST_ROUTER_OUTCOME_WAIT_S, 1.2 s) so the amended record lands before Claude reads
        #: it; VLC starts local files in single-digit milliseconds, so this is pure headroom.
        self._playback_wait_s = float(self._config.get("PLAYBACK_CONFIRM_WAIT_S", 0.8))
        
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
        asyncio.create_task(self.subscribe(
            EventTopics.MUSIC_PLAYBACK_STARTED,
            self._handle_music_playback_started
        ))
        self.logger.info("Subscribed to INTENT_DETECTED events")

    async def _handle_music_playback_started(self, payload: Dict[str, Any]) -> None:
        """Record the track MusicControllerService actually started."""
        track = (payload or {}).get("track") or {}
        if not isinstance(track, dict):
            return
        name = track.get("title") or track.get("name")
        if not name:
            return
        self._last_started_track = name
        if self._playback_started is not None:
            self._playback_started.set()
    
    async def _handle_intent(self, payload: Dict[str, Any]) -> None:
        """Handle an intent detection event."""
        # Bound before the try: the except block below reports on them, and an exception
        # raised while unpacking the payload would otherwise hit an unbound local.
        intent_name = ""
        parameters: Dict[str, Any] = {}
        conversation_id = None
        original_text = ""
        tool_call_id = None
        source = None
        try:
            self.logger.debug(f"Received intent payload: {payload}")

            intent_name = payload.get("intent_name", "")
            parameters = payload.get("parameters", {})
            conversation_id = payload.get("conversation_id", None)
            original_text = payload.get("original_text", "")
            # Provenance, set by JevIntentService to "jev_fast_router". It has to survive into
            # INTENT_EXECUTION_RESULT: ClaudeService needs it to know whether the spoken
            # confirmation for this turn is already coming from its own main turn (fast router)
            # or has to be generated here (its own tool call). Dropping it is what produced two
            # spoken replies on 2026-09-17 10:57:24.
            source = payload.get("source")
            
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

                # Tell the fast-router gate what actually happened, so the
                # <action_already_taken> block ClaudeService builds describes the result
                # rather than the request. Only meaningful for a fast-router dispatch; a
                # Claude tool call has no gate record and this is a no-op.
                if source == "jev_fast_router" and original_text and result.get("track"):
                    GATE.resolve_outcome(original_text, {"track": result["track"]})

                # Emit intent execution result for verbal feedback
                # SKIP for analyze_scene - it handles its own response generation
                if intent_name != "analyze_scene":
                    await self._emit_intent_execution_result(
                        intent_name,
                        parameters,
                        result,
                        tool_call_id,
                        conversation_id,
                        original_text,
                        source
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
                    original_text,
                    source
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
                    original_text,
                    source
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
        original_text: Optional[str],
        source: Optional[str] = None
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
            source: Provenance of the intent ("jev_fast_router", or None for a Claude tool call)
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
                conversation_id=conversation_id,
                source=source
            )
            
            # Emit the event
            await self.emit(EventTopics.INTENT_EXECUTION_RESULT, payload)
            self.logger.info(f"Successfully emitted execution result for {intent_name}")
            
        except Exception as e:
            self.logger.error(f"Error emitting intent execution result: {e}")
    
    async def _handle_play_music_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
        """Handle the play_music intent.

        The result this returns is what R3X says out loud, so it must describe what happened,
        not what was asked for. FIXED 2026-09-17: it used to report the *requested* track, and
        with the old alias table that meant announcing "Cantina Band" while "Huttuk Cheeka" was
        playing. It now waits briefly for MUSIC_PLAYBACK_STARTED and reports that.
        """
        try:
            track = (parameters.get("track") or "").strip()

            # Track selection. A generic request resolves to None, which dispatches a bare
            # `play music` and lets MusicControllerService choose - it is the only component
            # that knows the real library.
            selected_track = await self._select_smart_track(track) if track else None
            self.logger.info(f"Track selection: {track!r} → {selected_track!r}")

            # Arm the confirmation latch *before* dispatching, so a playback event that lands
            # in the same event-loop turn cannot be missed.
            self._playback_started = asyncio.Event()
            self._last_started_track = None

            cli_payload = {
                "command": "play",
                "subcommand": "music",
                "args": [selected_track] if selected_track else [],
                "raw_input": f"play music {selected_track}" if selected_track else "play music",
                "conversation_id": conversation_id
            }

            await self.emit(EventTopics.CLI_COMMAND, cli_payload)
            self.logger.info(f"Emitted CLI_COMMAND event for play music: {selected_track or '(any)'}")

            started_track = await self._await_started_track()

            if started_track:
                return {
                    "success": True,
                    "track": started_track,
                    "requested": track or None,
                    "selected": selected_track,
                    "action": "play",
                    "message": f"Now playing: {started_track}"
                }

            # Nothing reported back. Say only what we know to be true.
            self.logger.warning(
                "No MUSIC_PLAYBACK_STARTED within "
                f"{self._playback_wait_s:.2f}s; confirming the request, not a track name"
            )
            return {
                "success": True,
                "track": None,
                "requested": track or None,
                "selected": selected_track,
                "action": "play",
                "message": "Music playback requested"
            }

        except Exception as e:
            self.logger.error(f"Error handling play_music intent: {e}")
            return {
                "success": False,
                "error": f"Failed to play music: {str(e)}"
            }

    async def _await_started_track(self) -> Optional[str]:
        """Wait, briefly, for the track that actually started."""
        latch = self._playback_started
        if latch is None:
            return None
        try:
            await asyncio.wait_for(latch.wait(), timeout=self._playback_wait_s)
        except asyncio.TimeoutError:
            return None
        finally:
            self._playback_started = None
        return self._last_started_track

    async def _select_smart_track(self, track_request: str) -> Optional[str]:
        """Reduce a spoken request to the words that identify a track, or None.

        REWRITTEN 2026-09-17. The old implementation matched against a hard-coded list of
        invented ids (`cantina_band`, `imperial_march`, `droid_march`, `jedi_rocks`) that
        correspond to no file in `audio/music/` - the real cantina track is
        "Cantina Song aka Mad About Mad About Me". Every generic request was therefore forced
        to `cantina_band`, MusicControllerService logged "No matches found for 'cantina_band',
        playing first track", and R3X announced a track nobody was hearing. It also emitted a
        `list music` CLI command as a side effect, which dumped the library to the console
        mid-turn.

        There is exactly one matcher in this system that knows the real library:
        `MusicControllerService._smart_play_track`. This method's only job is to decide whether
        the user named anything at all, and to hand the naming words to that matcher. The
        decision itself lives in `core/track_request.py`, shared with
        `llm.jev_intents.extract_parameters` so the two layers cannot disagree.

        Returns:
            A track number, or the distinguishing words of the request, or None for a generic
            request ("play some music") - which means "controller's choice".
        """
        return naming_phrase(track_request)

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