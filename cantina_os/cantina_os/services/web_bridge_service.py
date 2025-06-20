"""
DJ R3X Web Bridge Service

CantinaOS service that provides web dashboard connectivity via FastAPI and Socket.IO.
Bridges the web dashboard with CantinaOS event bus for real-time monitoring and control.
"""

"""
SERVICE: WebBridgeService
PURPOSE: Web dashboard connectivity bridge with FastAPI REST API and Socket.IO real-time communication
EVENTS_IN: SERVICE_STATUS_UPDATE, TRANSCRIPTION_FINAL, VOICE_LISTENING_STARTED, VOICE_LISTENING_STOPPED, MIC_RECORDING_START, MIC_RECORDING_STOP, MUSIC_PLAYBACK_STARTED, MUSIC_PLAYBACK_STOPPED, MUSIC_LIBRARY_UPDATED, DJ_MODE_CHANGED, LLM_RESPONSE, SYSTEM_MODE_CHANGE, DASHBOARD_LOG
EVENTS_OUT: MUSIC_COMMAND, SYSTEM_SET_MODE_REQUEST, USER_INPUT, SERVICE_STATUS_REQUEST
KEY_METHODS: _handle_music_command, _handle_voice_command, _handle_system_command, _broadcast_event_to_dashboard, broadcast_validated_status
DEPENDENCIES: FastAPI, Socket.IO, uvicorn web server, CORS middleware for web dashboard connectivity
"""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, Optional, Set

import socketio
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..base_service import BaseService
from ..core.event_topics import EventTopics
from ..event_payloads import ServiceStatus
from ..schemas.validation import SocketIOValidationMixin, StatusPayloadValidationMixin, validate_socketio_command
from ..schemas.web_commands import (
    VoiceCommandSchema,
    MusicCommandSchema,
    DJCommandSchema,
    SystemCommandSchema,
    validate_command_data
)
from ..schemas import BaseWebResponse, WebCommandError

# Configure logging
logger = logging.getLogger(__name__)


