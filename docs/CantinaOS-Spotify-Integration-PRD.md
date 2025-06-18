# CantinaOS Spotify Integration - Product Requirements Document (PRD)

## 1. Executive Summary

### 1.1 Overview
The CantinaOS Spotify Integration expands DJ R3X's music capabilities by adding Spotify streaming as an optional music source alongside the existing local Star Wars music library. This integration provides access to millions of tracks for everyday use while preserving the authentic Oga's Cantina experience as the default behavior.

### 1.2 Problem Statement
The current DJ R3X system has a limited music library of ~20 Star Wars-specific tracks from Oga's Cantina, which creates constraints for:
- Everyday casual use beyond the Star Wars theme
- Extended DJ mode sessions requiring musical variety
- Demonstrating the system's capabilities with popular/recognizable music
- Long-form background music during development and testing

### 1.3 Solution
A new **MusicSourceManagerService** that coordinates between local Star Wars music (default) and Spotify streaming (on-demand), providing:
- Seamless integration with existing CantinaOS architecture
- Explicit Spotify activation via voice/CLI commands
- Graceful fallback to local music when Spotify unavailable
- Spotify's recommendation engine for DJ mode track selection
- Simple single-user authentication and setup

## 2. User Profile & Use Cases

### 2.1 Primary User
**System Developer/Operator** (Single User)
- Daily DJ R3X system operation and development
- Wants expanded music library for variety and extended use
- Prefers Star Wars music for demos/theme maintenance
- Needs reliable fallback when internet/Spotify unavailable
- Values simple setup without complex configuration management

### 2.2 Key Use Cases

1. **Default Star Wars Experience**: System starts with local Cantina music, maintaining authentic experience
2. **Expanded Everyday Use**: "Play Spotify [song/artist]" for access to full music catalog
3. **Extended DJ Mode**: Use Spotify recommendations for longer, more varied DJ sessions
4. **Reliable Fallback**: Automatic return to local music when Spotify unavailable
5. **Demo Flexibility**: Switch between thematic Star Wars music and popular recognizable tracks
6. **Development Background**: Access to instrumental/ambient music during coding sessions

## 3. Technical Architecture

### 3.1 Service Architecture Overview

The MusicSourceManagerService follows CantinaOS patterns and integrates seamlessly with existing services:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Voice/CLI     │────▶│ MusicSource     │────▶│ Local Provider  │
│   Commands      │     │ Manager         │     │ (Existing VLC)  │
└─────────────────┘     │                 │     └─────────────────┘
                        │                 │     ┌─────────────────┐
┌─────────────────┐     │                 │────▶│ Spotify Provider│
│ BrainService    │────▶│                 │     │ (New Service)   │
│ (DJ Mode)       │     │                 │     └─────────────────┘
└─────────────────┘     └─────────────────┘
                                │
                                ▼
                        ┌─────────────────┐
                        │  Web Dashboard  │
                        │ Source Toggle   │
                        └─────────────────┘
```

### 3.2 Integration with Existing CantinaOS Architecture

**Service Registry Integration**:
| Service Name | Purpose | Events Subscribed (Inputs) | Events Published (Outputs) | Configuration | Hardware Dependencies |
|--------------|---------|----------------------------|----------------------------|---------------|----------------------|
| MusicSourceManagerService | Music provider coordination | MUSIC_COMMAND, SPOTIFY_COMMAND, DJ_MODE_CHANGED | MUSIC_PLAYBACK_STARTED, MUSIC_PROVIDER_CHANGED, SPOTIFY_STATUS_UPDATE | default_provider, spotify_config, fallback_enabled | None |

**Event Bus Integration**:
- **Subscribed Events**: `MUSIC_COMMAND`, `SPOTIFY_COMMAND`, `DJ_MODE_CHANGED`, `AUDIO_DUCKING_START/STOP`
- **Published Events**: `MUSIC_PLAYBACK_STARTED/STOPPED`, `MUSIC_PROVIDER_CHANGED`, `SPOTIFY_STATUS_UPDATE`
- **Event Flow**: `Voice/CLI → CommandDispatcher → MusicSourceManager → Provider → Playback`

### 3.3 Provider Architecture

#### 3.3.1 MusicProvider Interface
```python
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any

