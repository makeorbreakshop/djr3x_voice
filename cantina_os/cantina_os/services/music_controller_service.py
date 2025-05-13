"""
Music Controller Service for CantinaOS

This service manages music playback with mode-aware behavior and audio ducking during speech.
"""

import os
import asyncio
import logging
from typing import Dict, Optional, List, Any
import vlc
from pydantic import BaseModel
from pyee.asyncio import AsyncIOEventEmitter
import time
import uuid

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    MusicCommandPayload,
    BaseEventPayload,
    ServiceStatusPayload,
    ServiceStatus,
    SystemModePayload,
    LogLevel
)

class MusicTrack(BaseModel):
    """Model representing a music track."""
    name: str
    path: str
    duration: Optional[float] = None

class MusicControllerService(BaseService):
    """
    Service for managing music playback with mode-aware behavior and audio ducking.
    
    Features:
    - Mode-specific playback behavior (IDLE, AMBIENT, INTERACTIVE)
    - Audio ducking during speech
    - CLI command integration
    - Resource cleanup
    """
    
    def __init__(self, event_bus: AsyncIOEventEmitter, music_dir: str = "assets/music"):
        """Initialize the music controller service."""
        super().__init__(service_name="MusicController", event_bus=event_bus)
        self.music_dir = music_dir
        self.tracks: Dict[str, MusicTrack] = {}
        self.current_track: Optional[MusicTrack] = None
        self.player: Optional[vlc.MediaPlayer] = None
        self.current_mode = "IDLE"
        self.normal_volume = 70  # Normal playback volume (0-100)
        self.ducking_volume = 30  # Volume during speech (0-100)
        self.is_ducking = False
        
        # Create VLC instance
        self.vlc_instance = vlc.Instance()
        
        # Subscriptions will be set up during start()
        self._subscriptions = []
        
    async def subscribe_to_events(self):
        """Subscribe to relevant system events."""
        # Add debugging for subscriptions
        self.logger.debug("Setting up music controller event subscriptions")
        
        # Use proper subscription using BaseService.subscribe
        await self.subscribe(EventTopics.MUSIC_COMMAND, self._handle_music_command)
        self.logger.debug("Subscribed to MUSIC_COMMAND events")
        
        await self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_mode_change)
        self.logger.debug("Subscribed to SYSTEM_MODE_CHANGE events")
        
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_start)
        self.logger.debug("Subscribed to SPEECH_SYNTHESIS_STARTED events")
        
        await self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_end)
        self.logger.debug("Subscribed to SPEECH_SYNTHESIS_ENDED events")
        
        self.logger.info("Music controller event subscriptions complete")
        
    async def start(self):
        """Start the music controller service."""
        self.logger.info("Starting music controller service")
        
        # Call the parent start method first
        await super().start()
        
        # Now subscribe to events
        self.logger.debug("Setting up event subscriptions")
        await self.subscribe_to_events()
        
        # Load the music library
        self.logger.debug("Loading music library")
        await self._load_music_library()
        
        # Set service status to running
        self.logger.info(f"Music controller started with {len(self.tracks)} tracks loaded")
        await self._emit_status(ServiceStatus.RUNNING, "Music controller started")
        
    async def stop(self):
        """Stop the music controller service and cleanup resources."""
        try:
            # Stop any current playback
            if self.player:
                self.player.stop()
                self.player.release()
                self.player = None
                self.current_track = None
                
                # Emit playback stopped event
                await self.emit(
                    EventTopics.MUSIC_PLAYBACK_STOPPED,
                    BaseEventPayload(conversation_id=None)
                )
            
            # Remove event subscriptions
            for topic, handler in self._subscriptions:
                try:
                    # Check if event_bus exists and only then try to remove the listener
                    if self.event_bus is not None:
                        try:
                            await self.event_bus.remove_listener(topic, handler)
                        except Exception as e:
                            self.logger.debug(f"Error removing event listener for {topic}: {e}")
                    else:
                        self.logger.debug(f"Skipping listener removal for {topic}: event_bus is None")
                except Exception as e:
                    self.logger.debug(f"Error accessing event bus for {topic}: {e}")
            
            # Clear subscriptions list
            self._subscriptions.clear()
            
            # Release VLC instance
            if self.vlc_instance:
                self.vlc_instance.release()
                self.vlc_instance = None
            
            # Call parent stop method
            await super().stop()
            
        except Exception as e:
            self.logger.error(f"Error during MusicControllerService cleanup: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Cleanup error: {e}",
                severity=LogLevel.ERROR
            )
            raise
        
    async def _load_music_library(self):
        """Load available music tracks from the music directory."""
        try:
            # Get the absolute path to log where we're looking
            abs_music_dir = os.path.abspath(self.music_dir)
            self.logger.info(f"Loading music from directory: {abs_music_dir}")
            
            if not os.path.exists(self.music_dir):
                self.logger.warning(f"Music directory not found: {self.music_dir}")
                
                # Check if there's a music directory at a relative location
                parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                alt_music_dir = os.path.join(parent_dir, "assets", "music")
                self.logger.info(f"Trying alternate music directory: {alt_music_dir}")
                
                if os.path.exists(alt_music_dir):
                    self.music_dir = alt_music_dir
                    self.logger.info(f"Using alternate music directory: {alt_music_dir}")
                else:
                    self.logger.warning(f"Alternate music directory not found: {alt_music_dir}")
                    return
                
            # List all files in the directory
            files = os.listdir(self.music_dir)
            self.logger.debug(f"Files found in music directory: {files}")
            
            for filename in files:
                if filename.endswith(('.mp3', '.wav', '.m4a')):
                    path = os.path.join(self.music_dir, filename)
                    name = os.path.splitext(filename)[0]
                    
                    # Create media to get duration
                    media = self.vlc_instance.media_new(path)
                    media.parse()
                    duration = media.get_duration() / 1000.0  # Convert ms to seconds
                    
                    self.tracks[name] = MusicTrack(
                        name=name,
                        path=path,
                        duration=duration
                    )
                    self.logger.debug(f"Loaded track: {name} ({duration:.1f}s)")
                    
            self.logger.info(f"Loaded {len(self.tracks)} music tracks")
            
        except Exception as e:
            self.logger.error(f"Error loading music library: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Failed to load music library: {e}",
                severity=LogLevel.ERROR
            )
            
    async def _handle_music_command(self, payload):
        """Handle music commands from CLI or other sources."""
        try:
            # Add detailed debugging about the received payload
            self.logger.debug(f"Received music command payload: {payload}")
            
            # Convert to proper payload type if needed
            if not isinstance(payload, MusicCommandPayload):
                self.logger.debug(f"Converting raw payload to MusicCommandPayload: {payload}")
                # Extract command and args from CLI input
                if isinstance(payload, dict):
                    command = payload.get("command", "").strip().lower()
                    args = payload.get("args", [])
                    raw_input = payload.get("raw_input", "")
                    conversation_id = payload.get("conversation_id")
                    
                    self.logger.debug(f"Processing command: '{command}' with args: {args}")
                    self.logger.debug(f"Raw input: '{raw_input}'")
                    
                    # Handle list music command
                    if command == "list" and args and args[0] == "music":
                        self.logger.debug("Processing 'list music' command")
                        await self._handle_list_music(conversation_id)
                        return
                        
                    # Handle play music command
                    if command == "play" and args and args[0] == "music":
                        self.logger.debug("Processing 'play music' command")
                        if len(args) < 2:
                            # Create the error payload as a dictionary
                            error_payload = {
                                "message": "Please specify a track number or name: play music <number/name>",
                                "is_error": True,
                                "timestamp": time.time(),
                                "event_id": str(uuid.uuid4()),
                                "command": "play music"
                            }
                            
                            await self.emit(
                                EventTopics.CLI_RESPONSE,
                                error_payload
                            )
                            return
                            
                        # Get the track identifier (remaining args)
                        track_identifier = args[1]  # This could be a number or name
                        self.logger.debug(f"Track identifier: {track_identifier}")
                        try:
                            # Try to parse as number first
                            track_index = int(track_identifier) - 1  # Convert to 0-based index
                            self.logger.debug(f"Parsed as track index: {track_index}")
                            await self._handle_play_by_index(track_index, conversation_id)
                        except ValueError:
                            # Not a number, try as name
                            track_name = " ".join(args[1:])  # Join all remaining args as name
                            self.logger.debug(f"Using as track name: '{track_name}'")
                            await self._handle_play_by_name(track_name, conversation_id)
                        return
                        
                    # Handle stop music command - now handles both formats
                    if command == "stop" or (command == "stop" and args and args[0] == "music") or (command == "stop music"):
                        self.logger.debug(f"Processing 'stop music' command. Current player state: {self.player is not None}")
                        await self._handle_stop_request(
                            MusicCommandPayload(
                                action="stop",
                                conversation_id=conversation_id
                            )
                        )
                        
                        # Create confirmation response as a dictionary
                        response_payload = {
                            "message": "Music playback stopped",
                            "is_error": False,
                            "timestamp": time.time(),
                            "event_id": str(uuid.uuid4()),
                            "command": "stop music"
                        }
                        
                        await self.emit(
                            EventTopics.CLI_RESPONSE,
                            response_payload
                        )
                        return
                        
                # Handle standard action payload
                if hasattr(payload, 'action'):
                    self.logger.debug(f"Processing action: {payload.action}")
                    if payload.action == "play":
                        await self._handle_play_request(payload)
                    elif payload.action == "stop":
                        await self._handle_stop_request(payload)
                    else:
                        self.logger.warning(f"Unknown music command action: {payload.action}")
                else:
                    self.logger.warning(f"Unhandled music command payload: {payload}")
                
        except Exception as e:
            self.logger.error(f"Error handling music command: {e}")
            # Send error response to CLI
            
            # Create error response as a dictionary
            error_payload = {
                "message": f"Error handling music command: {e}",
                "is_error": True,
                "timestamp": time.time(),
                "event_id": str(uuid.uuid4()),
                "command": "music command"
            }
            
            await self.emit(
                EventTopics.CLI_RESPONSE,
                error_payload
            )
            
    async def _handle_list_music(self, conversation_id=None):
        """List available music tracks and send to CLI."""
        try:
            tracks = await self.get_track_list()
            if not tracks:
                message = "No music tracks found"
            else:
                # Format the list with numbers
                track_list = "\nAvailable Music Tracks:\n"
                for i, track in enumerate(tracks):
                    duration_str = f"{int(track['duration'] // 60)}:{int(track['duration'] % 60):02d}" if track.get('duration') else "?"
                    track_list += f"  {i+1}. {track['name']} ({duration_str})\n"
                
                message = track_list
                
            # Log the response for debugging
            self.logger.debug(f"Sending music list response: {message[:50]}...")
            
            # Create the CliResponsePayload as a dictionary directly
            response_payload = {
                "message": message,
                "is_error": False,
                "timestamp": time.time(),
                "event_id": str(uuid.uuid4()),
                "command": "list music"
            }
            
            # Send response to CLI
            await self.emit(
                EventTopics.CLI_RESPONSE,
                response_payload
            )
        except Exception as e:
            self.logger.error(f"Error listing music tracks: {e}")
            
            # Create error response as a dictionary
            error_payload = {
                "message": f"Error listing music tracks: {e}",
                "is_error": True,
                "timestamp": time.time(),
                "event_id": str(uuid.uuid4()),
                "command": "list music"
            }
            
            await self.emit(
                EventTopics.CLI_RESPONSE,
                error_payload
            )
            
    async def _handle_play_by_index(self, index, conversation_id=None):
        """Play a track by its index in the track list."""
        try:
            tracks = list(self.tracks.keys())
            if 0 <= index < len(tracks):
                track_name = tracks[index]
                await self._handle_play_request(
                    MusicCommandPayload(
                        action="play",
                        song_query=track_name,
                        conversation_id=conversation_id
                    )
                )
                
                # Log the response for debugging
                self.logger.debug(f"Sending play confirmation: track {track_name}")
                
                # Create the response payload as a dictionary
                response_payload = {
                    "message": f"Now playing: {track_name}",
                    "is_error": False,
                    "timestamp": time.time(),
                    "event_id": str(uuid.uuid4()),
                    "command": "play music"
                }
                
                # Send confirmation to CLI
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    response_payload
                )
            else:
                # Create error response as a dictionary
                error_payload = {
                    "message": f"Invalid track number: {index+1}. Please choose between 1 and {len(tracks)}",
                    "is_error": True,
                    "timestamp": time.time(),
                    "event_id": str(uuid.uuid4()),
                    "command": "play music"
                }
                
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    error_payload
                )
        except Exception as e:
            self.logger.error(f"Error playing track by index: {e}")
            
            # Create error response as a dictionary
            error_payload = {
                "message": f"Error playing music: {e}",
                "is_error": True,
                "timestamp": time.time(),
                "event_id": str(uuid.uuid4()),
                "command": "play music"
            }
            
            await self.emit(
                EventTopics.CLI_RESPONSE,
                error_payload
            )
            
    async def _handle_play_by_name(self, name, conversation_id=None):
        """Play a track by matching its name."""
        try:
            # Find matching track
            name_lower = name.lower()
            matches = []
            
            for track_name in self.tracks.keys():
                if name_lower in track_name.lower():
                    matches.append(track_name)
                    
            if len(matches) == 1:
                # Exact one match found
                track_name = matches[0]
                await self._handle_play_request(
                    MusicCommandPayload(
                        action="play",
                        song_query=track_name,
                        conversation_id=conversation_id
                    )
                )
                
                # Log the response for debugging
                self.logger.debug(f"Sending play confirmation: track {track_name}")
                
                # Create the response payload as a dictionary
                response_payload = {
                    "message": f"Now playing: {track_name}",
                    "is_error": False,
                    "timestamp": time.time(),
                    "event_id": str(uuid.uuid4()),
                    "command": "play music"
                }
                
                # Send confirmation to CLI
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    response_payload
                )
            elif len(matches) > 1:
                # Multiple matches
                match_list = "\nMultiple matches found. Please be more specific or use track number:\n"
                for i, track_name in enumerate(matches):
                    match_list += f"  • {track_name}\n"
                
                # Create the response payload as a dictionary
                response_payload = {
                    "message": match_list,
                    "is_error": False,
                    "timestamp": time.time(),
                    "event_id": str(uuid.uuid4()),
                    "command": "play music"
                }
                
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    response_payload
                )
            else:
                # No matches
                # Create error response as a dictionary
                error_payload = {
                    "message": f"No tracks found matching '{name}'",
                    "is_error": True,
                    "timestamp": time.time(),
                    "event_id": str(uuid.uuid4()),
                    "command": "play music"
                }
                
                await self.emit(
                    EventTopics.CLI_RESPONSE,
                    error_payload
                )
        except Exception as e:
            self.logger.error(f"Error playing track by name: {e}")
            
            # Create error response as a dictionary
            error_payload = {
                "message": f"Error playing music: {e}",
                "is_error": True,
                "timestamp": time.time(),
                "event_id": str(uuid.uuid4()),
                "command": "play music"
            }
            
            await self.emit(
                EventTopics.CLI_RESPONSE,
                error_payload
            )
            
    async def _handle_play_request(self, payload: MusicCommandPayload):
        """Handle a request to play music."""
        try:
            if not payload.song_query:
                self.logger.warning("No song query provided in play request")
                return
                
            # Find matching track
            track = None
            query = payload.song_query.lower()
            for t in self.tracks.values():
                if query in t.name.lower():
                    track = t
                    break
                    
            if not track:
                self.logger.warning(f"No matching track found for query: {payload.song_query}")
                return
                
            # Stop current playback if any
            if self.player:
                self.player.stop()
                self.player.release()
                
            # Create new media player
            self.player = self.vlc_instance.media_player_new()
            media = self.vlc_instance.media_new(track.path)
            self.player.set_media(media)
            
            # Set initial volume based on current state
            volume = self.ducking_volume if self.is_ducking else self.normal_volume
            self.player.audio_set_volume(volume)
            
            # Start playback
            self.player.play()
            self.current_track = track
            
            # Emit state change event
            await self.emit(
                EventTopics.MUSIC_PLAYBACK_STARTED,
                BaseEventPayload(
                    conversation_id=payload.conversation_id
                )
            )
            
        except Exception as e:
            self.logger.error(f"Error handling play request: {e}")
            
            # Clean up resources on error
            if self.player:
                try:
                    self.player.stop()
                    self.player.release()
                except Exception as cleanup_error:
                    self.logger.error(f"Error during player cleanup: {cleanup_error}")
                finally:
                    self.player = None
                    self.current_track = None
            
            await self.emit(
                EventTopics.MUSIC_ERROR,
                BaseEventPayload(
                    conversation_id=payload.conversation_id
                )
            )
            
    async def _handle_stop_request(self, payload: MusicCommandPayload):
        """Handle a request to stop music playback."""
        try:
            self.logger.debug(f"Handling stop request. Current player: {self.player is not None}")
            if self.player:
                self.logger.debug("Stopping and releasing player")
                self.player.stop()
                self.player.release()
                self.player = None
                self.current_track = None
                
                self.logger.debug("Emitting playback stopped event")
                await self.emit(
                    EventTopics.MUSIC_PLAYBACK_STOPPED,
                    BaseEventPayload(
                        conversation_id=payload.conversation_id
                    )
                )
            else:
                self.logger.debug("No active player to stop")
                
        except Exception as e:
            self.logger.error(f"Error handling stop request: {e}")
            await self.emit(
                EventTopics.MUSIC_ERROR,
                BaseEventPayload(
                    conversation_id=payload.conversation_id
                )
            )
            
    async def _handle_mode_change(self, payload: Dict[str, Any]):
        """Handle system mode changes."""
        # Use new_mode field from the payload (not SystemModePayload.mode)
        if not isinstance(payload, dict) or "new_mode" not in payload:
            self.logger.error(f"Invalid mode change payload: {payload}")
            return
            
        new_mode = payload["new_mode"]
        self.current_mode = new_mode
        
        # Get conversation ID if available
        conversation_id = payload.get("conversation_id")
        
        # Stop music in IDLE mode
        if new_mode == "IDLE" and self.player:
            await self._handle_stop_request(
                MusicCommandPayload(
                    action="stop",
                    conversation_id=conversation_id
                )
            )
            
    async def _handle_speech_start(self, payload: BaseEventPayload):
        """Handle speech synthesis start - reduce music volume."""
        if self.player and self.current_mode == "INTERACTIVE":
            self.is_ducking = True
            self.player.audio_set_volume(self.ducking_volume)
            
    async def _handle_speech_end(self, payload: BaseEventPayload):
        """Handle speech synthesis end - restore music volume."""
        if self.player and self.current_mode == "INTERACTIVE":
            self.is_ducking = False
            self.player.audio_set_volume(self.normal_volume)
            
    async def get_track_list(self) -> List[Dict[str, any]]:
        """Get a list of available music tracks."""
        track_info = []
        for track in self.tracks.values():
            track_info.append({
                "name": track.name,
                "duration": track.duration
            })
        return track_info
        
    async def play_music(self, track_index=None, track_name=None):
        """
        Play a music track by index or name.
        
        Args:
            track_index: Index of the track to play (optional)
            track_name: Name of the track to play (optional)
            
        Returns:
            True if playback started, False otherwise
        """
        try:
            # Get track by index if provided
            if track_index is not None and 0 <= track_index < len(self.tracks):
                track_name = list(self.tracks.keys())[track_index]
                
            # Get track by name if provided or derived from index
            if track_name:
                # Create a payload to pass to _handle_play_request
                payload = MusicCommandPayload(
                    action="play",
                    song_query=track_name,
                    conversation_id=None
                )
                await self._handle_play_request(payload)
                return True
                
            return False
        except Exception as e:
            self.logger.error(f"Error in play_music: {e}")
            return False
        
    async def _cleanup_player(self, player):
        """
        Clean up a VLC player instance.
        
        Args:
            player: The VLC player instance to clean up
            
        Returns:
            None
        """
        if player:
            try:
                player.stop()
                player.release()
                self.logger.debug(f"Successfully cleaned up VLC player {id(player)}")
            except Exception as e:
                self.logger.error(f"Error cleaning up VLC player: {e}")
        
    async def _emit_status(
        self,
        status: ServiceStatus,
        message: str,
        severity: Optional[LogLevel] = None
    ) -> None:
        """Report service status change."""
        payload = ServiceStatusPayload(
            service_name=self.service_name,
            status=status,
            message=message,
            severity=severity
        )
        await self.emit(EventTopics.SERVICE_STATUS_UPDATE, payload) 