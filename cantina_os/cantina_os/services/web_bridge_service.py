"""
DJ R3X Web Bridge Service

CantinaOS service that provides web dashboard connectivity via FastAPI and Socket.IO.
Bridges the web dashboard with CantinaOS event bus for real-time monitoring and control.
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

# Configure logging
logger = logging.getLogger(__name__)


class WebBridgeService(BaseService):
    """
    Web Bridge Service for DJ R3X Dashboard

    Provides FastAPI endpoints and Socket.IO connectivity for the web dashboard.
    Bridges web client communication with CantinaOS event bus.
    """

    def __init__(self, event_bus, config: Dict[str, Any] = None):
        """Initialize the Web Bridge Service."""
        super().__init__(
            service_name="web_bridge",
            event_bus=event_bus,
            logger=logging.getLogger("cantina_os.services.web_bridge"),
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
        self._logger.info("Starting DJ R3X Web Bridge Service")

        # Create FastAPI application
        self._create_fastapi_app()

        # Create Socket.IO server
        self._create_socketio_server()

        # Subscribe to CantinaOS events
        self._subscribe_to_events()

        # Start the web server
        await self._start_web_server()

        # Start background tasks
        asyncio.create_task(self._periodic_status_broadcast())

        # Request status from all services that may have started before us
        self._event_bus.emit(
            EventTopics.SERVICE_STATUS_REQUEST,
            {"source": "web_bridge", "timestamp": datetime.now().isoformat()},
        )

        self._status = ServiceStatus.RUNNING
        await self._emit_status(
            ServiceStatus.RUNNING, "Web Bridge Service started successfully"
        )
        self._logger.info("Web Bridge Service started successfully")

    async def _stop(self) -> None:
        """Stop the web bridge service."""
        self._logger.info("Stopping DJ R3X Web Bridge Service")

        if self._server:
            self._server.should_exit = True
            await asyncio.sleep(1)  # Give server time to cleanup

        self._status = ServiceStatus.STOPPED
        await self._emit_status(
            ServiceStatus.RUNNING, "Web Bridge Service started successfully"
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
                # Get music from assets directory
                music_dir = os.path.join(
                    os.path.dirname(__file__), "..", "assets", "music"
                )
                tracks = []

                if os.path.exists(music_dir):
                    for i, filename in enumerate(os.listdir(music_dir)):
                        if filename.endswith((".mp3", ".wav", ".m4a")):
                            name, _ = os.path.splitext(filename)
                            if " - " in name:
                                artist, title = name.split(" - ", 1)
                            else:
                                artist = "Cantina Band"
                                title = name

                            tracks.append(
                                {
                                    "id": str(i + 1),
                                    "title": title.strip(),
                                    "artist": artist.strip(),
                                    "duration": "3:00",
                                    "file": filename,
                                    "path": os.path.join(music_dir, filename),
                                }
                            )

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

        @self._sio.event
        async def voice_command(sid, data):
            """Handle voice commands from dashboard"""
            logger.info(f"Voice command from {sid}: {data}")

            try:
                if data.get("action") == "start":
                    # Follow proper CantinaOS engagement flow:
                    # Dashboard → SYSTEM_SET_MODE_REQUEST (INTERACTIVE) → YodaModeManagerService → VOICE_LISTENING_STARTED
                    self._event_bus.emit(
                        EventTopics.SYSTEM_SET_MODE_REQUEST,
                        {"mode": "INTERACTIVE", "source": "web_dashboard", "sid": sid},
                    )
                elif data.get("action") == "stop":
                    # Return to AMBIENT mode when stopping voice recording
                    self._event_bus.emit(
                        EventTopics.SYSTEM_SET_MODE_REQUEST,
                        {"mode": "AMBIENT", "source": "web_dashboard", "sid": sid},
                    )
            except Exception as e:
                logger.error(f"Error forwarding voice command: {e}")
                await self._sio.emit(
                    "error",
                    {"message": f"Failed to execute voice command: {e}"},
                    room=sid,
                )

        @self._sio.event
        async def music_command(sid, data):
            """Handle music commands from dashboard"""
            logger.info(f"Music command from {sid}: {data}")

            try:
                action = data.get("action")
                if action == "play":
                    self._event_bus.emit(
                        EventTopics.MUSIC_COMMAND,
                        {
                            "action": "play",
                            "song_query": data.get(
                                "track_name", data.get("track_id", "")
                            ),
                            "source": "web_dashboard",
                            "conversation_id": None,
                        },
                    )
                elif action in ["pause", "stop"]:
                    self._event_bus.emit(
                        EventTopics.MUSIC_COMMAND,
                        {
                            "action": "stop",
                            "source": "web_dashboard",
                            "conversation_id": None,
                        },
                    )
                elif action == "next":
                    self._event_bus.emit(
                        EventTopics.DJ_NEXT_TRACK, {"source": "web_dashboard"}
                    )
            except Exception as e:
                logger.error(f"Error forwarding music command: {e}")
                await self._sio.emit(
                    "error",
                    {"message": f"Failed to execute music command: {e}"},
                    room=sid,
                )

        @self._sio.event
        async def dj_command(sid, data):
            """Handle DJ mode commands from dashboard"""
            logger.info(f"DJ command from {sid}: {data}")

            try:
                action = data.get("action")
                if action == "start":
                    self._event_bus.emit(
                        EventTopics.DJ_COMMAND,
                        {
                            "command": "dj start",
                            "auto_transition": data.get("auto_transition", True),
                            "source": "web_dashboard",
                        },
                    )
                elif action == "stop":
                    self._event_bus.emit(
                        EventTopics.DJ_COMMAND,
                        {"command": "dj stop", "source": "web_dashboard"},
                    )
                elif action == "next":
                    self._event_bus.emit(
                        EventTopics.DJ_NEXT_TRACK, {"source": "web_dashboard"}
                    )
            except Exception as e:
                logger.error(f"Error forwarding DJ command: {e}")
                await self._sio.emit(
                    "error", {"message": f"Failed to execute DJ command: {e}"}, room=sid
                )

        @self._sio.event
        async def system_command(sid, data):
            """Handle system commands from dashboard"""
            logger.info(f"System command from {sid}: {data}")

            try:
                action = data.get("action")
                if action == "set_mode":
                    # Translate web dashboard mode requests to proper CantinaOS events
                    mode = data.get("mode", "").upper()
                    if mode in ["IDLE", "AMBIENT", "INTERACTIVE"]:
                        self._event_bus.emit(
                            EventTopics.SYSTEM_SET_MODE_REQUEST,
                            {"mode": mode, "source": "web_dashboard", "sid": sid},
                        )
                    else:
                        raise ValueError(f"Invalid mode: {mode}")
                elif action == "restart":
                    self._event_bus.emit(
                        EventTopics.SYSTEM_SHUTDOWN_REQUESTED,
                        {"restart": True, "source": "web_dashboard"},
                    )
                elif action == "refresh_config":
                    # Emit config refresh request
                    self._event_bus.emit(
                        "CONFIG_REFRESH_REQUEST", {"source": "web_dashboard"}
                    )
            except Exception as e:
                logger.error(f"Error forwarding system command: {e}")
                await self._sio.emit(
                    "error",
                    {"message": f"Failed to execute system command: {e}"},
                    room=sid,
                )

    def _subscribe_to_events(self) -> None:
        """Subscribe to CantinaOS events for dashboard monitoring."""
        self._event_bus.on(
            EventTopics.SERVICE_STATUS_UPDATE, self._handle_service_status_update
        )
        self._event_bus.on(
            EventTopics.TRANSCRIPTION_FINAL, self._handle_transcription_final
        )
        self._event_bus.on(
            EventTopics.TRANSCRIPTION_INTERIM, self._handle_transcription_interim
        )
        self._event_bus.on(
            EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started
        )
        self._event_bus.on(
            EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped
        )
        self._event_bus.on(
            EventTopics.MUSIC_PLAYBACK_STARTED, self._handle_music_playback_started
        )
        self._event_bus.on(
            EventTopics.MUSIC_PLAYBACK_STOPPED, self._handle_music_playback_stopped
        )
        self._event_bus.on(EventTopics.DJ_MODE_CHANGED, self._handle_dj_mode_changed)
        self._event_bus.on(EventTopics.LLM_RESPONSE, self._handle_llm_response)
        self._event_bus.on(EventTopics.SYSTEM_ERROR, self._handle_system_error)
        self._event_bus.on(EventTopics.DASHBOARD_LOG, self._handle_dashboard_log)

        # System mode events - critical for dashboard state synchronization
        self._event_bus.on(
            EventTopics.SYSTEM_MODE_CHANGE, self._handle_system_mode_change
        )
        self._event_bus.on("MODE_TRANSITION_STARTED", self._handle_mode_transition)
        self._event_bus.on("MODE_TRANSITION_COMPLETE", self._handle_mode_transition)

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
        self, event_topic: str, data: Dict[str, Any], event_name: str = None
    ):
        """Broadcast an event to dashboard clients."""
        if not self._dashboard_clients or not self._sio:
            return

        event_data = {
            "topic": event_topic,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

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
        """Handle voice listening started event"""
        await self._broadcast_event_to_dashboard(
            EventTopics.VOICE_LISTENING_STARTED, {"status": "recording"}, "voice_status"
        )

    async def _handle_voice_listening_stopped(self, data):
        """Handle voice listening stopped event"""
        await self._broadcast_event_to_dashboard(
            EventTopics.VOICE_LISTENING_STOPPED,
            {"status": "processing"},
            "voice_status",
        )

    async def _handle_music_playback_started(self, data):
        """Handle music playback started event"""
        await self._broadcast_event_to_dashboard(
            EventTopics.MUSIC_PLAYBACK_STARTED,
            {
                "action": "started",
                "track": data.get("track", {}),
                "source": data.get("source", "unknown"),
                "mode": data.get("mode", "INTERACTIVE"),
            },
            "music_status",
        )

    async def _handle_music_playback_stopped(self, data):
        """Handle music playback stopped event"""
        await self._broadcast_event_to_dashboard(
            EventTopics.MUSIC_PLAYBACK_STOPPED,
            {"action": "stopped", "track_name": data.get("track_name")},
            "music_status",
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
        logger.info(f"System mode changed: {data}")
        await self._broadcast_event_to_dashboard(
            EventTopics.SYSTEM_MODE_CHANGE,
            {
                "current_mode": data.get("mode", "IDLE"),
                "previous_mode": data.get("previous_mode"),
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
        """Handle dashboard log events from LoggingService."""
        try:
            # Validate the log payload
            log_data = {
                "timestamp": data.get("timestamp", datetime.now().isoformat()),
                "level": data.get("level", "INFO"),
                "service": data.get("service", "Unknown"),
                "message": data.get("message", ""),
                "session_id": data.get("session_id", ""),
                "entry_id": data.get("entry_id", ""),
            }

            # Broadcast to all connected dashboard clients
            await self._broadcast_event_to_dashboard(
                EventTopics.DASHBOARD_LOG, log_data, "system_log"
            )

        except Exception as e:
            logger.error(f"Error handling dashboard log: {e}")