class WebBridgeService(BaseService, SocketIOValidationMixin, StatusPayloadValidationMixin):
    """
    Web Bridge Service for DJ R3X Dashboard

    Provides FastAPI endpoints and Socket.IO connectivity for the web dashboard.
    Bridges web client communication with CantinaOS event bus.
    """

    def __init__(self, event_bus, config=None, name="web_bridge"):
        """Initialize the Web Bridge Service."""
        super().__init__(
            service_name=name,
            event_bus=event_bus,
            logger=logging.getLogger(__name__)
        )
        

        self._config = config or {}
        self._host = self._config.get("host", "127.0.0.1")
        self._port = self._config.get("port", 8000)

        # Web server components
        self._app: Optional[FastAPI] = None
        self._server: Optional[uvicorn.Server] = None
        self._sio: Optional[socketio.AsyncServer] = None

        # Dashboard state
        self._dashboard_clients: Dict[str, Any] = {}
        self._event_buffer = []
        self._service_status = {}
        self._cached_service_status = {}  # Cache for intelligent status caching
        self._last_status_update = 0  # Timestamp of last status update
        self._music_library_cache = {}  # Cache for music library data with durations

        # Event filtering and throttling
        self._event_throttle_state = defaultdict(lambda: {"last_sent": 0, "count": 0})
        self._high_frequency_events = {
            EventTopics.AUDIO_CHUNK,
            EventTopics.VOICE_AUDIO_LEVEL,
            EventTopics.SPEECH_SYNTHESIS_AMPLITUDE,
        }
        self._throttle_limits = {
            "high_frequency": {
                "max_per_second": 10,
                "events": self._high_frequency_events,
            },
            "medium_frequency": {
                "max_per_second": 30,
                "events": {
                    EventTopics.TRANSCRIPTION_INTERIM,
                    EventTopics.LLM_RESPONSE_CHUNK,
                },
            },
            "low_frequency": {"max_per_second": 0, "events": set()},
        }

    async def _start(self) -> None:
        """Start the web bridge service."""
        # CRITICAL DEBUG: Use proper logging to trace execution
        self._logger.critical("CRITICAL DEBUG: WebBridge._start() method called!")
        logging.getLogger("cantina_os.main").critical("CRITICAL DEBUG: WebBridge._start() method called!")
        
        try:
            self._logger.critical("[WebBridge] Step 1: Starting DJ R3X Web Bridge Service")

            # Create FastAPI application
            self._logger.critical("[WebBridge] Step 2: About to create FastAPI application")
            self._create_fastapi_app()
            self._logger.critical("[WebBridge] Step 2: FastAPI application created successfully")

            # Create Socket.IO server
            self._logger.critical("[WebBridge] Step 3: About to create Socket.IO server")
            self._create_socketio_server()
            self._logger.critical("[WebBridge] Step 3: Socket.IO server created successfully")

            # Subscribe to CantinaOS events
            self._logger.critical("[WebBridge] Step 4: About to subscribe to events")
            await self._subscribe_to_events()
            self._logger.critical("[WebBridge] Step 4: Event subscriptions completed")

            # Start the web server
            self._logger.critical("[WebBridge] Step 5: About to start web server")
            await self._start_web_server()
            self._logger.critical("[WebBridge] Step 5: Web server started successfully")

            # Start background tasks
            self._logger.critical("[WebBridge] Step 6: Starting background tasks")
            asyncio.create_task(self._periodic_status_broadcast())
            self._logger.critical("[WebBridge] Step 6: Background tasks started")

            # Request status from all services that may have started before us
            self._logger.critical("[WebBridge] Step 7: Requesting status from existing services")
            self._event_bus.emit(
                EventTopics.SERVICE_STATUS_REQUEST,
                {"source": "web_bridge", "timestamp": datetime.now().isoformat()},
            )
            self._logger.critical("[WebBridge] Step 7: Status request sent")
            
            # Request music library update if music controller is already running
            self._logger.info("[WebBridge] Requesting music library update from music controller")
            self._event_bus.emit(
                EventTopics.MUSIC_COMMAND,
                {
                    "command": "refresh_music_library",
                    "source": "web_bridge",
                    "timestamp": datetime.now().isoformat()
                }
            )

            self._logger.critical("[WebBridge] Step 8: ALL INITIALIZATION COMPLETE - WebBridge fully operational")
            
        except Exception as e:
            self._logger.critical(f"[WebBridge] CRITICAL ERROR in _start(): {e}")
            import traceback
            self._logger.critical(f"[WebBridge] Traceback: {traceback.format_exc()}")
            # Re-raise to ensure BaseService knows the start failed
            raise

    async def _stop(self) -> None:
        """Stop the web bridge service."""
        self._logger.info("Stopping DJ R3X Web Bridge Service")

        if self._server:
            self._server.should_exit = True
            await asyncio.sleep(1)  # Give server time to cleanup

        self._status = ServiceStatus.STOPPED
        await self._emit_status(
            ServiceStatus.STOPPED, "Web Bridge Service stopped"
        )
        self._logger.info("Web Bridge Service stopped")

    def _create_fastapi_app(self) -> None:
        """Create the FastAPI application with all endpoints."""
        self._app = FastAPI(
            title="DJ R3X Web Bridge",
            description="Bridge service connecting web dashboard to CantinaOS",
            version="1.0.0",
        )

        # Configure CORS
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:3000"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Add API routes
        self._add_api_routes()

    def _create_socketio_server(self) -> None:
        """Create the Socket.IO server and mount to FastAPI."""
        self._sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=["http://localhost:3000"],
            logger=False,  # DISABLE SocketIO debug logging to prevent terminal spam
            engineio_logger=False,  # DISABLE EngineIO debug logging to prevent terminal spam
        )

        # Add Socket.IO event handlers
        self._add_socketio_handlers()

        # Mount Socket.IO to FastAPI
        self._app = socketio.ASGIApp(self._sio, self._app)

    def _add_api_routes(self) -> None:
        """Add REST API routes to FastAPI app."""

        @self._app.get("/")
        async def root():
            """Health check endpoint"""
            return {
                "service": "DJ R3X Web Bridge",
                "status": "running",
                "cantina_os_connected": True,
                "dashboard_clients": len(self._dashboard_clients),
                "timestamp": datetime.now().isoformat(),
            }

        @self._app.get("/api/system/status")
        async def get_system_status():
            """Get current system status"""
            return {
                "status": "online",
                "services": self._get_service_status(),
                "uptime": "00:00:00",
                "memory_usage": "0 MB",
                "cpu_usage": "0%",
            }

        @self._app.get("/api/music/library")
        async def get_music_library():
            """Get music library from CantinaOS"""
            try:
                tracks = []
                
                # First, try to use cached data with actual durations
                if self._music_library_cache:
                    logger.info(f"[WebBridge] Serving {len(self._music_library_cache)} tracks from cache")
                    
                    for i, (track_name, track_data) in enumerate(self._music_library_cache.items()):
                        # Format duration properly
                        duration_seconds = track_data.get("duration")
                        if duration_seconds is not None:
                            # Convert seconds to MM:SS format
                            minutes = int(duration_seconds // 60)
                            seconds = int(duration_seconds % 60)
                            duration_str = f"{minutes}:{seconds:02d}"
                        else:
                            duration_str = "Unknown"
                        
                        tracks.append({
                            "id": str(i + 1),
                            "title": track_data.get("title", track_name),
                            "artist": track_data.get("artist", "Cantina Band"),
                            "duration": duration_str,
                            "file": os.path.basename(track_data.get("path", "")),
                            "path": track_data.get("path", "")
                        })
                else:
                    # Fallback to filesystem if no cached data available
                    logger.info("[WebBridge] No cached music library, falling back to filesystem")
                    music_dir = os.path.join(
                        os.path.dirname(__file__), "..", "assets", "music"
                    )
                    
                    if os.path.exists(music_dir):
                        for i, filename in enumerate(os.listdir(music_dir)):
                            if filename.endswith((".mp3", ".wav", ".m4a")):
                                name, _ = os.path.splitext(filename)
                                if " - " in name:
                                    artist, title = name.split(" - ", 1)
                                else:
                                    artist = "Cantina Band"
                                    title = name

                                tracks.append({
                                    "id": str(i + 1),
                                    "title": title.strip(),
                                    "artist": artist.strip(),
                                    "duration": "Unknown",  # Changed from hardcoded "3:00"
                                    "file": filename,
                                    "path": os.path.join(music_dir, filename),
                                })

                return {"tracks": tracks}
            except Exception as e:
                logger.error(f"Error fetching music library: {e}")
                return {"tracks": [], "error": str(e)}

    def _add_socketio_handlers(self) -> None:
        """Add Socket.IO event handlers."""

        @self._sio.event
        async def connect(sid, environ):
            """Handle client connection"""
            logger.info(f"Dashboard client connected: {sid}")
            self._dashboard_clients[sid] = {
                "connected_at": datetime.now(),
                "subscriptions": [],
            }

            # Send current system status
            await self._sio.emit(
                "system_status",
                {
                    "cantina_os_connected": True,
                    "services": self._get_service_status(),
                    "timestamp": datetime.now().isoformat(),
                },
                room=sid,
            )

            # Send buffered events
            for event in self._event_buffer[-10:]:
                await self._sio.emit("event_replay", event, room=sid)

        @self._sio.event
        async def disconnect(sid):
            """Handle client disconnection"""
            logger.info(f"Dashboard client disconnected: {sid}")
            if sid in self._dashboard_clients:
                del self._dashboard_clients[sid]

        @self._sio.event
        async def subscribe_events(sid, data):
            """Handle event subscription requests"""
            event_types = data.get("events", [])
            if sid in self._dashboard_clients:
                self._dashboard_clients[sid]["subscriptions"] = event_types
                self._dashboard_clients[sid]["filter_level"] = data.get(
                    "filter_level", "all"
                )
                logger.info(f"Client {sid} subscribed to: {event_types}")

        # Register validated command handlers
        self._sio.on("voice_command", self._handle_voice_command)
        self._sio.on("music_command", self._handle_music_command)
        # REMOVED dj_command - DJ commands should go through regular command system
        self._sio.on("system_command", self._handle_system_command)
        
        # Voice recording control handlers (for VoiceTab Talk/Stop buttons)
        self._sio.on("voice_recording_start", self._handle_voice_recording_start)
        self._sio.on("voice_recording_stop", self._handle_voice_recording_stop)
        
        # Handle regular CLI-style commands from dashboard
        @self._sio.event
        async def command(sid, data):
            """Handle simple CLI commands from dashboard"""
            command_text = data.get("command", "").strip()
            logger.info(f"Command from dashboard {sid}: {command_text}")
            
            # Parse the command text just like CLI does
            parts = command_text.split()
            command = parts[0] if parts else ""
            args = parts[1:] if len(parts) > 1 else []
            
            # Emit to command dispatcher with proper payload structure
            self._event_bus.emit(EventTopics.CLI_COMMAND, {
                "command": command,
                "args": args,
                "raw_input": command_text,
                "source": "dashboard",
                "sid": sid
            })

    async def _subscribe_to_events(self) -> None:
        """Subscribe to CantinaOS events for dashboard monitoring."""
        logger.info("[WebBridge] Subscribing to CantinaOS events...")
        
        # Use proper BaseService async subscription pattern
        await asyncio.gather(
            self.subscribe(EventTopics.SERVICE_STATUS_UPDATE, self._handle_service_status_update),
            self.subscribe(EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription_final),
            self.subscribe(EventTopics.TRANSCRIPTION_INTERIM, self._handle_transcription_interim),
            self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started),
            self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped),
            
            # Voice recording events - critical for recording status
            self.subscribe(EventTopics.MIC_RECORDING_START, self._handle_mic_recording_start),
            self.subscribe(EventTopics.MIC_RECORDING_STOP, self._handle_mic_recording_stop),
            
            # Voice completion events - critical for resetting voice status to idle
            self.subscribe(EventTopics.VOICE_PROCESSING_COMPLETE, self._handle_voice_processing_complete),
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_STARTED, self._handle_speech_synthesis_started),
            self.subscribe(EventTopics.SPEECH_GENERATION_STARTED, self._handle_speech_synthesis_started),  # CRITICAL: Missing event!
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_COMPLETED, self._handle_speech_synthesis_completed),
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_ENDED, self._handle_speech_synthesis_ended),
            self.subscribe(EventTopics.SPEECH_GENERATION_COMPLETE, self._handle_speech_generation_complete),
            self.subscribe(EventTopics.LLM_PROCESSING_ENDED, self._handle_llm_processing_ended),
            self.subscribe(EventTopics.VOICE_ERROR, self._handle_voice_error),
            
            # Music events - CRITICAL for dashboard music status
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_playback_started),
            self.subscribe(EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_playback_stopped),
            self.subscribe(EventTopics.MUSIC_PLAYBACK_PAUSED, self._handle_music_playback_paused),
            self.subscribe(EventTopics.MUSIC_PLAYBACK_RESUMED, self._handle_music_playback_resumed),
            self.subscribe(EventTopics.MUSIC_PROGRESS, self._handle_music_progress),
            self.subscribe(EventTopics.MUSIC_QUEUE_UPDATED, self._handle_music_queue_updated),
            self.subscribe(EventTopics.MUSIC_LIBRARY_UPDATED, self._handle_music_library_updated),
            
            # DJ Mode and Commentary events - CRITICAL for dashboard status indicators
            self.subscribe(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed),
            self.subscribe(EventTopics.GPT_COMMENTARY_RESPONSE, self._handle_gpt_commentary_response),
            self.subscribe(EventTopics.CROSSFADE_STARTED, self._handle_crossfade_started),
            self.subscribe(EventTopics.DJ_NEXT_TRACK_SELECTED, self._handle_dj_next_track_selected),
            
            # Other events
            self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response),
            self.subscribe(EventTopics.SYSTEM_ERROR, self._handle_system_error),
            self.subscribe(EventTopics.DASHBOARD_LOG, self._handle_dashboard_log),
            
            # System mode events - critical for dashboard state synchronization
            self.subscribe(EventTopics.SYSTEM_MODE_CHANGE, self._handle_system_mode_change),
            self.subscribe(EventTopics.MODE_TRANSITION_STARTED, self._handle_mode_transition),
            self.subscribe(EventTopics.MODE_TRANSITION_COMPLETE, self._handle_mode_transition),
        )
        
        logger.info(f"[WebBridge] Successfully subscribed to all events")

    async def _start_web_server(self) -> None:
        """Start the uvicorn web server."""
        config = uvicorn.Config(
            self._app, host=self._host, port=self._port, log_level="warning"  # Reduce uvicorn log noise
        )
        self._server = uvicorn.Server(config)

        # Start server in background task
        asyncio.create_task(self._server.serve())

        # Wait a moment for server to start
        await asyncio.sleep(1)

    def _get_service_status(self) -> Dict[str, Any]:
        """Get current service status."""
        # Default service definitions for services that may not have reported yet
        default_services = {
            "web_bridge": {"status": "offline", "uptime": "0:00:00"},
            "debug": {"status": "offline", "uptime": "0:00:00"},
            "logging_service": {"status": "offline", "uptime": "0:00:00"},
            "yoda_mode_manager": {"status": "offline", "uptime": "0:00:00"},
            "mode_command_handler": {"status": "offline", "uptime": "0:00:00"},
            "memory_service": {"status": "offline", "uptime": "0:00:00"},
            "mouse_input": {"status": "offline", "uptime": "0:00:00"},
            "deepgram_direct_mic": {"status": "offline", "uptime": "0:00:00"},
            "gpt_service": {"status": "offline", "uptime": "0:00:00"},
            "intent_router_service": {"status": "offline", "uptime": "0:00:00"},
            "brain_service": {"status": "offline", "uptime": "0:00:00"},
            "timeline_executor_service": {"status": "offline", "uptime": "0:00:00"},
            "elevenlabs_service": {"status": "offline", "uptime": "0:00:00"},
            "cached_speech_service": {"status": "offline", "uptime": "0:00:00"},
            "mode_change_sound": {"status": "offline", "uptime": "0:00:00"},
            "MusicController": {"status": "offline", "uptime": "0:00:00"},
            "eye_light_controller": {"status": "offline", "uptime": "0:00:00"},
            "cli": {"status": "offline", "uptime": "0:00:00"},
            "command_dispatcher": {"status": "offline", "uptime": "0:00:00"},
        }

        # Merge real service status data with defaults
        # Real status data takes precedence over defaults
        merged_status = default_services.copy()
        merged_status.update(self._service_status)

        return merged_status

    async def _periodic_status_broadcast(self) -> None:
        """Periodically broadcast status to all connected clients with intelligent caching."""
        while self._status == ServiceStatus.RUNNING:
            if self._dashboard_clients:
                current_time = time.time()

                # Get current service status
                current_status = self._get_service_status()

                # Only broadcast if status has changed or if it's been more than 60 seconds
                should_broadcast = (
                    current_status != self._cached_service_status
                    or current_time - self._last_status_update > 60
                )

                if should_broadcast:
                    status_data = {
                        "cantina_os_connected": True,
                        "services": current_status,
                        "timestamp": datetime.now().isoformat(),
                    }
                    await self._sio.emit("system_status", status_data)

                    # Update cache and timestamp
                    self._cached_service_status = current_status.copy()
                    self._last_status_update = current_time

            # Reduced frequency from 5s to 30s for polling
            await asyncio.sleep(30)

    async def _broadcast_event_to_dashboard(
        self, event_topic: str, data: Dict[str, Any], event_name: str = None, skip_validation: bool = False
    ):
        """Broadcast an event to all connected dashboard clients."""
        if not self._dashboard_clients or not self._sio:
            return

        # The `skip_validation` parameter indicates the data is already validated
        # and serialized by `validate_and_serialize_status`.
        # The logic here should simply be to wrap and emit.
        
        # All validation logic has been removed from this function as it was
        # redundant and causing serialization issues. The `StatusPayloadValidationMixin`
        # is now the single source of truth for status validation.

        event_data = {
            "topic": event_topic,
            "data": data, # `data` is now assumed to be the validated payload
            "timestamp": datetime.now().isoformat(),
            "validated": skip_validation # Use skip_validation flag to indicate status
        }

        # Broadcast to all connected clients
        for sid in self._dashboard_clients.keys():
            try:
                await self._sio.emit(
                    event_name or "cantina_event", event_data, room=sid
                )
            except Exception as e:
                logger.error(f"Error broadcasting event to client {sid}: {e}")

    # Event handlers
    async def _handle_service_status_update(self, data):
        """Handle service status updates from CantinaOS"""
        service_name = data.get("service_name", "unknown")
        raw_status = data.get("status", "unknown")

        # Map backend status to frontend expected values
        status_map = {
            "RUNNING": "online",
            "STOPPED": "offline",
            "ERROR": "offline",
            "DEGRADED": "warning",
            "INITIALIZING": "warning",
        }
        status = status_map.get(raw_status, "offline")

        self._service_status[service_name] = {
            "status": status,
            "uptime": data.get("uptime", "0:00:00"),
            "last_update": datetime.now().isoformat(),
        }

        await self._broadcast_event_to_dashboard(
            EventTopics.SERVICE_STATUS_UPDATE,
            {
                "service": service_name,
                "status": status,
                "data": self._service_status[service_name],
            },
            "service_status_update",
        )

    async def _handle_transcription_final(self, data):
        """Handle final transcription results"""
        await self._broadcast_event_to_dashboard(
            EventTopics.TRANSCRIPTION_FINAL,
            {
                "text": data.get("text", ""),
                "confidence": data.get("confidence", 0.0),
                "final": True,
            },
            "transcription_update",
        )

    async def _handle_transcription_interim(self, data):
        """Handle interim transcription results"""
        await self._broadcast_event_to_dashboard(
            EventTopics.TRANSCRIPTION_INTERIM,
            {
                "text": data.get("text", ""),
                "confidence": data.get("confidence", 0.0),
                "final": False,
            },
            "transcription_update",
        )

    async def _handle_voice_listening_started(self, data):
        """Handle voice listening started event with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "recording",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "recording",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.VOICE_LISTENING_STARTED,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast voice listening started status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.VOICE_LISTENING_STARTED, raw_payload, "voice_status"
            )

    async def _handle_voice_listening_stopped(self, data):
        """Handle voice listening stopped event with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "processing",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "processing",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.VOICE_LISTENING_STOPPED,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast voice listening stopped status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.VOICE_LISTENING_STOPPED, raw_payload, "voice_status"
            )

    async def _handle_mic_recording_start(self, data):
        """Handle mic recording start event - emit recording status with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "recording",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "recording",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.MIC_RECORDING_START,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast mic recording start status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.MIC_RECORDING_START, raw_payload, "voice_status"
            )

    async def _handle_mic_recording_stop(self, data):
        """Handle mic recording stop event - emit processing status with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "processing",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "processing",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.MIC_RECORDING_STOP,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast mic recording stop status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.MIC_RECORDING_STOP, raw_payload, "voice_status"
            )

    async def _handle_voice_processing_complete(self, data):
        """Handle voice processing completion - reset to idle with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.VOICE_PROCESSING_COMPLETE,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast voice processing complete status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.VOICE_PROCESSING_COMPLETE, raw_payload, "voice_status"
            )

    async def _handle_speech_synthesis_started(self, data):
        """Handle speech synthesis started - set status to speaking with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "speaking",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "speaking",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.SPEECH_SYNTHESIS_STARTED,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast speech synthesis started status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.SPEECH_SYNTHESIS_STARTED, raw_payload, "voice_status"
            )

    async def _handle_speech_synthesis_completed(self, data):
        """Handle speech synthesis completion - reset to idle with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.SPEECH_SYNTHESIS_COMPLETED,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast speech synthesis completed status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.SPEECH_SYNTHESIS_COMPLETED, raw_payload, "voice_status"
            )

    async def _handle_speech_synthesis_ended(self, data):
        """Handle speech synthesis ended - reset to idle with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.SPEECH_SYNTHESIS_ENDED,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast speech synthesis ended status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.SPEECH_SYNTHESIS_ENDED, raw_payload, "voice_status"
            )

    async def _handle_speech_generation_complete(self, data):
        """Handle speech generation complete - reset to idle with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.SPEECH_GENERATION_COMPLETE,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast speech generation complete status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.SPEECH_GENERATION_COMPLETE, raw_payload, "voice_status"
            )

    async def _handle_llm_processing_ended(self, data):
        """Handle LLM processing completion - reset to idle with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "idle",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.LLM_PROCESSING_ENDED,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast LLM processing ended status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.LLM_PROCESSING_ENDED, raw_payload, "voice_status"
            )

    async def _handle_voice_error(self, data):
        """Handle voice processing error - reset to idle with enhanced validation"""
        # Use enhanced validation for voice status payload
        raw_payload = {
            "status": "idle",
            "error": data.get("error", "Voice processing error"),
            "timestamp": datetime.now().isoformat()  # Always use ISO string format
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "status": "idle",
            "error": "Voice processing error",
            "timestamp": datetime.now().isoformat()
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="voice",
            data=raw_payload,
            event_topic=EventTopics.VOICE_ERROR,
            socket_event_name="voice_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast voice error status, using fallback method")
            await self._broadcast_event_to_dashboard(
                EventTopics.VOICE_ERROR, raw_payload, "voice_status"
            )

    async def _handle_music_playback_started(self, data):
        """Handle music playback started event with enhanced validation"""
        logger.critical(f"[WebBridge] CRITICAL DEBUG: _handle_music_playback_started called with data: {data}")
        logger.critical(f"[WebBridge] CRITICAL DEBUG: Data type: {type(data)}")
        logger.critical(f"[WebBridge] CRITICAL DEBUG: Data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
        
        track_data = data.get("track", {})
        logger.info(f"[WebBridge] Music playback started - track data: {track_data}")
        
        # Use enhanced validation for music status payload
        raw_payload = {
            "action": "started",
            "track": track_data,
            "source": data.get("source", "unknown"),
            "mode": data.get("mode", "INTERACTIVE"),
            # Phase 2.3: Add timing data for client-side progress calculation
            "start_timestamp": data.get("start_timestamp"),
            "duration": data.get("duration"),
        }
        
        logger.critical(f"[WebBridge] CRITICAL DEBUG: Built raw_payload: {raw_payload}")
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "action": "started",
            "track": None,
            "source": "music_controller",
            "mode": "INTERACTIVE",
            "start_timestamp": None,
            "duration": None
        }
        
        # Use the enhanced validation system
        logger.critical(f"[WebBridge] CRITICAL DEBUG: About to call broadcast_validated_status")
        success = await self.broadcast_validated_status(
            status_type="music",
            data=raw_payload,
            event_topic=EventTopics.MUSIC_PLAYBACK_STARTED,
            socket_event_name="music_status",
            fallback_data=fallback_payload
        )
        logger.critical(f"[WebBridge] CRITICAL DEBUG: broadcast_validated_status returned: {success}")
        
        if success:
            logger.info(f"[WebBridge] Successfully broadcast validated music status")
        else:
            logger.warning(f"[WebBridge] Failed to broadcast music status, using fallback method")
            # Fallback to original method if validation broadcast fails
            await self._broadcast_event_to_dashboard(
                EventTopics.MUSIC_PLAYBACK_STARTED,
                raw_payload,
                "music_status",
            )

    async def _handle_music_playback_stopped(self, data):
        """Handle music playback stopped event with enhanced validation"""
        # Use enhanced validation for music status payload
        raw_payload = {
            "action": "stopped",
            "track": data.get("track"),
            "track_name": data.get("track_name"),
            "source": data.get("source", "music_controller"),
            "mode": data.get("mode", "INTERACTIVE"),
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "action": "stopped",
            "track": None,
            "source": "music_controller",
            "mode": "INTERACTIVE"
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="music",
            data=raw_payload,
            event_topic=EventTopics.MUSIC_PLAYBACK_STOPPED,
            socket_event_name="music_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast music stopped status, using fallback method")
            # Fallback to original method if validation broadcast fails
            await self._broadcast_event_to_dashboard(
                EventTopics.MUSIC_PLAYBACK_STOPPED,
                raw_payload,
                "music_status",
            )

    async def _handle_music_playback_paused(self, data):
        """Handle music playback paused event with enhanced validation"""
        # Use enhanced validation for music status payload
        raw_payload = {
            "action": "paused",
            "track": data.get("track"),
            "source": data.get("source", "music_controller"),
            "mode": data.get("mode", "INTERACTIVE"),
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "action": "paused",
            "track": None,
            "source": "music_controller",
            "mode": "INTERACTIVE"
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="music",
            data=raw_payload,
            event_topic=EventTopics.MUSIC_PLAYBACK_PAUSED,
            socket_event_name="music_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast music paused status, using fallback method")
            # Fallback to original method if validation broadcast fails
            await self._broadcast_event_to_dashboard(
                EventTopics.MUSIC_PLAYBACK_PAUSED,
                raw_payload,
                "music_status",
            )

    async def _handle_music_playback_resumed(self, data):
        """Handle music playback resumed event with enhanced validation"""
        # Use enhanced validation for music status payload
        raw_payload = {
            "action": "resumed",
            "track": data.get("track"),
            "source": data.get("source", "music_controller"),
            "mode": data.get("mode", "INTERACTIVE"),
        }
        
        # Create fallback payload in case of validation failure
        fallback_payload = {
            "action": "resumed",
            "track": None,
            "source": "music_controller",
            "mode": "INTERACTIVE"
        }
        
        # Use the enhanced validation system
        success = await self.broadcast_validated_status(
            status_type="music",
            data=raw_payload,
            event_topic=EventTopics.MUSIC_PLAYBACK_RESUMED,
            socket_event_name="music_status",
            fallback_data=fallback_payload
        )
        
        if not success:
            logger.warning(f"[WebBridge] Failed to broadcast music resumed status, using fallback method")
            # Fallback to original method if validation broadcast fails
            await self._broadcast_event_to_dashboard(
                EventTopics.MUSIC_PLAYBACK_RESUMED,
                raw_payload,
                "music_status",
            )

    async def _handle_music_progress(self, data):
        """Handle music progress updates with enhanced validation"""
        logger.info(f"[WebBridge] [PROGRESS_DEBUG] Received music progress data: {data}")
        
        # Convert progress from 0-100 to 0.0-1.0 for Pydantic validation
        progress_percent = data.get("progress_percent", 0.0)
        progress_normalized = progress_percent / 100.0 if progress_percent > 1.0 else progress_percent
        
        # Build payload that matches WebProgressPayload structure
        pydantic_payload = {
            "operation": "music_playback",
            "progress": progress_normalized,  # 0.0-1.0 range expected by Pydantic
            "status": data.get("status", "playing"),
            "details": f"Position: {data.get('position_sec', 0.0):.1f}s / {data.get('duration_sec', 0.0):.1f}s",
            "timestamp": data.get("timestamp", datetime.now().isoformat())
        }
        
        # Build frontend payload with all the data the dashboard needs
        frontend_payload = {
            "action": "progress",  # Required by frontend for progress identification
            "operation": "music_playback",
            "progress": progress_normalized,
            "status": data.get("status", "playing"),
            "track": data.get("track"),
            "position_sec": data.get("position_sec", 0.0),
            "duration_sec": data.get("duration_sec", 0.0),
            "time_remaining_sec": data.get("time_remaining_sec", 0.0),
            "progress_percent": progress_percent,  # Keep original for frontend compatibility
            "timestamp": data.get("timestamp", datetime.now().isoformat())
        }
        
        logger.info(f"[WebBridge] [PROGRESS_DEBUG] Built Pydantic payload: {pydantic_payload}")
        logger.info(f"[WebBridge] [PROGRESS_DEBUG] Built frontend payload: {frontend_payload}")
        
        # Create fallback payload matching WebProgressPayload structure
        fallback_payload = {
            "operation": "music_playback",
            "progress": 0.0,
            "status": "idle",
            "details": "Progress data unavailable"
        }
        
        # Try the enhanced validation system first
        try:
            success = await self.broadcast_validated_status(
                status_type="progress",
                data=pydantic_payload,  # Use Pydantic-compatible structure
                event_topic=EventTopics.MUSIC_PROGRESS,
                socket_event_name="music_progress",
                fallback_data=fallback_payload
            )
            
            if success:
                logger.info(f"[WebBridge] [PROGRESS_DEBUG] Successfully validated and broadcast progress data")
                return
            else:
                logger.warning(f"[WebBridge] [PROGRESS_DEBUG] Pydantic validation failed, trying direct broadcast")
        except Exception as e:
            logger.error(f"[WebBridge] [PROGRESS_DEBUG] Error in enhanced validation: {e}")
        
        # Fallback: Direct broadcast without Pydantic validation but with frontend-compatible data
        logger.info(f"[WebBridge] [PROGRESS_DEBUG] Using direct broadcast with frontend payload")
        await self._broadcast_event_to_dashboard(
            EventTopics.MUSIC_PROGRESS,
            frontend_payload,  # Send the full frontend-compatible payload
            "music_progress",
        )

    async def _handle_music_queue_updated(self, data):
        """Handle music queue updates"""
        await self._broadcast_event_to_dashboard(
            EventTopics.MUSIC_QUEUE_UPDATED,
            {
                "action": "queue_updated",
                "queue_length": data.get("queue_length", 0),
                "added_track": data.get("added_track")
            },
            "music_queue",
        )

    async def _handle_music_library_updated(self, data):
        """Handle music library updates with track duration information"""
        # Cache the music library data for API endpoint
        tracks = data.get("tracks", {})
        self._music_library_cache = tracks
        
        logger.info(f"[WebBridge] Updated music library cache with {len(tracks)} tracks")
        
        # Broadcast the update to connected dashboard clients
        await self._broadcast_event_to_dashboard(
            EventTopics.MUSIC_LIBRARY_UPDATED,
            {
                "track_count": data.get("track_count", 0),
                "tracks": tracks
            },
            "music_library_updated",
        )

    async def _handle_dj_mode_changed(self, data):
        """Handle DJ mode status changes"""
        is_active = data.get("is_active", False)
        await self._broadcast_event_to_dashboard(
            EventTopics.DJ_MODE_CHANGED,
            {
                "mode": "active" if is_active else "inactive",
                "is_active": is_active,
                "auto_transition": data.get("auto_transition", False),
            },
            "dj_status",
        )

    async def _handle_gpt_commentary_response(self, data):
        """Handle GPT commentary responses - forwards as llm_response to dashboard"""
        self.logger.critical(f"[WebBridge] CRITICAL DEBUG: _handle_gpt_commentary_response called with data: {data}")
        
        # Fix: Use commentary_text field from the actual payload
        commentary_text = data.get("commentary_text", "") or data.get("text", "")
        self.logger.info(f"[WebBridge] Forwarding GPT commentary to dashboard: {commentary_text[:50]}...")
        
        # Forward as 'llm_response' event that DJTab expects
        await self._broadcast_event_to_dashboard(
            EventTopics.GPT_COMMENTARY_RESPONSE,
            {
                "text": commentary_text,
                "context": data.get("context", ""),
                "request_id": data.get("request_id", ""),
                "is_partial": data.get("is_partial", False)
            },
            "llm_response",  # DJTab listens for this event name
        )
        self.logger.critical(f"[WebBridge] CRITICAL DEBUG: Successfully broadcast llm_response event")

    async def _handle_crossfade_started(self, data):
        """Handle crossfade started events"""
        self.logger.info(f"[WebBridge] Forwarding crossfade started to dashboard")
        
        # Forward as 'crossfade_started' event that DJTab expects
        await self._broadcast_event_to_dashboard(
            EventTopics.CROSSFADE_STARTED,
            {
                "from_track": data.get("from_track", ""),
                "to_track": data.get("to_track", ""),
                "duration": data.get("duration", 8.0),
                "crossfade_id": data.get("crossfade_id", "")
            },
            "crossfade_started",  # DJTab listens for this event name
        )

    async def _handle_dj_next_track_selected(self, data):
        """Handle DJ next track selection - updates upcoming queue"""
        self.logger.critical(f"[WebBridge] CRITICAL DEBUG: _handle_dj_next_track_selected called with data: {data}")
        self.logger.info(f"[WebBridge] CRITICAL FIX: DJ next track selected event received!")
        self.logger.info(f"[WebBridge] DJ next track selected: {data.get('track', {}).get('title', 'Unknown')}")
        self.logger.info(f"[WebBridge] Full data payload: {data}")
        
        # Extract track information
        track_info = data.get("track", {})
        
        # Create queue entry format that DJTab expects
        queue_track = {
            "title": track_info.get("title", "Unknown Track"),
            "artist": track_info.get("artist", "Unknown Artist"),
            "duration": self._format_duration(track_info.get("duration", 0)),
            "track_id": track_info.get("track_id", track_info.get("title", ""))
        }
        
        self.logger.critical(f"[WebBridge] CRITICAL DEBUG: Created queue_track: {queue_track}")
        
        # For now, send as a single-item queue (future enhancement: maintain full queue)
        await self._broadcast_event_to_dashboard(
            EventTopics.DJ_NEXT_TRACK_SELECTED,
            {
                "upcoming_queue": [queue_track],
                "next_track": queue_track,
                "queue_length": 1
            },
            "dj_queue_update",  # Custom event name for queue updates
        )
        self.logger.critical(f"[WebBridge] CRITICAL DEBUG: Successfully broadcast dj_queue_update event")
    
    def _format_duration(self, duration_seconds):
        """Format duration from seconds to MM:SS format"""
        if not duration_seconds or duration_seconds <= 0:
            return "Unknown"
        
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        return f"{minutes}:{seconds:02d}"

    async def _handle_llm_response(self, data):
        """Handle LLM response events"""
        await self._broadcast_event_to_dashboard(
            EventTopics.LLM_RESPONSE,
            {"text": data.get("text", ""), "intent": data.get("intent")},
            "llm_response",
        )

    async def _handle_system_error(self, data):
        """Handle system error events"""
        logger.error(f"CantinaOS system error: {data}")
        await self._broadcast_event_to_dashboard(
            EventTopics.SYSTEM_ERROR,
            {
                "error": data.get("error", "Unknown error"),
                "service": data.get("service"),
            },
            "system_error",
        )

    async def _handle_system_mode_change(self, data):
        """Handle system mode changes from YodaModeManagerService"""
        logger.info(f"[WebBridge] System mode change received: {data}")
        await self._broadcast_event_to_dashboard(
            EventTopics.SYSTEM_MODE_CHANGE,
            {
                "current_mode": data.get("new_mode", "IDLE"),  # Backend uses new_mode
                "previous_mode": data.get("old_mode"),  # Backend uses old_mode
                "timestamp": data.get("timestamp", datetime.now().isoformat()),
            },
            "system_mode_change",
        )

    async def _handle_mode_transition(self, data):
        """Handle mode transition events"""
        logger.info(f"Mode transition: {data}")
        await self._broadcast_event_to_dashboard(
            data.get("topic", "MODE_TRANSITION"),
            {
                "status": data.get("status"),
                "from_mode": data.get("from_mode"),
                "to_mode": data.get("to_mode"),
                "timestamp": data.get("timestamp", datetime.now().isoformat()),
            },
            "mode_transition",
        )

    async def _handle_dashboard_log(self, data):
        """Handle dashboard log events from LoggingService with throttling to reduce CLI flooding."""
        try:
            # PHASE 1.2: Filter out DEBUG level logs and high-frequency events to reduce CLI flooding
            log_level = data.get("level", "INFO")
            log_message = data.get("message", "")
            
            # Filter out DEBUG logs entirely (they are rarely needed in dashboard)
            if log_level == "DEBUG":
                return
            
            # Filter out specific high-frequency, noisy log patterns that flood CLI
            noise_patterns = [
                "Progress update:",
                "Timer calculation:",
                "Generated progress data",
                "Successfully emitted MUSIC_PROGRESS",
                "[PROGRESS_DEBUG]",
                "[TIMER_DEBUG]",
                "VLC player.is_playing()",
                "audio_callback",
                "Core Audio"  # Filters VLC Core Audio warnings
            ]
            
            for pattern in noise_patterns:
                if pattern in log_message:
                    return  # Skip broadcasting this log
            
            # CRITICAL FIX: Allow important DJ Mode logs to pass through without throttling
            important_dj_patterns = [
                "Selected next track:",
                "Starting crossfade from",
                "DJ mode activated",
                "DJ mode deactivated",
                "Crossfade",
                "Generated commentary:",
                "Track ending soon"
            ]
            
            is_important_dj_log = any(pattern in log_message for pattern in important_dj_patterns)
            
            # Rate limiting: Only send system logs every 10 seconds instead of continuously
            current_time = time.time()
            throttle_key = f"dashboard_log_{data.get('service', 'unknown')}"
            last_sent = self._event_throttle_state[throttle_key]["last_sent"]
            
            # Allow ERROR, WARNING, CRITICAL, and important DJ logs to pass through immediately
            # Throttle other INFO and other levels to once per 10 seconds per service
            if log_level not in ["ERROR", "WARNING", "CRITICAL"] and not is_important_dj_log:
                if current_time - last_sent < 10.0:  # 10 second throttle for non-important INFO logs
                    return
            
            # Update throttle state only for non-important logs to avoid throttling important ones
            if not is_important_dj_log:
                self._event_throttle_state[throttle_key]["last_sent"] = current_time
            
            # Validate the log payload
            log_data = {
                "timestamp": data.get("timestamp", datetime.now().isoformat()),
                "level": log_level,
                "service": data.get("service", "Unknown"),
                "message": log_message,
                "session_id": data.get("session_id", ""),
                "entry_id": data.get("entry_id", ""),
            }

            # Broadcast to all connected dashboard clients (not CLI)
            await self._broadcast_event_to_dashboard(
                EventTopics.DASHBOARD_LOG, log_data, "system_log"
            )

        except Exception as e:
            logger.error(f"Error handling dashboard log: {e}")

    # Socket.IO Command Handlers (with validation decorators)
    
    @validate_socketio_command("voice_command")
    async def _handle_voice_command(self, sid, validated_command: VoiceCommandSchema):
        """Handle voice commands from dashboard with validation"""
        logger.info(f"Voice command from {sid}: action={validated_command.action}")

        try:
            # Convert to CantinaOS event payload
            event_payload = validated_command.to_cantina_event()
            event_payload["sid"] = sid  # Add socket session ID
            
            # Emit to appropriate CantinaOS event topic
            self._event_bus.emit(
                EventTopics.SYSTEM_SET_MODE_REQUEST,
                event_payload
            )
            
            # Send success acknowledgment to client
            response = BaseWebResponse.success_response(
                message=f"Voice command '{validated_command.action}' processed successfully",
                command_id=validated_command.command_id,
                data={"action": validated_command.action}
            )
            await self._sio.emit(
                "command_ack",
                response.model_dump(mode='json'),
                room=sid,
            )
            
        except Exception as e:
            logger.error(f"Error processing voice command: {e}")
            error_response = BaseWebResponse.error_response(
                message=f"Failed to process voice command: {e}",
                error_code="PROCESSING_ERROR",
                command_id=validated_command.command_id
            )
            await self._sio.emit(
                "command_ack",
                error_response.model_dump(mode='json'),
                room=sid,
            )

    @validate_socketio_command("music_command")
    async def _handle_music_command(self, sid, validated_command: MusicCommandSchema):
        """Handle music commands from dashboard with validation"""
        logger.info(f"Music command from {sid}: action={validated_command.action}")

        try:
            # Convert to CantinaOS event payload
            event_payload = validated_command.to_cantina_event()
            event_payload["sid"] = sid  # Add socket session ID
            
            # Emit to CantinaOS MUSIC_COMMAND event topic
            self._event_bus.emit(
                EventTopics.MUSIC_COMMAND,
                event_payload
            )
            
            # Send success acknowledgment to client
            response = BaseWebResponse.success_response(
                message=f"Music command '{validated_command.action}' processed successfully",
                command_id=validated_command.command_id,
                data={
                    "action": validated_command.action,
                    "track_name": validated_command.track_name,
                    "volume_level": validated_command.volume_level
                }
            )
            await self._sio.emit(
                "command_ack",
                response.model_dump(mode='json'),
                room=sid,
            )
            
        except Exception as e:
            logger.error(f"Error processing music command: {e}")
            error_response = BaseWebResponse.error_response(
                message=f"Failed to process music command: {e}",
                error_code="PROCESSING_ERROR",
                command_id=validated_command.command_id
            )
            await self._sio.emit(
                "command_ack",
                error_response.model_dump(mode='json'),
                room=sid,
            )


    @validate_socketio_command("system_command")
    async def _handle_system_command(self, sid, validated_command: SystemCommandSchema):
        """Handle system commands from dashboard with validation"""
        logger.info(f"System command from {sid}: action={validated_command.action}")

        try:
            # Convert to CantinaOS event payload
            event_payload = validated_command.to_cantina_event()
            event_payload["sid"] = sid  # Add socket session ID
            
            # Get appropriate event topic for this system command
            event_topic = validated_command.get_event_topic()
            
            # Emit to appropriate CantinaOS event topic
            self._event_bus.emit(event_topic, event_payload)
            
            # Send success acknowledgment to client
            response = BaseWebResponse.success_response(
                message=f"System command '{validated_command.action}' processed successfully",
                command_id=validated_command.command_id,
                data={
                    "action": validated_command.action,
                    "mode": validated_command.mode,
                    "event_topic": event_topic
                }
            )
            await self._sio.emit(
                "command_ack",
                response.model_dump(mode='json'),
                room=sid,
            )
            
        except Exception as e:
            logger.error(f"Error processing system command: {e}")
            error_response = BaseWebResponse.error_response(
                message=f"Failed to process system command: {e}",
                error_code="PROCESSING_ERROR",
                command_id=validated_command.command_id
            )
            await self._sio.emit(
                "command_ack",
                error_response.model_dump(mode='json'),
                room=sid,
            )

    async def _handle_voice_recording_start(self, sid, data):
        """Handle voice recording start from VoiceTab Talk button"""
        logger.info(f"Voice recording start from {sid}")
        
        try:
            # Emit MIC_RECORDING_START event directly to CantinaOS (same as MouseInputService)
            self._event_bus.emit(EventTopics.MIC_RECORDING_START, {
                "source": "dashboard",
                "sid": sid
            })
            
            logger.info(f"Emitted MIC_RECORDING_START for VoiceTab from {sid}")
            
        except Exception as e:
            logger.error(f"Error processing voice recording start: {e}")

    async def _handle_voice_recording_stop(self, sid, data):
        """Handle voice recording stop from VoiceTab Stop button"""
        logger.info(f"Voice recording stop from {sid}")
        
        try:
            # Emit MIC_RECORDING_STOP event directly to CantinaOS (same as MouseInputService)
            self._event_bus.emit(EventTopics.MIC_RECORDING_STOP, {
                "source": "dashboard", 
                "sid": sid
            })
            
            logger.info(f"Emitted MIC_RECORDING_STOP for VoiceTab from {sid}")
            
        except Exception as e:
            logger.error(f"Error processing voice recording stop: {e}")
