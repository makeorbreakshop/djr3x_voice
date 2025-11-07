# CantinaOS Spotify Integration - Implementation TODO

> **Comprehensive implementation checklist based on the Spotify Integration PRD**
> 
> Follow this checklist to implement the MusicSourceManagerService approach with local-first architecture.

## 📋 Overview

This checklist implements the **MusicSourceManagerService** approach outlined in the PRD, which:
- Defaults to local Star Wars music 
- Requires explicit "play spotify" commands
- Provides graceful fallback to local when Spotify unavailable
- Follows all CantinaOS architecture standards

---

## Phase 1: Core Service Foundation (Week 1)

### 1.1 MusicSourceManagerService Setup

**Service Structure & Files**
- [ ] Create service directory: `cantina_os/cantina_os/services/music_source_manager_service/`
- [ ] Create `__init__.py` in service directory
- [ ] Create `music_source_manager_service.py` with proper class structure
- [ ] Create `tests/` subdirectory with `test_music_source_manager_service.py`

**Service Class Implementation**
- [ ] Create `MusicSourceManagerService` class inheriting from `StandardService`
- [ ] Set service name: `name="music_source_manager_service"`
- [ ] Implement required constructor signature: `def __init__(self, event_bus, config=None, name="music_source_manager_service")`
- [ ] Call `super().__init__(event_bus, config, name=name)`

**Configuration Model**
- [ ] Create Pydantic `_Config` model with these fields:
  - [ ] `default_provider: str = "local"` - Default music provider
  - [ ] `enable_spotify: bool = False` - Enable Spotify integration
  - [ ] `spotify_config: Optional[Dict[str, Any]] = None` - Spotify configuration
  - [ ] `fallback_enabled: bool = True` - Auto-fallback to local
  - [ ] `local_music_directory: str = "./music"` - Local music path
- [ ] Validate config in `__init__` method

### 1.2 Provider Interface Design

**Abstract Provider Interface**
- [ ] Create `music_providers/` subdirectory in service directory
- [ ] Create `base_provider.py` with abstract `MusicProvider` class
- [ ] Define provider interface methods:
  - [ ] `async def initialize() -> bool` - Setup provider
  - [ ] `async def search(query: str) -> List[Track]` - Search tracks
  - [ ] `async def play_track(track_id: str) -> bool` - Play specific track
  - [ ] `async def stop() -> bool` - Stop playback
  - [ ] `async def get_current_track() -> Optional[Track]` - Get current track
  - [ ] `async def is_available() -> bool` - Check if provider is working

**Track Model**
- [ ] Create `track_model.py` with unified track representation:
  - [ ] `track_id: str` - Unique identifier
  - [ ] `title: str` - Track title
  - [ ] `artist: str` - Artist name
  - [ ] `duration: Optional[int]` - Duration in seconds
  - [ ] `provider: str` - Which provider ("local" or "spotify")
  - [ ] `source_path: Optional[str]` - Local file path or Spotify URI

### 1.3 Local Music Provider

**LocalMusicProvider Implementation**
- [ ] Create `local_music_provider.py` implementing `MusicProvider`
- [ ] Integrate with existing `MusicControllerService` functionality:
  - [ ] Use existing music library scanning
  - [ ] Use existing VLC playback engine
  - [ ] Preserve existing crossfade and ducking capabilities
- [ ] Map local files to `Track` model format
- [ ] Implement search functionality over local library
- [ ] Handle provider availability (check if music directory exists)

### 1.4 Event Topics & Payloads

**Add to EventTopics enum (`core/event_topics.py`)**
- [ ] `MUSIC_PROVIDER_CHANGED = "/music/provider_changed"` - Provider switch events
- [ ] `SPOTIFY_COMMAND = "/music/spotify_command"` - Spotify-specific commands  
- [ ] `MUSIC_SOURCE_STATUS = "/music/source_status"` - Source availability status

**Add to event_payloads.py**
- [ ] `MusicProviderChangedPayload` with fields:
  - [ ] `previous_provider: str`
  - [ ] `current_provider: str`
  - [ ] `reason: str` (user_request, fallback, etc.)
  - [ ] `timestamp: str`
