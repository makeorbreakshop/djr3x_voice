"""
DJ R3X Web Bridge Service

FastAPI service that bridges the web dashboard with CantinaOS event bus.
Provides WebSocket communication and REST API endpoints for dashboard control.
"""

import asyncio
import logging
import json
import sys
import os
import time
from typing import Dict, Any, Optional, Set
from datetime import datetime
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import socketio
import uvicorn
from pydantic import BaseModel

# Add CantinaOS to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'cantina_os'))
from cantina_os.core.event_bus import EventBus
from cantina_os.core.event_topics import EventTopics

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="DJ R3X Web Bridge",
    description="Bridge service connecting web dashboard to CantinaOS",
    version="1.0.0"
)

# Configure CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create Socket.IO server
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=["http://localhost:3000"],
    logger=True,
    engineio_logger=True
)

# Mount Socket.IO to FastAPI
socket_app = socketio.ASGIApp(sio, app)

# Global state
cantina_os_event_bus: Optional[EventBus] = None
cantina_os_connected = False
dashboard_clients: Dict[str, Any] = {}
event_buffer = []  # Buffer events when dashboard not connected
service_status = {}  # Track real service status from CantinaOS
music_library_cache = {}  # Cache music library data from CantinaOS

# Event filtering and throttling
event_throttle_state = defaultdict(lambda: {'last_sent': 0, 'count': 0})
high_frequency_events = {
    EventTopics.AUDIO_CHUNK, 
    EventTopics.VOICE_AUDIO_LEVEL,
    EventTopics.SPEECH_SYNTHESIS_AMPLITUDE
}
throttle_limits = {
    # High frequency events: max 10 per second
    'high_frequency': {'max_per_second': 10, 'events': high_frequency_events},
    # Medium frequency: max 30 per second
    'medium_frequency': {'max_per_second': 30, 'events': {
        EventTopics.TRANSCRIPTION_INTERIM,
        EventTopics.LLM_RESPONSE_CHUNK
    }},
    # Low frequency: no throttling
    'low_frequency': {'max_per_second': 0, 'events': set()}  # 0 = no limit
}

# Pydantic models for API
class SystemStatusResponse(BaseModel):
    status: str
    services: Dict[str, Any]
    uptime: str
    memory_usage: str
    cpu_usage: str

class VoiceCommandRequest(BaseModel):
    action: str  # "start", "stop"
    text: Optional[str] = None

class MusicCommandRequest(BaseModel):
    action: str  # "play", "pause", "stop", "next", "volume"
    track_id: Optional[str] = None
    volume: Optional[int] = None

class DJModeRequest(BaseModel):
    action: str  # "start", "stop", "next"
    auto_transition: Optional[bool] = None
    interval: Optional[int] = None

# Socket.IO Events
@sio.event
async def connect(sid, environ):
    """Handle client connection"""
    logger.info(f"Dashboard client connected: {sid}")
    dashboard_clients[sid] = {
        'connected_at': datetime.now(),
        'subscriptions': []
    }
    
    # Send current system status
    await sio.emit('system_status', {
        'cantina_os_connected': cantina_os_connected,
        'services': get_service_status(),
        'timestamp': datetime.now().isoformat()
    }, room=sid)
    
    # Send buffered events if any
    for event in event_buffer[-10:]:  # Send last 10 events
        await sio.emit('event_replay', event, room=sid)

@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    logger.info(f"Dashboard client disconnected: {sid}")
    if sid in dashboard_clients:
        del dashboard_clients[sid]

@sio.event
async def subscribe_events(sid, data):
    """Handle event subscription requests"""
    event_types = data.get('events', [])
    if sid in dashboard_clients:
        dashboard_clients[sid]['subscriptions'] = event_types
        dashboard_clients[sid]['filter_level'] = data.get('filter_level', 'all')  # all, critical, minimal
        logger.info(f"Client {sid} subscribed to: {event_types} with filter: {data.get('filter_level', 'all')}")