class MusicProvider(ABC):
    """Abstract base class for music providers."""
    
    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize provider with configuration."""
        pass
    
    @abstractmethod
    async def search_tracks(self, query: str, limit: int = 10) -> List[MusicTrack]:
        """Search for tracks matching query."""
        pass
    
    @abstractmethod
    async def play_track(self, track_id: str) -> bool:
        """Start playing specified track."""
        pass
    
    @abstractmethod
    async def stop_playback(self) -> bool:
        """Stop current playback."""
        pass
    
    @abstractmethod
    async def get_recommendations(self, seed_track: str = None) -> List[MusicTrack]:
        """Get recommended tracks for DJ mode."""
        pass
```

#### 3.3.2 Local Music Provider
```python
class LocalMusicProvider(MusicProvider):
    """Provider for local Star Wars music files."""
    
    def __init__(self, music_controller_service):
        self.music_controller = music_controller_service  # Reuse existing service
        self.capabilities = ["local_playback", "crossfade", "offline"]
    
    async def search_tracks(self, query: str) -> List[MusicTrack]:
        """Search existing local music library."""
        return await self.music_controller.search_library(query)
```

#### 3.3.3 Spotify Music Provider
```python
class SpotifyMusicProvider(MusicProvider):
    """Provider for Spotify streaming integration."""
    
    def __init__(self, spotify_config):
        self.capabilities = ["streaming", "search", "recommendations", "large_catalog"]
        self.client = None
        self.device_id = None
    
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize Spotify Web API client."""
        # OAuth setup with user authorization
        # Device discovery for playback
        pass
    
    async def search_tracks(self, query: str, limit: int = 10) -> List[MusicTrack]:
        """Search Spotify catalog with PG content filtering."""
        results = self.client.search(q=query, type='track', limit=limit)
        # Filter explicit content
        return [track for track in results if not track.get('explicit', False)]
```

## 4. Service Implementation

### 4.1 MusicSourceManagerService Class Structure

```python
class MusicSourceManagerService(BaseService):
    """Central orchestrator for multiple music providers."""
    
    class _Config(BaseModel):
        """Service configuration with local-first defaults."""
        default_provider: str = Field(default="local", description="Default music provider")
        enable_spotify: bool = Field(default=False, description="Enable Spotify integration")
        spotify_config: Optional[Dict[str, Any]] = Field(default=None)
        fallback_enabled: bool = Field(default=True, description="Auto-fallback to local")
        content_filter: str = Field(default="pg", description="Content filtering level")
        
    def __init__(self, event_bus, config=None, name="music_source_manager"):
        super().__init__(event_bus, config, name=name)
        self._config = self._Config(**(config or {}))
        self._providers: Dict[str, MusicProvider] = {}
        self._active_provider: str = "local"  # Always default to local
        self._fallback_provider: str = "local"
```

### 4.2 Core Service Methods

#### 4.2.1 Provider Management
```python
async def _start(self) -> None:
    """Initialize providers with local-first approach."""
    await self._setup_subscriptions()
    
    # Always initialize local provider first
    local_provider = LocalMusicProvider(self._existing_music_controller)
    await local_provider.initialize({})
    self._providers["local"] = local_provider
    
    # Initialize Spotify only if configured
    if self._config.enable_spotify and self._config.spotify_config:
        try:
            spotify_provider = SpotifyMusicProvider()
            success = await spotify_provider.initialize(self._config.spotify_config)
            if success:
                self._providers["spotify"] = spotify_provider
                self._logger.info("Spotify provider initialized successfully")
            else:
                self._logger.warning("Spotify initialization failed, local-only mode")
        except Exception as e:
            self._logger.error(f"Spotify setup error: {e}, continuing with local-only")
    
    await self._emit_status(ServiceStatus.RUNNING, "MusicSourceManager started")

async def _route_command(self, command: str, **kwargs) -> Tuple[str, MusicProvider]:
    """Route commands to appropriate provider with fallback."""
    
    # Check for explicit provider specification
    if "spotify" in command.lower() or kwargs.get("provider") == "spotify":
        if "spotify" in self._providers:
            return "spotify", self._providers["spotify"]
        else:
            self._logger.warning("Spotify requested but not available, using local")
            return "local", self._providers["local"]
    
    # Default to active provider (local by default)
    active_provider = self._providers.get(self._active_provider, self._providers["local"])
    return self._active_provider, active_provider
```

#### 4.2.2 Command Handling
```python
async def _handle_music_command(self, event_name: str, payload: Dict[str, Any]) -> None:
    """Handle music commands with provider routing."""
    try:
        action = payload.get("action")
        query = payload.get("song_query", "")
        
        # Route to appropriate provider
        provider_name, provider = await self._route_command(action, song_query=query)
        
        # Execute command with fallback
        success = await self._execute_with_fallback(provider_name, provider, action, payload)
        
        if success:
            # Update active provider
            if action in ["play", "search"]:
                self._active_provider = provider_name
            
            # Emit success response
            self._emit_dict(EventTopics.MUSIC_PLAYBACK_STARTED, {
                "provider": provider_name,
                "action": action,
                "track": payload.get("song_query"),
                "timestamp": time.time()
            })
        else:
            await self._emit_status(ServiceStatus.ERROR, f"Music command failed: {action}")
            
    except Exception as e:
        self._logger.error(f"Error handling music command: {e}")
        await self._emit_status(ServiceStatus.ERROR, str(e))

async def _execute_with_fallback(self, provider_name: str, provider: MusicProvider, 
                               action: str, payload: Dict[str, Any]) -> bool:
    """Execute command with automatic fallback to local."""
    try:
        # Try primary provider
        success = await self._execute_on_provider(provider, action, payload)
        if success:
            return True
    except Exception as e:
        self._logger.warning(f"Provider {provider_name} failed: {e}")
    
    # Fallback to local if enabled and not already local
    if (self._config.fallback_enabled and 
        provider_name != "local" and 
        "local" in self._providers):
        
        self._logger.info(f"Falling back to local music")
        local_provider = self._providers["local"]
        try:
            return await self._execute_on_provider(local_provider, action, payload)
        except Exception as e:
            self._logger.error(f"Local fallback also failed: {e}")
    
    return False
```

## 5. User Interface Integration

### 5.1 Command Interface Design

#### 5.1.1 CLI Commands
```bash
# Default behavior (local Star Wars music)
play music cantina band          # → Local music search
dj start                        # → Local DJ mode

# Explicit Spotify commands
play spotify despacito          # → Spotify search and play
search spotify rock music      # → Browse Spotify catalog
spotify dj start               # → DJ mode with Spotify recommendations

# Provider management
music source status            # → Show active provider
music source local             # → Switch to local only
music source spotify           # → Switch to Spotify (if available)
```

#### 5.1.2 Voice Commands
```
"Play Cantina Band"            → Local music (default)
"Play Spotify Despacito"      → Spotify explicit
"Start DJ mode"               → Current provider DJ mode
"Play some rock music on Spotify" → Spotify with genre
"Switch to local music"       → Provider switching
```

### 5.2 Web Dashboard Integration

#### 5.2.1 Music Tab Enhancements
```typescript
interface MusicState {
  activeProvider: 'local' | 'spotify';
  availableProviders: string[];
  spotifyConnected: boolean;
  currentTrack: TrackInfo | null;
  library: {
    local: TrackInfo[];
    spotify: SearchResults | null;
  };
}

// UI Components
<ProviderToggle>
  <LocalButton active={activeProvider === 'local'} />
  <SpotifyButton active={activeProvider === 'spotify'} disabled={!spotifyConnected} />
</ProviderToggle>

<MusicBrowser>
  {activeProvider === 'local' ? <LocalLibrary /> : <SpotifySearch />}
</MusicBrowser>
```

#### 5.2.2 Provider Status Indicators
- **Local Active**: Green indicator, "Star Wars Music"
- **Spotify Active**: Spotify green with connection status
- **Spotify Unavailable**: Gray indicator, "Not Connected"
- **Fallback Mode**: Yellow indicator, "Using Local (Spotify Unavailable)"

## 6. Authentication & Configuration

### 6.1 Spotify Authentication Setup

#### 6.1.1 One-Time Setup Process
```python
class SpotifyAuthenticator:
    """Simple OAuth setup for single-user system."""
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = "http://localhost:8888/callback"
        self.scope = "user-read-playback-state user-modify-playback-state streaming"
    
    async def authenticate(self) -> Optional[str]:
        """Perform OAuth flow and return access token."""
        # 1. Open browser to Spotify authorization URL
        # 2. User grants permission
        # 3. Callback receives authorization code
        # 4. Exchange code for access token
        # 5. Store token securely for future use
        pass
```

#### 6.1.2 Configuration Management
```bash
# .env configuration
SPOTIFY_CLIENT_ID=your_spotify_app_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_app_client_secret
SPOTIFY_DEVICE_NAME=DJ_R3X
MUSIC_DEFAULT_PROVIDER=local
MUSIC_ENABLE_SPOTIFY=true
CONTENT_FILTER_LEVEL=pg
```

### 6.2 Setup Commands
```python
# CLI setup commands
music setup spotify              # → Launch OAuth flow
music test spotify              # → Test Spotify connection
music config show              # → Display current configuration
music provider enable spotify  # → Enable Spotify provider
music provider disable spotify # → Disable Spotify provider
```

## 7. DJ Mode Integration

### 7.1 Enhanced DJ Mode with Spotify

#### 7.1.1 Provider-Aware DJ Logic
```python
class DJModeOrchestrator:
    """Enhanced DJ mode supporting multiple providers."""
    
    async def select_next_track(self, current_provider: str) -> MusicTrack:
        """Select next track based on active provider."""
        
        if current_provider == "spotify":
            # Use Spotify's recommendation engine
            recommendations = await self._get_spotify_recommendations()
            return self._filter_and_select(recommendations)
        else:
            # Use existing local selection logic
            return await self._select_local_track()
    
    async def _get_spotify_recommendations(self) -> List[MusicTrack]:
        """Get Spotify recommendations with content filtering."""
        spotify_provider = self._source_manager.get_provider("spotify")
        
        # Get recommendations based on current context
        recommendations = await spotify_provider.get_recommendations(
            seed_genres=['ambient', 'electronic', 'jazz'],  # DJ-friendly genres
            target_valence=0.7,  # Positive mood
            target_energy=0.6,   # Moderate energy
            limit=20
        )
        
        # Apply PG content filter
        return [track for track in recommendations if not track.explicit]
```

#### 7.1.2 DJ Mode Commands
```bash
# Provider-specific DJ mode
dj start local                 # → DJ mode with local music only
dj start spotify              # → DJ mode with Spotify recommendations  
dj start                      # → Use active provider