- [ ] `SpotifyCommandPayload` with fields:
  - [ ] `action: str` (play, search, etc.)
  - [ ] `query: Optional[str]`
  - [ ] `track_id: Optional[str]`
  - [ ] `timestamp: str`

### 1.5 Service Lifecycle Implementation

**Required Lifecycle Methods**
- [ ] Implement `_start()` method:
  - [ ] Call `await self._setup_subscriptions()`
  - [ ] Initialize default provider (local)
  - [ ] Load provider configurations
  - [ ] Emit `SERVICE_STATUS_UPDATE` when ready
- [ ] Implement `_stop()` method:
  - [ ] Stop current provider
  - [ ] Clean up resources
  - [ ] Cancel background tasks
- [ ] Implement `_setup_subscriptions()` method:
  - [ ] Subscribe to `MUSIC_COMMAND` events
  - [ ] Subscribe to `SPOTIFY_COMMAND` events
  - [ ] Use `await asyncio.gather()` pattern

**Event Handlers**
- [ ] Implement `_handle_music_command()` - Route to appropriate provider
- [ ] Implement `_handle_spotify_command()` - Handle Spotify-specific commands
- [ ] Add proper error handling with try/except blocks
- [ ] Use `self._emit_dict()` for all event emissions

### 1.6 Service Registration

**Main.py Integration**
- [ ] Import service: `from .services.music_source_manager_service import MusicSourceManagerService`
- [ ] Add to `SERVICE_CLASS_MAP`: `"music_source_manager_service": MusicSourceManagerService`
- [ ] Add to `service_order` after dependencies:
  ```python
  service_order = [
      "memory_service",
      "command_dispatcher", 
      "music_controller_service",  # Dependency
      "music_source_manager_service",  # Add here
      # ... other services
  ]
  ```

**Configuration Setup**
- [ ] Add environment variables to `.env`:
  - [ ] `MUSIC_DEFAULT_PROVIDER=local`
  - [ ] `ENABLE_SPOTIFY=false`
  - [ ] `LOCAL_MUSIC_DIRECTORY=./music`

### 1.7 Testing Foundation

**Unit Tests**
- [ ] Test service initialization
- [ ] Test provider switching logic
- [ ] Test event subscription setup
- [ ] Test error handling for missing providers
- [ ] Test configuration validation

**Integration Testing Setup**
- [ ] Mock event bus for testing
- [ ] Mock provider implementations
- [ ] Test service startup sequence
- [ ] Test graceful shutdown

---

## Phase 2: Spotify Provider Implementation (Week 2)

### 2.1 Spotify API Integration

**Dependencies & Setup**
- [ ] Add to `requirements.txt`: `spotipy>=2.23.0` (Spotify Web API client)
- [ ] Add to `requirements.txt`: `python-dotenv>=1.0.0` (for environment variables)
- [ ] Research Spotify Web API authentication patterns
- [ ] Document required Spotify app credentials (Client ID, Client Secret)

**SpotifyMusicProvider Implementation**
- [ ] Create `spotify_music_provider.py` implementing `MusicProvider`
- [ ] Implement OAuth 2.0 authentication with refresh tokens
- [ ] Add authentication configuration:
  - [ ] `spotify_client_id: str`
  - [ ] `spotify_client_secret: str` 
  - [ ] `spotify_redirect_uri: str = "http://localhost:8080/callback"`
  - [ ] `spotify_scope: str = "user-read-playback-state,user-modify-playback-state"`

**Provider Methods Implementation**
- [ ] `async def initialize()` - Setup Spotify client and authenticate
- [ ] `async def search(query: str)` - Search Spotify catalog
- [ ] `async def play_track(track_id: str)` - Play via Spotify Connect
- [ ] `async def stop()` - Stop Spotify playback
- [ ] `async def get_current_track()` - Get current Spotify track
- [ ] `async def is_available()` - Check Spotify API connectivity

### 2.2 Authentication System

**Simple Single-User OAuth**
- [ ] Implement authorization code flow for initial setup
- [ ] Store refresh token securely (encrypted file or environment)
- [ ] Automatic token refresh when expired
- [ ] Fallback handling when authentication fails

