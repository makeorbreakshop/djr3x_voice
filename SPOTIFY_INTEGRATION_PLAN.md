# Spotify Integration Implementation Plan

## Summary
Integrating Spotify Connect as an alternative music source to local files, using a backend abstraction pattern.

## Completed Steps ✅
1. Created `MusicBackend` abstract class
2. Created `LocalMusicBackend` (VLC-based)
3. Created `SpotifyMusicBackend` (Spotify Connect API)
4. Added `MUSIC_SOURCE_CHANGED` event topic
5. Added `MusicSourceChangedPayload` payload class

## Next Steps

### 1. Update MusicControllerService Config
```python
class MusicControllerConfig(BaseModel):
    music_dir: str = "assets/music"
    normal_volume: int = 70
    ducking_volume: int = 50
    crossfade_duration_ms: int = 3000
    crossfade_steps: int = 50
    track_ending_threshold_sec: int = 30

    # NEW: Spotify configuration
    enable_spotify: bool = False
    spotify_client_id: Optional[str] = None
    spotify_client_secret: Optional[str] = None
    spotify_redirect_uri: str = "http://localhost:8080/callback"
    spotify_device_name: Optional[str] = None
    default_source: str = "local"  # "local" or "spotify"
```

### 2. Update MusicControllerService __init__
```python
def __init__(self, event_bus, config=None):
    # ... existing init ...

    # NEW: Backend system
    self.backends = {}
    self.active_source = "local"

    # NEW: Separate libraries per source
    self.libraries = {
        "local": {},
        "spotify": {}
    }

    # Keep existing for backwards compatibility
    self.tracks = self.libraries["local"]  # Alias to active library
```

### 3. Update start() method
```python
async def start(self):
    # Initialize local backend
    local_backend = LocalMusicBackend(self.vlc_instance, self.logger)
    await local_backend.initialize()
    self.backends["local"] = local_backend

    # Initialize Spotify backend if configured
    if self._config.enable_spotify:
        try:
            spotify_config = {
                "client_id": self._config.spotify_client_id,
                "client_secret": self._config.spotify_client_secret,
                "redirect_uri": self._config.spotify_redirect_uri,
                "device_name": self._config.spotify_device_name
            }
            spotify_backend = SpotifyMusicBackend(spotify_config, self.logger)
            if await spotify_backend.initialize():
                self.backends["spotify"] = spotify_backend
        except Exception as e:
            self.logger.warning(f"Spotify backend initialization failed: {e}")

    # Load libraries
    await self._load_local_library()
    if "spotify" in self.backends:
        await self._load_spotify_library()

    # Set active source
    self.active_source = self._config.default_source
```

### 4. Add source switching methods
```python
async def _switch_source(self, new_source: str):
    """Switch to different music source."""
    if new_source not in self.backends:
        return False

    # Stop current backend
    await self.backends[self.active_source].stop_playback()

    # Switch
    old_source = self.active_source
    self.active_source = new_source
    self.tracks = self.libraries[new_source]  # Update alias

    # Emit event
    payload = MusicSourceChangedPayload(
        previous_source=old_source,
        current_source=new_source,
        available_sources=list(self.backends.keys())
    )
    await self.emit(EventTopics.MUSIC_SOURCE_CHANGED, payload.model_dump())

    return True
```

### 5. Update _handle_music_command
```python
async def _handle_music_command(self, payload):
    action = payload.get("action")

    if action == "source":
        # NEW: Handle source switching
        source_name = payload.get("source_name")
        if source_name == "status":
            await self._show_source_status()
        else:
            await self._switch_source(source_name)

    elif action == "play":
        # Use active backend
        await self._play_from_active_source(payload)

    # ... other actions
```

### 6. Add Spotify library loading
```python
async def _load_spotify_library(self):
    """Load user's Spotify library."""
    backend = self.backends.get("spotify")
    if not backend or not backend.sp:
        return

    try:
        # Load user's playlists
        playlists = await asyncio.to_thread(
            backend.sp.current_user_playlists,
            limit=50
        )

        for playlist in playlists["items"]:
            tracks = await asyncio.to_thread(
                backend.sp.playlist_tracks,
                playlist["id"]
            )

            for item in tracks["items"]:
                track_data = item["track"]
                track_name = f"{track_data['name']} - {track_data['artists'][0]['name']}"

                self.libraries["spotify"][track_name] = MusicTrack(
                    name=track_name,
                    path=track_data["uri"],  # "spotify:track:xxx"
                    duration=track_data["duration_ms"] / 1000.0,
                    artist=track_data["artists"][0]["name"],
                    album=track_data["album"]["name"],
                    provider="spotify"
                )

        self.logger.info(f"Loaded {len(self.libraries['spotify'])} Spotify tracks")

    except Exception as e:
        self.logger.error(f"Failed to load Spotify library: {e}")
```

### 7. Update crossfade method
```python
async def _crossfade_to_track(self, next_track, source="dj", duration_sec=None):
    """Crossfade using active backend."""
    duration = duration_sec or (self._config.crossfade_duration_ms / 1000.0)

    backend = self.backends[self.active_source]
    await backend.crossfade_to_track(next_track, duration)

    self.current_track = next_track
    # ... emit events
```

### 8. Add commands to CommandDispatcherService
```python
# In command registration
commands = {
    "music source local": {
        "topic": EventTopics.MUSIC_COMMAND,
        "payload": {"action": "source", "source_name": "local"}
    },
    "music source spotify": {
        "topic": EventTopics.MUSIC_COMMAND,
        "payload": {"action": "source", "source_name": "spotify"}
    },
    "music source status": {
        "topic": EventTopics.MUSIC_COMMAND,
        "payload": {"action": "source", "source_name": "status"}
    }
}
```

### 9. Update BrainService
```python
class BrainService:
    def __init__(self, ...):
        self._current_music_source = "local"

    async def _start(self):
        await self.subscribe(
            EventTopics.MUSIC_SOURCE_CHANGED,
            self._handle_music_source_changed
        )

    async def _handle_music_source_changed(self, payload):
        self._current_music_source = payload["current_source"]
        self.logger.info(f"DJ mode will use: {self._current_music_source}")
```

### 10. Environment Configuration
```bash
# .env additions
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_DEVICE_NAME=Brandon's MacBook
MUSIC_DEFAULT_SOURCE=local
ENABLE_SPOTIFY=false  # Set to true to enable
```

## Testing Plan
1. Test local music still works (play, stop, list)
2. Test Spotify initialization (with valid credentials)
3. Test source switching (local → spotify → local)
4. Test playback from each source
5. Test DJ mode with local source
6. Test DJ mode with Spotify source
7. Test crossfade with both backends
8. Test ducking with both backends

## Files Modified
- ✅ `cantina_os/cantina_os/services/music_controller_service/music_backends.py` (NEW)
- ✅ `cantina_os/cantina_os/services/music_controller_service/__init__.py` (NEW)
- ✅ `cantina_os/cantina_os/core/event_topics.py` (MUSIC_SOURCE_CHANGED)
- ✅ `cantina_os/cantina_os/event_payloads.py` (MusicSourceChangedPayload)
- ⏳ `cantina_os/cantina_os/services/music_controller_service/music_controller_service.py` (MAJOR UPDATES)
- ⏳ `cantina_os/cantina_os/services/brain_service.py` (Source awareness)
- ⏳ `cantina_os/cantina_os/services/command_dispatcher_service.py` (Source commands)
- ⏳ `.env` (Spotify credentials)
- ⏳ `cantina_os/cantina_os/main.py` (Import updates)

## Key Design Decisions
1. **Backend abstraction** - Clean interface, easy to add more providers
2. **Explicit source switching** - User controls which library is active
3. **Separate libraries** - Local and Spotify tracks in separate dictionaries
4. **Event-driven** - All communication via events (MUSIC_SOURCE_CHANGED)
5. **Backwards compatible** - Existing code mostly unchanged
6. **Graceful degradation** - Spotify optional, system works without it