# DJ mode provider switching
dj switch spotify             # → Switch DJ mode to Spotify
dj fallback                   # → Force fallback to local in DJ mode
```

## 8. Feature Requirements

### 8.1 Core Features (MVP)

**Provider Management**
- ✅ Local music provider (existing functionality preserved)
- ✅ Spotify provider with basic playback
- ✅ Automatic provider routing based on commands
- ✅ Graceful fallback from Spotify to local

**Command Integration**
- ✅ CLI commands with provider specification
- ✅ Voice command recognition for Spotify
- ✅ Integration with existing CommandDispatcher
- ✅ Web dashboard provider toggle

**Basic Spotify Features**
- ✅ OAuth authentication setup
- ✅ Track search and playback
- ✅ PG content filtering
- ✅ Connection status monitoring

### 8.2 Enhanced Features (V2)

**DJ Mode Enhancement**
- ✅ Spotify recommendations for DJ mode
- ✅ Provider-aware track selection
- ✅ Genre and mood-based filtering
- ✅ Seamless DJ mode provider switching

**Advanced Search**
- ✅ Multi-criteria Spotify search (artist, album, genre)
- ✅ Search result ranking and filtering
- ✅ Recent searches and favorites
- ✅ Voice search query processing

**Provider Intelligence**
- ✅ Smart provider selection based on query
- ✅ Automatic retry with exponential backoff
- ✅ Provider health monitoring
- ✅ Usage analytics and preferences

### 8.3 Future Features (V3)

**Advanced Integrations**
- ✅ Spotify playlist integration
- ✅ Cross-fade between providers
- ✅ Offline Spotify caching
- ✅ Multiple music service support (YouTube Music, Apple Music)

## 9. Technical Specifications

### 9.1 Performance Requirements

**Response Times**
- Provider switching: < 2 seconds
- Spotify search: < 3 seconds  
- Track playback start: < 5 seconds
- Fallback to local: < 1 second

**Reliability**
- Local music availability: 100% (offline)
- Spotify connection uptime: > 95% (when internet available)
- Fallback success rate: > 99%
- Command routing accuracy: > 98%

### 9.2 Content Safety

**PG Content Filtering**
```python
def filter_explicit_content(tracks: List[MusicTrack]) -> List[MusicTrack]:
    """Remove explicit content for family-friendly operation."""
    return [
        track for track in tracks 
        if not track.explicit and 
        not any(word in track.title.lower() for word in BLOCKED_WORDS)
    ]
```

**Safe Default Behavior**
- All Spotify searches filtered for explicit content
- DJ mode uses curated genre seeds (ambient, jazz, electronic, classical)
- Fallback to local Star Wars music maintains safe content guarantee

### 9.3 Error Handling

**Spotify API Failures**
```python
async def handle_spotify_error(self, error: SpotifyException) -> None:
    """Handle Spotify-specific errors with appropriate fallback."""
    
    if isinstance(error, SpotifyAuthError):
        # Token expired, attempt refresh
        await self._refresh_spotify_token()
    elif isinstance(error, SpotifyRateLimitError):
        # Rate limited, queue request for retry
        await self._queue_for_retry(error.retry_after)
    elif isinstance(error, SpotifyConnectionError):
        # Network issue, fall back to local
        await self._activate_fallback_mode()
    else:
        # Unknown error, disable Spotify temporarily
        await self._disable_spotify_temporarily()
```

## 10. Implementation Plan

### 10.1 Development Phases

**Phase 1: Core Service Foundation (1 week)**
- Create MusicSourceManagerService following CantinaOS standards
- Implement MusicProvider interface and LocalMusicProvider
- Basic provider routing and command handling
- Service registration and integration testing

**Phase 2: Spotify Provider Implementation (1 week)**  
- Implement SpotifyMusicProvider with OAuth authentication
- Basic search and playback functionality
- PG content filtering implementation
- Connection status monitoring and error handling

**Phase 3: Command Integration (1 week)**
- Update CommandDispatcherService for provider routing
- Voice command recognition for Spotify
- CLI command expansion with provider options
- Integration testing with existing voice/command flow

**Phase 4: DJ Mode Enhancement (1 week)**
- Provider-aware DJ mode logic
- Spotify recommendations integration
- Enhanced track selection algorithms
- Comprehensive testing with both providers

**Phase 5: Web Dashboard Integration (1 week)**
- Provider toggle UI in Music tab
- Spotify search interface
- Connection status indicators
- Real-time provider switching

**Phase 6: Polish and Testing (1 week)**
- Comprehensive error handling and fallback testing
- Performance optimization
- Documentation and setup guides
- End-to-end integration testing

### 10.2 Technical Milestones

1. **MusicSourceManager Operational** - Provider routing working
2. **Spotify Authentication Working** - OAuth flow complete
3. **Basic Spotify Playback** - Search and play from Spotify
4. **Voice/CLI Integration** - "Play Spotify" commands functional
5. **DJ Mode with Spotify** - Recommendations driving track selection
6. **Web Dashboard Complete** - Full provider switching UI
7. **Production Ready** - All error handling and fallbacks tested

## 11. Risk Assessment & Mitigation

### 11.1 Technical Risks

**Spotify API Reliability**
- Risk: Spotify API outages breaking music functionality
- Mitigation: Robust fallback to local music with automatic retry
- Monitoring: Connection status tracking and user notification

**Authentication Complexity**
- Risk: OAuth flow confusion or token expiration
- Mitigation: Clear setup documentation and automatic token refresh
- Fallback: Manual re-authentication with clear instructions

**Provider Switching Confusion**
- Risk: Users unsure which provider is active
- Mitigation: Clear status indicators and confirmation responses
- Fallback: Always allow manual provider specification

### 11.2 User Experience Risks

**Local Music Preference Lost**
- Risk: Spotify becoming default and losing Star Wars character
- Mitigation: Always default to local, require explicit Spotify commands
- Monitoring: Usage analytics to ensure local remains primary

**Command Complexity**
- Risk: Too many command variations confusing users
- Mitigation: Keep existing commands unchanged, add explicit Spotify variants
- Documentation: Clear command reference with examples

## 12. Success Metrics

### 12.1 Functionality Success

**Core Integration**
- All existing local music commands work unchanged
- Spotify commands achieve >95% success rate when connected
- Fallback to local works in >99% of Spotify failures
- Voice command recognition >90% accuracy for provider specification

**User Experience**
- Setup process completed in <10 minutes
- Music source switching <3 seconds
- DJ mode runs for >30 minutes without interruption
- Demonstration mode switches between providers seamlessly

### 12.2 Technical Performance

**Reliability**
- Service startup success rate: >99%
- Local music availability: 100% (offline guaranteed)
- Spotify integration uptime: >95% when internet available
- Provider fallback response time: <2 seconds

**Quality**
- Zero breaking changes to existing functionality
- Memory usage increase: <20MB for Spotify provider
- CPU usage impact: <5% during normal operation
- Network usage: <100MB/hour for Spotify streaming

## 13. Future Enhancements

### 13.1 Additional Music Services

**Multi-Provider Support**
- YouTube Music integration
- Apple Music support
- SoundCloud integration
- Local network music servers (Plex, etc.)

**Advanced Provider Features**
- Cross-provider playlist creation
- Unified search across all providers
- Provider recommendation mixing
- Smart provider selection based on content

### 13.2 Enhanced DJ Mode

**AI-Powered Selection**
- Machine learning for music preference detection
- Context-aware mood selection
- Time-of-day based music selection
- User feedback learning for better recommendations

**Performance Features**
- Beatmatching between providers
- Key matching for harmonic mixing
- Advanced crossfading algorithms
- Real-time audio effects and filters

## 14. Conclusion

The CantinaOS Spotify Integration provides a practical expansion of DJ R3X's musical capabilities while preserving the authentic Star Wars experience that defines the system's character. By implementing a local-first approach with explicit Spotify activation, users gain access to millions of tracks for everyday use without compromising the thematic integrity of demonstrations and Star Wars-focused interactions.

The MusicSourceManagerService architecture ensures seamless integration with existing CantinaOS patterns while providing robust fallback mechanisms that maintain system reliability. The phased implementation approach delivers immediate value through basic Spotify integration while building a foundation for advanced features and additional music service support.

**Key Benefits**:
- Expanded music library for everyday use and extended DJ sessions
- Preserved Star Wars music as default for authentic experience  
- Simple single-user setup with robust error handling
- Seamless integration with existing voice/CLI/web interfaces
- Foundation for future multi-provider music ecosystem

This integration transforms DJ R3X from a themed demonstration system into a versatile daily-use music assistant while maintaining its unique Star Wars character and reliability.

---

**Document Version**: 1.0  
**Last Updated**: 2025-06-18  
**Next Review**: Upon completion of Phase 1 implementation