@sio.event
async def voice_command(sid, data):
    """Handle voice commands from dashboard"""
    logger.info(f"Voice command from {sid}: {data}")
    
    if cantina_os_event_bus and cantina_os_connected:
        # Forward to CantinaOS event bus
        try:
            if data.get('action') == 'start':
                cantina_os_event_bus.emit(EventTopics.MIC_RECORDING_START, {
                    'source': 'web_dashboard',
                    'sid': sid
                })
            elif data.get('action') == 'stop':
                cantina_os_event_bus.emit(EventTopics.MIC_RECORDING_STOP, {
                    'source': 'web_dashboard',
                    'sid': sid
                })
        except Exception as e:
            logger.error(f"Error forwarding voice command to CantinaOS: {e}")
            await sio.emit('error', {'message': f'Failed to execute voice command: {e}'}, room=sid)
    else:
        # Fallback to simulation when not connected
        if data.get('action') == 'start':
            await sio.emit('voice_status', {
                'status': 'recording',
                'timestamp': datetime.now().isoformat()
            }, room=sid)
        elif data.get('action') == 'stop':
            await sio.emit('voice_status', {
                'status': 'processing',
                'timestamp': datetime.now().isoformat()
            }, room=sid)
            
            # Simulate transcription after delay
            await asyncio.sleep(2)
            await sio.emit('transcription_update', {
                'text': 'Hey DJ R3X, play some cantina music',
                'confidence': 0.95,
                'final': True,
                'timestamp': datetime.now().isoformat()
            }, room=sid)

@sio.event
async def music_command(sid, data):
    """Handle music commands from dashboard"""
    logger.info(f"Music command from {sid}: {data}")
    
    if cantina_os_event_bus and cantina_os_connected:
        # Forward to CantinaOS MusicControllerService
        try:
            action = data.get('action')
            if action == 'play':
                # Use MUSIC_COMMAND event with proper payload for MusicControllerService
                cantina_os_event_bus.emit(EventTopics.MUSIC_COMMAND, {
                    'action': 'play',
                    'song_query': data.get('track_name', data.get('track_id', '')),
                    'source': 'web_dashboard',
                    'conversation_id': None
                })
            elif action == 'pause':
                cantina_os_event_bus.emit(EventTopics.MUSIC_COMMAND, {
                    'action': 'stop',
                    'source': 'web_dashboard',
                    'conversation_id': None
                })
            elif action == 'stop':
                cantina_os_event_bus.emit(EventTopics.MUSIC_COMMAND, {
                    'action': 'stop',
                    'source': 'web_dashboard',
                    'conversation_id': None
                })
            elif action == 'volume':
                # For volume control, we'll need to implement this in MusicControllerService
                cantina_os_event_bus.emit(EventTopics.MUSIC_COMMAND, {
                    'command': 'volume',
                    'volume': data.get('volume'),
                    'source': 'web_dashboard'
                })
            elif action == 'next':
                cantina_os_event_bus.emit(EventTopics.DJ_NEXT_TRACK, {
                    'source': 'web_dashboard'
                })
        except Exception as e:
            logger.error(f"Error forwarding music command to CantinaOS: {e}")
            await sio.emit('error', {'message': f'Failed to execute music command: {e}'}, room=sid)
    else:
        # Fallback simulation
        await sio.emit('music_status', {
            'action': data.get('action'),
            'track_id': data.get('track_id'),
            'track_name': data.get('track_name'),
            'volume': data.get('volume'),
            'timestamp': datetime.now().isoformat()
        })

@sio.event
async def dj_command(sid, data):
    """Handle DJ mode commands from dashboard"""
    logger.info(f"DJ command from {sid}: {data}")
    
    if cantina_os_event_bus and cantina_os_connected:
        # Forward to CantinaOS DJ services
        try:
            action = data.get('action')
            if action == 'start':
                cantina_os_event_bus.emit(EventTopics.DJ_MODE_START, {
                    'auto_transition': data.get('auto_transition', True),
                    'source': 'web_dashboard'
                })
            elif action == 'stop':
                cantina_os_event_bus.emit(EventTopics.DJ_MODE_STOP, {
                    'source': 'web_dashboard'
                })
            elif action == 'next':
                cantina_os_event_bus.emit(EventTopics.DJ_NEXT_TRACK, {
                    'source': 'web_dashboard'
                })
        except Exception as e:
            logger.error(f"Error forwarding DJ command to CantinaOS: {e}")
            await sio.emit('error', {'message': f'Failed to execute DJ command: {e}'}, room=sid)
    else:
        # Fallback simulation
        await sio.emit('dj_status', {
            'mode': 'active' if data.get('action') == 'start' else 'inactive',
            'auto_transition': data.get('auto_transition', True),
            'timestamp': datetime.now().isoformat()
        })

# REST API Endpoints
@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "DJ R3X Web Bridge",
        "status": "running",
        "cantina_os_connected": cantina_os_connected,
        "dashboard_clients": len(dashboard_clients),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/system/status", response_model=SystemStatusResponse)
async def get_system_status():
    """Get current system status"""
    return SystemStatusResponse(
        status="offline" if not cantina_os_connected else "online",
        services=get_service_status(),
        uptime="00:00:00",
        memory_usage="0 MB",
        cpu_usage="0%"
    )