**Configuration Management**
- [ ] Add Spotify config to service `_Config`:
  ```python
  spotify_config: Optional[Dict[str, Any]] = Field(default_factory=lambda: {
      "client_id": os.getenv("SPOTIFY_CLIENT_ID"),
      "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET"),
      "redirect_uri": "http://localhost:8080/callback"
  })
  ```
- [ ] Validate required credentials on startup
- [ ] Graceful degradation when credentials missing

### 2.3 Track Format Unification

**Track Model Mapping**
- [ ] Map Spotify track objects to unified `Track` model:
  - [ ] `track_id` = Spotify track URI
  - [ ] `title` = track name
  - [ ] `artist` = primary artist name
  - [ ] `duration` = duration_ms / 1000
  - [ ] `provider` = "spotify"
  - [ ] `source_path` = Spotify URI

**Search Result Formatting**
- [ ] Limit search results to reasonable number (20-50 tracks)
- [ ] Include track popularity for better ranking
- [ ] Handle empty search results gracefully
- [ ] Add PG content filtering for family-friendly results

### 2.4 Spotify Connect Integration

**Playback Control**
- [ ] Research Spotify Connect API for device control
- [ ] Implement device discovery and selection
- [ ] Handle case when no active Spotify device available
- [ ] Provide clear error messages for device setup

**State Synchronization**
- [ ] Monitor Spotify playback state
- [ ] Emit `TRACK_PLAYING` events for Spotify tracks
- [ ] Handle external Spotify app interactions
- [ ] Sync volume and progress information

---

## Phase 3: Command Integration (Week 3)

### 3.1 Command Parsing Enhancement

**CommandDispatcherService Updates**
- [ ] Add Spotify command patterns to dispatcher:
  - [ ] `spotify play <query>` → SpotifyCommand
  - [ ] `spotify search <query>` → SpotifyCommand  
  - [ ] `spotify stop` → SpotifyCommand
  - [ ] `play spotify <query>` → SpotifyCommand (alternative syntax)

**Command Registration in main.py**
- [ ] Register Spotify commands with CommandDispatcher:
  ```python
  dispatcher.register_command("spotify play", "music_source_manager_service", EventTopics.SPOTIFY_COMMAND)
  dispatcher.register_command("spotify search", "music_source_manager_service", EventTopics.SPOTIFY_COMMAND)
  dispatcher.register_command("spotify stop", "music_source_manager_service", EventTopics.SPOTIFY_COMMAND)
  dispatcher.register_command("play spotify", "music_source_manager_service", EventTopics.SPOTIFY_COMMAND)
  ```

### 3.2 Provider Switching Logic

**Command Router Implementation**
- [ ] Implement provider detection in command handling:
  ```python
  async def _handle_music_command(self, event_name: str, payload: Dict[str, Any]):
      command = payload.get("command", "")
      if "spotify" in command.lower():
          await self._switch_to_spotify_provider()
      else:
          await self._switch_to_local_provider()
  ```
- [ ] Add provider switching with proper state management
- [ ] Emit `MUSIC_PROVIDER_CHANGED` events
- [ ] Update memory service with current provider state

**Fallback Mechanism**
- [ ] Monitor Spotify API availability
- [ ] Automatic fallback to local when Spotify fails:
  ```python
  async def _fallback_to_local(self, reason: str):
      if self._config.fallback_enabled:
          await self._switch_to_local_provider()
          await self._emit_dict(EventTopics.MUSIC_PROVIDER_CHANGED, {
              "previous_provider": "spotify",
              "current_provider": "local", 
              "reason": f"fallback: {reason}"
          })
  ```

### 3.3 Voice Command Integration

**Natural Language Support**
- [ ] Update GPT persona to understand Spotify commands:
  - [ ] "play spotify music" → spotify play command
  - [ ] "search spotify for song" → spotify search command
  - [ ] "stop spotify" → spotify stop command
- [ ] Add intent detection for Spotify requests
- [ ] Maintain backwards compatibility with existing music commands

**Response Generation**
- [ ] Generate appropriate responses for Spotify commands
- [ ] Include track information in responses
- [ ] Handle error cases with helpful messages

### 3.4 Memory Service Integration

**State Persistence**
- [ ] Add to MemoryService state keys:
  - [ ] `current_music_provider` - Active provider
  - [ ] `spotify_last_track` - Last played Spotify track
  - [ ] `spotify_available` - Spotify API status
- [ ] Update state on provider changes
- [ ] Restore provider state on service restart

---

## Phase 4: Error Handling & Edge Cases (Week 4)

### 4.1 Comprehensive Error Handling

**Network Connectivity Issues**
- [ ] Handle Spotify API timeouts
- [ ] Graceful degradation during network outages
- [ ] Retry logic with exponential backoff
- [ ] Clear user feedback for connectivity issues

**Authentication Problems**
- [ ] Handle expired/invalid tokens
- [ ] Automatic re-authentication when possible
- [ ] Fallback to local when auth permanently fails
- [ ] User-friendly error messages for setup issues

**Device/Hardware Issues**
- [ ] Handle missing Spotify devices
- [ ] Audio output conflicts between local and Spotify
- [ ] VLC vs Spotify Connect audio coordination
- [ ] Device discovery and setup guidance

### 4.2 Content Safety Implementation

**PG Rating Filter**
- [ ] Implement Spotify track filtering:
  ```python
  def _is_family_friendly(self, track: Dict) -> bool:
      explicit = track.get("explicit", False)
      return not explicit
  ```
- [ ] Filter search results for appropriate content
- [ ] Add configuration option to disable filtering
- [ ] Log filtered content for review

**Content Validation**
- [ ] Validate track availability before playback
- [ ] Handle region-restricted content
- [ ] Provide alternatives for unavailable tracks

### 4.3 Performance Optimization

**Caching Strategy**
- [ ] Cache Spotify search results for recent queries
- [ ] Cache authentication tokens appropriately
- [ ] Implement request rate limiting for Spotify API
- [ ] Monitor API quota usage

**Resource Management**
- [ ] Proper cleanup of Spotify client resources
- [ ] Memory management for search results
- [ ] Background token refresh without blocking

### 4.4 Logging & Debugging

**Enhanced Logging**
- [ ] Add comprehensive logging for provider operations:
  - [ ] Provider switching events
  - [ ] Authentication status changes
  - [ ] API errors and responses
  - [ ] Fallback operations
- [ ] Log levels appropriate for production vs development
- [ ] Avoid logging sensitive authentication data

**Debug Commands**
- [ ] Add debug commands for testing:
  - [ ] `debug spotify status` - Check Spotify connectivity
  - [ ] `debug spotify auth` - Check authentication status
  - [ ] `debug provider switch` - Force provider switching

---

## Phase 5: Web Dashboard Integration (Week 5)

### 5.1 WebBridge Service Updates

**Event Forwarding**
- [ ] Update WebBridge to handle new event types:
  - [ ] `MUSIC_PROVIDER_CHANGED` → Forward to dashboard
  - [ ] `SPOTIFY_COMMAND` → Handle from web interface
  - [ ] `MUSIC_SOURCE_STATUS` → Update dashboard status
- [ ] Add field mapping for Spotify-specific data
- [ ] Handle provider-specific payload formats

**Dashboard Command Support**
- [ ] Add web command handlers for Spotify:
  ```python
  @self._sio.event
  async def spotify_command(sid, data):
      command_payload = SpotifyCommandPayload(**data)
      self._event_bus.emit(EventTopics.SPOTIFY_COMMAND, command_payload.model_dump())
  ```

### 5.2 Dashboard UI Integration (Optional)

**Provider Toggle**
- [ ] Add provider selector to web dashboard
- [ ] Real-time provider status display
- [ ] Manual provider switching controls
- [ ] Visual indication of current provider

**Status Display**
- [ ] Show current music provider in dashboard
- [ ] Spotify authentication status indicator
- [ ] Fallback status notifications
- [ ] Provider availability indicators

### 5.3 Real-time Updates

**State Synchronization**
- [ ] Broadcast provider changes to connected clients
- [ ] Update dashboard when fallback occurs
- [ ] Sync track information regardless of provider
- [ ] Handle dashboard reconnection state sync

---

## Phase 6: Testing & Documentation (Week 6)

### 6.1 Comprehensive Testing

**Unit Test Coverage**
- [ ] Test all provider implementations
- [ ] Test command parsing and routing
- [ ] Test fallback mechanisms
- [ ] Test error handling paths
- [ ] Test configuration validation

**Integration Testing**
- [ ] End-to-end provider switching
- [ ] Voice command to Spotify playback flow
- [ ] Dashboard to provider interaction
- [ ] Multi-provider session scenarios

**Mock Testing Setup**
- [ ] Mock Spotify API for testing without credentials
- [ ] Mock network failures for fallback testing
- [ ] Mock authentication flows
- [ ] Test with missing dependencies

### 6.2 Performance Testing

**Load Testing**
- [ ] Multiple rapid provider switches
- [ ] High-frequency search operations
- [ ] Long-running session stability
- [ ] Memory usage monitoring

**API Quota Management**
- [ ] Test rate limiting implementation
- [ ] Monitor API usage patterns
- [ ] Validate quota monitoring logic

### 6.3 Documentation

**User Documentation**
- [ ] Spotify setup guide (getting credentials)
- [ ] Voice command examples
- [ ] Troubleshooting guide for common issues
- [ ] Configuration options documentation

**Developer Documentation**
- [ ] Provider interface documentation
- [ ] Event flow diagrams
- [ ] Architecture decision records
- [ ] Testing procedures

**Configuration Documentation**
- [ ] Environment variable reference
- [ ] Configuration file examples
- [ ] Security considerations for credentials

---

## ✅ Verification Checklist

**Final Testing Requirements**
- [ ] Service starts without errors with Spotify disabled
- [ ] Service starts without errors with Spotify enabled (with valid credentials)
- [ ] Local music continues to work exactly as before
- [ ] "play spotify [song]" commands work when enabled
- [ ] Automatic fallback works when Spotify becomes unavailable
- [ ] Voice commands correctly route to appropriate provider
- [ ] Dashboard shows current provider status
- [ ] All error cases provide helpful user feedback
- [ ] No regressions in existing music functionality
- [ ] Memory usage remains stable during provider switching

**Architecture Compliance Verification**
- [ ] Service follows CantinaOS service creation guidelines
- [ ] Uses EventTopics enum for all event names
- [ ] Implements proper `_start()` and `_stop()` lifecycle
- [ ] Uses `await asyncio.gather()` for initial subscriptions
- [ ] Uses `self._emit_dict()` for all event emissions
- [ ] Proper error handling with try/except blocks
- [ ] Resource cleanup in `_stop()` method
- [ ] Follows import path standards
- [ ] Pydantic models for all event payloads

**Production Readiness**
- [ ] No API keys or secrets in code
- [ ] Proper environment variable handling
- [ ] Graceful degradation without Spotify
- [ ] Clear error messages for setup issues
- [ ] Reasonable defaults for all configuration
- [ ] No breaking changes to existing APIs

---

## 📚 Reference Documentation

**CantinaOS Architecture**
- `cantina_os/docs/SERVICE_CREATION_GUIDELINES.md` - Service creation standards
- `cantina_os/docs/ARCHITECTURE_STANDARDS.md` - Coding standards  
- `cantina_os/docs/SERVICE_REGISTRY.md` - Service discovery patterns
- `cantina_os/docs/CANTINA_OS_TROUBLESHOOTING.md` - Common issues and solutions

**External APIs**
- [Spotify Web API Documentation](https://developer.spotify.com/documentation/web-api/)
- [Spotipy Library Documentation](https://spotipy.readthedocs.io/)
- [OAuth 2.0 Authorization Code Flow](https://developer.spotify.com/documentation/general/guides/authorization/code-flow/)

**Testing Resources**
- Existing CantinaOS service tests for patterns
- Mock implementations in test suites
- Performance testing utilities

---

**Document Version**: 1.0  
**Created**: 2025-06-18  
**Implementation Target**: 6 weeks  
**Next Review**: After Phase 1 completion