@app.post("/api/voice/command")
async def voice_command_api(command: VoiceCommandRequest):
    """Execute voice command via REST API"""
    # TODO: Forward to CantinaOS
    logger.info(f"Voice command via API: {command}")
    return {"status": "accepted", "command": command.dict()}

@app.post("/api/music/command")
async def music_command_api(command: MusicCommandRequest):
    """Execute music command via REST API"""
    # TODO: Forward to CantinaOS
    logger.info(f"Music command via API: {command}")
    return {"status": "accepted", "command": command.dict()}

@app.post("/api/dj/command")
async def dj_command_api(command: DJModeRequest):
    """Execute DJ mode command via REST API"""
    # TODO: Forward to CantinaOS
    logger.info(f"DJ command via API: {command}")
    return {"status": "accepted", "command": command.dict()}

@app.get("/api/music/library")
async def get_music_library():
    """Get music library from CantinaOS"""
    try:
        if cantina_os_event_bus and cantina_os_connected and music_library_cache:
            # Use the cached music library data from CantinaOS
            tracks = []
            
            for i, (track_name, track_data) in enumerate(music_library_cache.items()):
                # Parse artist and title from track name or use provided metadata
                if track_data.get('artist') and track_data.get('title'):
                    artist = track_data['artist']
                    title = track_data['title']
                elif " - " in track_name:
                    artist, title = track_name.split(" - ", 1)
                else:
                    artist = "Cantina Band"
                    title = track_data.get('title') or track_name
                
                # Use duration from CantinaOS data (already calculated)
                duration_seconds = track_data.get('duration', 180)  # Default 3 minutes if missing
                
                # Convert duration to MM:SS format
                duration_str = f"{int(duration_seconds // 60)}:{int(duration_seconds % 60):02d}"
                
                tracks.append({
                    "id": str(i + 1),
                    "title": title.strip(),
                    "artist": artist.strip(),
                    "duration": duration_str,
                    "file": track_data.get('name', track_name),
                    "path": track_data.get('path', '')
                })
            
            return {"tracks": tracks}
        else:
            # Fallback mock data when not connected
            return {
                "tracks": [
                    {
                        "id": "1",
                        "title": "Cantina Band",
                        "artist": "Figrin D'an and the Modal Nodes",
                        "duration": "2:47",
                        "file": "cantina_band.mp3"
                    },
                    {
                        "id": "2", 
                        "title": "Duel of the Fates",
                        "artist": "John Williams",
                        "duration": "4:14",
                        "file": "duel_of_fates.mp3"
                    }
                ]
            }
    except Exception as e:
        logger.error(f"Error fetching music library: {e}")
        return {"tracks": [], "error": str(e)}

@app.get("/api/logs")
async def get_logs():
    """Get recent system logs"""
    # TODO: Fetch from CantinaOS
    return {
        "logs": [
            {
                "timestamp": datetime.now().isoformat(),
                "level": "INFO",
                "service": "WebBridge",
                "message": "Bridge service started"
            }
        ]
    }

# Helper functions
def get_service_status():
    """Get current service status from CantinaOS or fallback to mock data"""
    if cantina_os_connected and service_status:
        return service_status
    else:
        # Fallback mock data when not connected
        return {
            "deepgram_direct_mic": {"status": "offline", "uptime": "0:00:00"},
            "gpt_service": {"status": "offline", "uptime": "0:00:00"},
            "elevenlabs_service": {"status": "offline", "uptime": "0:00:00"},
            "music_controller": {"status": "offline", "uptime": "0:00:00"},
            "eye_light_controller": {"status": "offline", "uptime": "0:00:00"},
            "brain_service": {"status": "offline", "uptime": "0:00:00"},
        }

async def connect_to_cantina_os():
    """Connect to CantinaOS event bus"""
    global cantina_os_event_bus, cantina_os_connected, service_status
    logger.info("Attempting to connect to CantinaOS...")
    
    try:
        # Create event bus instance (shared with CantinaOS)
        cantina_os_event_bus = EventBus()
        
        # Subscribe to key events for dashboard monitoring
        cantina_os_event_bus.on(EventTopics.SERVICE_STATUS_UPDATE, handle_service_status_update)
        cantina_os_event_bus.on(EventTopics.TRANSCRIPTION_FINAL, handle_transcription_final)
        cantina_os_event_bus.on(EventTopics.TRANSCRIPTION_INTERIM, handle_transcription_interim)
        cantina_os_event_bus.on(EventTopics.VOICE_LISTENING_STARTED, handle_voice_listening_started)
        cantina_os_event_bus.on(EventTopics.VOICE_LISTENING_STOPPED, handle_voice_listening_stopped)
        cantina_os_event_bus.on(EventTopics.MUSIC_PLAYBACK_STARTED, handle_music_playback_started)
        cantina_os_event_bus.on(EventTopics.MUSIC_PLAYBACK_STOPPED, handle_music_playback_stopped)
        cantina_os_event_bus.on(EventTopics.MUSIC_LIBRARY_UPDATED, handle_music_library_updated)
        cantina_os_event_bus.on(EventTopics.DJ_MODE_CHANGED, handle_dj_mode_changed)
        cantina_os_event_bus.on(EventTopics.LLM_RESPONSE, handle_llm_response)
        cantina_os_event_bus.on(EventTopics.SYSTEM_ERROR, handle_system_error)
        
        cantina_os_connected = True
        logger.info("Successfully connected to CantinaOS event bus")
        
        # Broadcast connection status to dashboard clients
        if dashboard_clients:
            await sio.emit('cantina_os_status', {
                'connected': True,
                'timestamp': datetime.now().isoformat()
            })
            
    except Exception as e:
        cantina_os_connected = False
        logger.error(f"Failed to connect to CantinaOS: {e}")
        logger.warning("Running in standalone mode with mock data")

# Event filtering and throttling functions
def should_throttle_event(event_topic: str) -> bool:
    """Check if an event should be throttled based on frequency limits"""
    current_time = time.time()
    
    # Find the throttle category for this event
    throttle_category = None
    for category, config in throttle_limits.items():
        if event_topic in config['events']:
            throttle_category = category
            break
    
    # If no throttling rules apply, allow the event
    if not throttle_category or throttle_limits[throttle_category]['max_per_second'] == 0:
        return False
    
    # Check throttle state
    state = event_throttle_state[event_topic]
    max_per_second = throttle_limits[throttle_category]['max_per_second']
    
    # Reset counter if more than a second has passed
    if current_time - state['last_sent'] >= 1.0:
        state['count'] = 0
        state['last_sent'] = current_time
    
    # Check if we've exceeded the limit
    if state['count'] >= max_per_second:
        return True
    
    # Increment counter and allow event
    state['count'] += 1
    return False

def should_filter_event_for_client(event_topic: str, client_data: Dict[str, Any]) -> bool:
    """Check if an event should be filtered for a specific client based on their subscription preferences"""
    filter_level = client_data.get('filter_level', 'all')
    subscriptions = client_data.get('subscriptions', [])
    
    # If client has specific subscriptions, only send those events
    if subscriptions and event_topic not in subscriptions:
        return True
    
    # Apply filter level
    if filter_level == 'minimal':
        critical_events = {
            EventTopics.SYSTEM_ERROR,
            EventTopics.SERVICE_STATUS_UPDATE,
            EventTopics.TRANSCRIPTION_FINAL,
            EventTopics.DJ_MODE_CHANGED,
            EventTopics.MUSIC_PLAYBACK_STARTED,
            EventTopics.MUSIC_PLAYBACK_STOPPED
        }
        return event_topic not in critical_events
    
    elif filter_level == 'critical':
        important_events = {
            EventTopics.SYSTEM_ERROR,
            EventTopics.SERVICE_STATUS_UPDATE,
            EventTopics.TRANSCRIPTION_FINAL,
            EventTopics.TRANSCRIPTION_INTERIM,
            EventTopics.VOICE_LISTENING_STARTED,
            EventTopics.VOICE_LISTENING_STOPPED,
            EventTopics.DJ_MODE_CHANGED,
            EventTopics.MUSIC_PLAYBACK_STARTED,
            EventTopics.MUSIC_PLAYBACK_STOPPED,
            EventTopics.LLM_RESPONSE
        }
        return event_topic not in important_events
    
    # 'all' filter level or unknown - don't filter
    return False

async def broadcast_event_to_dashboard(event_topic: str, data: Dict[str, Any], event_name: str = None):
    """Broadcast an event to dashboard clients with filtering and throttling"""
    if not dashboard_clients:
        return
    
    # Apply global throttling
    if should_throttle_event(event_topic):
        return
    
    # Prepare event data
    event_data = {
        'topic': event_topic,
        'data': data,
        'timestamp': datetime.now().isoformat()
    }
    
    # Send to each client based on their subscription and filter preferences
    for sid, client_data in dashboard_clients.items():
        if not should_filter_event_for_client(event_topic, client_data):
            try:
                await sio.emit(event_name or 'cantina_event', event_data, room=sid)
            except Exception as e:
                logger.error(f"Error broadcasting event to client {sid}: {e}")

# Event handlers for CantinaOS events
async def handle_service_status_update(data):
    """Handle service status updates from CantinaOS"""
    service_name = data.get('service_name', 'unknown')
    status = data.get('status', 'unknown')
    
    service_status[service_name] = {
        'status': status,
        'uptime': data.get('uptime', '0:00:00'),
        'last_update': datetime.now().isoformat()
    }
    
    # Broadcast to dashboard clients with filtering
    await broadcast_event_to_dashboard(
        EventTopics.SERVICE_STATUS_UPDATE,
        {
            'service': service_name,
            'status': status,
            'data': service_status[service_name]
        },
        'service_status_update'
    )

async def handle_transcription_final(data):
    """Handle final transcription results"""
    await broadcast_event_to_dashboard(
        EventTopics.TRANSCRIPTION_FINAL,
        {
            'text': data.get('text', ''),
            'confidence': data.get('confidence', 0.0),
            'final': True
        },
        'transcription_update'
    )

async def handle_transcription_interim(data):
    """Handle interim transcription results"""
    await broadcast_event_to_dashboard(
        EventTopics.TRANSCRIPTION_INTERIM,
        {
            'text': data.get('text', ''),
            'confidence': data.get('confidence', 0.0),
            'final': False
        },
        'transcription_update'
    )

async def handle_voice_listening_started(data):
    """Handle voice listening started event"""
    await broadcast_event_to_dashboard(
        EventTopics.VOICE_LISTENING_STARTED,
        {'status': 'recording'},
        'voice_status'
    )

async def handle_voice_listening_stopped(data):
    """Handle voice listening stopped event"""
    await broadcast_event_to_dashboard(
        EventTopics.VOICE_LISTENING_STOPPED,
        {'status': 'processing'},
        'voice_status'
    )

async def handle_music_playback_started(data):
    """Handle music playback started event"""
    await broadcast_event_to_dashboard(
        EventTopics.MUSIC_PLAYBACK_STARTED,
        {
            'action': 'started',
            'track': data.get('track', {}),
            'source': data.get('source', 'unknown'),
            'mode': data.get('mode', 'INTERACTIVE'),
            'start_timestamp': data.get('start_timestamp', time.time()),
            'duration': data.get('duration')
        },
        'music_status'
    )

async def handle_music_playback_stopped(data):
    """Handle music playback stopped event"""
    await broadcast_event_to_dashboard(
        EventTopics.MUSIC_PLAYBACK_STOPPED,
        {
            'action': 'stopped',
            'track_name': data.get('track_name')
        },
        'music_status'
    )

async def handle_music_library_updated(data):
    """Handle music library updated event"""
    global music_library_cache
    # Store the music library data from CantinaOS
    music_library_cache = data.get('tracks', {})
    
    await broadcast_event_to_dashboard(
        EventTopics.MUSIC_LIBRARY_UPDATED,
        {
            'track_count': data.get('track_count', 0),
            'tracks': data.get('tracks', {})
        },
        'music_library_updated'
    )

async def handle_dj_mode_changed(data):
    """Handle DJ mode status changes"""
    await broadcast_event_to_dashboard(
        EventTopics.DJ_MODE_CHANGED,
        {
            'mode': data.get('mode', 'inactive'),
            'auto_transition': data.get('auto_transition', False)
        },
        'dj_status'
    )

async def handle_llm_response(data):
    """Handle LLM response events"""
    await broadcast_event_to_dashboard(
        EventTopics.LLM_RESPONSE,
        {
            'text': data.get('text', ''),
            'intent': data.get('intent')
        },
        'llm_response'
    )

async def handle_system_error(data):
    """Handle system error events"""
    logger.error(f"CantinaOS system error: {data}")
    await broadcast_event_to_dashboard(
        EventTopics.SYSTEM_ERROR,
        {
            'error': data.get('error', 'Unknown error'),
            'service': data.get('service')
        },
        'system_error'
    )

async def periodic_status_broadcast():
    """Periodically broadcast status to all connected clients"""
    while True:
        if dashboard_clients:
            status_data = {
                'cantina_os_connected': cantina_os_connected,
                'services': get_service_status(),
                'timestamp': datetime.now().isoformat()
            }
            await sio.emit('system_status', status_data)
        
        await asyncio.sleep(5)  # Broadcast every 5 seconds

@app.on_event("startup")
async def startup_event():
    """Initialize bridge service"""
    logger.info("Starting DJ R3X Web Bridge Service")
    
    # Start background tasks
    asyncio.create_task(connect_to_cantina_os())
    asyncio.create_task(periodic_status_broadcast())

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down DJ R3X Web Bridge Service")

if __name__ == "__main__":
    uvicorn.run(
        "main:socket_app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )