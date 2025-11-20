# Unified SpotifyPlayer Component Design Document

## Executive Summary

This document defines the architecture for a unified SpotifyPlayer component that consolidates the functionality from `SpotifyWebPlayer`, `SpotifySearch`, and `SpotifyTrackResults` into a single, cohesive interface. The design implements a state machine-driven approach that seamlessly transitions between authentication, player initialization, search, and playback states while maintaining type safety and integration with the existing CantinaOS event system.

## Component Interface Definition

### Core TypeScript Interfaces

```typescript
// Main component props interface
export interface SpotifyPlayerProps {
  className?: string
  onTrackChange?: (track: SpotifyTrack | null) => void
  onPlaybackStateChange?: (state: PlaybackState) => void
  onError?: (error: SpotifyPlayerError) => void
  compact?: boolean // Enable compact view for space-constrained layouts
  showSearch?: boolean // Toggle search interface visibility
  showQueue?: boolean // Toggle queue management visibility
  autoActivateDevice?: boolean // Automatically activate device when ready
}

// Consolidated state interface
export interface SpotifyPlayerState {
  // State machine status
  currentState: SpotifyPlayerStates
  previousState: SpotifyPlayerStates | null
  
  // Authentication state
  authState: {
    isAuthenticated: boolean
    isPremium: boolean
    isLoading: boolean
    error: string | null
    user: UserProfile | null
  }
  
  // Web Player state
  playerState: {
    isReady: boolean
    deviceId: string | null
    isActive: boolean
    isPlaying: boolean
    isPaused: boolean
    isBuffering: boolean
    duration: number
    position: number
    volume: number
    currentTrack: SpotifyTrack | null
    previousTracks: SpotifyTrack[]
    nextTracks: SpotifyTrack[]
  }
  
  // Search state
  searchState: {
    query: string
    isSearching: boolean
    results: SpotifySearchResult
    searchHistory: string[]
    selectedCategory: SearchCategory
    selectedTrack: SpotifyTrack | null
  }
  
  // UI state
  uiState: {
    showVolumeSlider: boolean
    showSearchHistory: boolean
    isTransitioning: boolean
    activePanel: 'player' | 'search' | 'queue'
    error: SpotifyPlayerError | null
  }
}

// State machine enumeration
export enum SpotifyPlayerStates {
  INITIALIZING = 'initializing',
  UNAUTHENTICATED = 'unauthenticated',
  AUTHENTICATING = 'authenticating',
  NON_PREMIUM = 'non_premium',
  DEVICE_INACTIVE = 'device_inactive',
  READY = 'ready',
  SEARCHING = 'searching',
  PLAYING = 'playing',
  PAUSED = 'paused',
  ERROR = 'error'
}

// Search categories
export type SearchCategory = 'tracks' | 'albums' | 'artists' | 'playlists'

// Error handling
export interface SpotifyPlayerError {
  type: 'auth' | 'player' | 'search' | 'network' | 'premium'
  message: string
  code?: string
  recoverable: boolean
  retryAction?: () => Promise<void>
}

// Action interface for state machine
export interface SpotifyPlayerAction {
  type: string
  payload?: any
}
```

### Component Action Creators

```typescript
// Authentication actions
export const authActions = {
  startAuth: (): SpotifyPlayerAction => ({ type: 'START_AUTH' }),
  authSuccess: (user: UserProfile, isPremium: boolean): SpotifyPlayerAction => ({
    type: 'AUTH_SUCCESS',
    payload: { user, isPremium }
  }),
  authFailure: (error: string): SpotifyPlayerAction => ({
    type: 'AUTH_FAILURE',
    payload: { error }
  }),
  logout: (): SpotifyPlayerAction => ({ type: 'LOGOUT' })
}

// Player actions
export const playerActions = {
  initializePlayer: (): SpotifyPlayerAction => ({ type: 'INITIALIZE_PLAYER' }),
  playerReady: (deviceId: string): SpotifyPlayerAction => ({
    type: 'PLAYER_READY',
    payload: { deviceId }
  }),
  playerError: (error: string): SpotifyPlayerAction => ({
    type: 'PLAYER_ERROR',
    payload: { error }
  }),
  deviceActivated: (): SpotifyPlayerAction => ({ type: 'DEVICE_ACTIVATED' }),
  playTrack: (track: SpotifyTrack): SpotifyPlayerAction => ({
    type: 'PLAY_TRACK',
    payload: { track }
  }),
  pauseTrack: (): SpotifyPlayerAction => ({ type: 'PAUSE_TRACK' }),
  resumeTrack: (): SpotifyPlayerAction => ({ type: 'RESUME_TRACK' }),
  stopTrack: (): SpotifyPlayerAction => ({ type: 'STOP_TRACK' }),
  updatePosition: (position: number): SpotifyPlayerAction => ({
    type: 'UPDATE_POSITION',
    payload: { position }
  }),
  setVolume: (volume: number): SpotifyPlayerAction => ({
    type: 'SET_VOLUME',
    payload: { volume }
  })
}

// Search actions
export const searchActions = {
  startSearch: (query: string): SpotifyPlayerAction => ({
    type: 'START_SEARCH',
    payload: { query }
  }),
  searchSuccess: (results: SpotifySearchResult): SpotifyPlayerAction => ({
    type: 'SEARCH_SUCCESS',
    payload: { results }
  }),
  searchFailure: (error: string): SpotifyPlayerAction => ({
    type: 'SEARCH_FAILURE',
    payload: { error }
  }),
  selectCategory: (category: SearchCategory): SpotifyPlayerAction => ({
    type: 'SELECT_CATEGORY',
    payload: { category }
  }),
  selectTrack: (track: SpotifyTrack): SpotifyPlayerAction => ({
    type: 'SELECT_TRACK',
    payload: { track }
  }),
  clearSearch: (): SpotifyPlayerAction => ({ type: 'CLEAR_SEARCH' })
}
```

## State Management Architecture

### State Machine Implementation

```typescript
// State machine reducer
export const spotifyPlayerReducer = (
  state: SpotifyPlayerState,
  action: SpotifyPlayerAction
): SpotifyPlayerState => {
  switch (action.type) {
    case 'START_AUTH':
      return {
        ...state,
        currentState: SpotifyPlayerStates.AUTHENTICATING,
        previousState: state.currentState,
        authState: { ...state.authState, isLoading: true, error: null }
      }
    
    case 'AUTH_SUCCESS':
      const { user, isPremium } = action.payload
      return {
        ...state,
        currentState: isPremium 
          ? SpotifyPlayerStates.DEVICE_INACTIVE 
          : SpotifyPlayerStates.NON_PREMIUM,
        previousState: state.currentState,
        authState: {
          isAuthenticated: true,
          isPremium,
          isLoading: false,
          error: null,
          user
        }
      }
    
    case 'AUTH_FAILURE':
      return {
        ...state,
        currentState: SpotifyPlayerStates.ERROR,
        previousState: state.currentState,
        authState: {
          ...state.authState,
          isLoading: false,
          error: action.payload.error
        },
        uiState: {
          ...state.uiState,
          error: {
            type: 'auth',
            message: action.payload.error,
            recoverable: true,
            retryAction: async () => { /* retry auth */ }
          }
        }
      }
    
    case 'PLAYER_READY':
      return {
        ...state,
        currentState: SpotifyPlayerStates.READY,
        previousState: state.currentState,
        playerState: {
          ...state.playerState,
          isReady: true,
          deviceId: action.payload.deviceId
        }
      }
    
    case 'PLAY_TRACK':
      return {
        ...state,
        currentState: SpotifyPlayerStates.PLAYING,
        previousState: state.currentState,
        playerState: {
          ...state.playerState,
          isPlaying: true,
          isPaused: false,
          currentTrack: action.payload.track
        }
      }
    
    // ... additional cases for all actions
    
    default:
      return state
  }
}

// State machine hook
export const useSpotifyPlayerStateMachine = () => {
  const [state, dispatch] = useReducer(spotifyPlayerReducer, initialState)
  
  // State transition helpers
  const canTransitionTo = (targetState: SpotifyPlayerStates): boolean => {
    const validTransitions: Record<SpotifyPlayerStates, SpotifyPlayerStates[]> = {
      [SpotifyPlayerStates.INITIALIZING]: [
        SpotifyPlayerStates.UNAUTHENTICATED,
        SpotifyPlayerStates.ERROR
      ],
      [SpotifyPlayerStates.UNAUTHENTICATED]: [
        SpotifyPlayerStates.AUTHENTICATING,
        SpotifyPlayerStates.ERROR
      ],
      [SpotifyPlayerStates.AUTHENTICATING]: [
        SpotifyPlayerStates.NON_PREMIUM,
        SpotifyPlayerStates.DEVICE_INACTIVE,
        SpotifyPlayerStates.ERROR
      ],
      // ... define all valid transitions
    }
    
    return validTransitions[state.currentState]?.includes(targetState) ?? false
  }
  
  return {
    state,
    dispatch,
    canTransitionTo,
    isInState: (targetState: SpotifyPlayerStates) => state.currentState === targetState,
    isAuthenticated: state.authState.isAuthenticated,
    isPremium: state.authState.isPremium,
    isPlayerReady: state.playerState.isReady,
    currentTrack: state.playerState.currentTrack
  }
}
```

### Custom Hooks Integration

```typescript
// Main hook that combines all functionality
export const useUnifiedSpotifyPlayer = (props: SpotifyPlayerProps) => {
  const { socket } = useSocketContext()
  const { sdk, authState } = useSpotifyContext()
  const { player, controls } = useSpotifyPlayer()
  const stateMachine = useSpotifyPlayerStateMachine()
  
  // Socket event handlers
  const handleSocketEvents = useCallback(() => {
    if (!socket) return
    
    const eventHandlers = {
      'spotify_play_full_track': (data: any) => {
        stateMachine.dispatch(playerActions.playTrack(data.track))
      },
      'spotify_auth_required': () => {
        stateMachine.dispatch(authActions.startAuth())
      },
      'spotify_device_activated': () => {
        stateMachine.dispatch(playerActions.deviceActivated())
      }
    }
    
    Object.entries(eventHandlers).forEach(([event, handler]) => {
      socket.on(event, handler)
    })
    
    return () => {
      Object.keys(eventHandlers).forEach(event => {
        socket.off(event)
      })
    }
  }, [socket, stateMachine])
  
  useEffect(handleSocketEvents, [handleSocketEvents])
  
  // Sync external state with internal state machine
  useEffect(() => {
    if (authState.isAuthenticated && authState.isPremium) {
      stateMachine.dispatch(authActions.authSuccess(authState.user!, authState.isPremium))
    } else if (authState.error) {
      stateMachine.dispatch(authActions.authFailure(authState.error))
    }
  }, [authState, stateMachine])
  
  return {
    state: stateMachine.state,
    dispatch: stateMachine.dispatch,
    controls,
    player
  }
}
```

## Component Lifecycle and State Transitions

### State Flow Diagram

```
INITIALIZING → UNAUTHENTICATED → AUTHENTICATING → [NON_PREMIUM | DEVICE_INACTIVE]
                     ↑                                        ↓
                     ←------------- ERROR ←-------------------
                                    ↓
DEVICE_INACTIVE → READY → [PLAYING | PAUSED | SEARCHING]
```

### State Handlers

```typescript
// Component state handlers
export const stateHandlers = {
  [SpotifyPlayerStates.INITIALIZING]: {
    render: () => <LoadingScreen message="Initializing Spotify SDK..." />,
    onEnter: (dispatch: Dispatch<SpotifyPlayerAction>) => {
      // Initialize SDK and check auth status
      dispatch(authActions.startAuth())
    }
  },
  
  [SpotifyPlayerStates.UNAUTHENTICATED]: {
    render: (props: RenderProps) => (
      <AuthenticationPanel 
        onAuthenticate={() => props.dispatch(authActions.startAuth())}
        error={props.state.authState.error}
      />
    )
  },
  
  [SpotifyPlayerStates.NON_PREMIUM]: {
    render: (props: RenderProps) => (
      <PremiumRequiredPanel 
        onUpgrade={() => window.open('https://www.spotify.com/premium/')}
        onLogout={() => props.dispatch(authActions.logout())}
      />
    )
  },
  
  [SpotifyPlayerStates.DEVICE_INACTIVE]: {
    render: (props: RenderProps) => (
      <DeviceActivationPanel 
        onActivate={() => props.controls.activateDevice()}
        deviceId={props.state.playerState.deviceId}
      />
    )
  },
  
  [SpotifyPlayerStates.READY]: {
    render: (props: RenderProps) => (
      <MainPlayerInterface 
        state={props.state}
        dispatch={props.dispatch}
        controls={props.controls}
      />
    )
  },
  
  [SpotifyPlayerStates.PLAYING]: {
    render: (props: RenderProps) => (
      <MainPlayerInterface 
        state={props.state}
        dispatch={props.dispatch}
        controls={props.controls}
      />
    ),
    onEnter: (dispatch: Dispatch<SpotifyPlayerAction>, track: SpotifyTrack) => {
      // Emit to socket for NOW PLAYING integration
      if (socket) {
        socket.emit('music_command', {
          action: 'play',
          track_name: track.name,
          provider: 'spotify',
          spotify_data: track
        })
      }
    }
  }
}
```

## UI Layout and Component Structure

### Main Component Structure

```typescript
export const SpotifyPlayer: React.FC<SpotifyPlayerProps> = (props) => {
  const {
    className = '',
    compact = false,
    showSearch = true,
    showQueue = true
  } = props
  
  const { state, dispatch, controls } = useUnifiedSpotifyPlayer(props)
  
  // Get appropriate renderer for current state
  const StateRenderer = stateHandlers[state.currentState]?.render
  
  if (!StateRenderer) {
    return <ErrorPanel error="Invalid state" />
  }
  
  return (
    <div className={`spotify-player ${compact ? 'compact' : ''} ${className}`}>
      <div className="spotify-player__container">
        {/* State-specific content */}
        <StateRenderer 
          state={state}
          dispatch={dispatch}
          controls={controls}
          compact={compact}
          showSearch={showSearch}
          showQueue={showQueue}
        />
        
        {/* Global error overlay */}
        {state.uiState.error && (
          <ErrorOverlay 
            error={state.uiState.error}
            onDismiss={() => dispatch({ type: 'CLEAR_ERROR' })}
            onRetry={state.uiState.error.retryAction}
          />
        )}
        
        {/* Loading overlay for transitions */}
        {state.uiState.isTransitioning && (
          <TransitionOverlay message="Loading..." />
        )}
      </div>
    </div>
  )
}
```

### Sub-Components

```typescript
// Main player interface when ready/playing
const MainPlayerInterface: React.FC<RenderProps> = ({ 
  state, 
  dispatch, 
  controls, 
  compact,
  showSearch,
  showQueue 
}) => {
  const [activePanel, setActivePanel] = useState<'player' | 'search' | 'queue'>('player')
  
  return (
    <div className="main-player-interface">
      {/* Header with navigation tabs */}
      <PlayerHeader 
        activePanel={activePanel}
        onPanelChange={setActivePanel}
        showSearch={showSearch}
        showQueue={showQueue}
        compact={compact}
      />
      
      {/* Dynamic content based on active panel */}
      <div className="player-content">
        {activePanel === 'player' && (
          <PlayerPanel 
            playerState={state.playerState}
            controls={controls}
            dispatch={dispatch}
            compact={compact}
          />
        )}
        
        {activePanel === 'search' && showSearch && (
          <SearchPanel 
            searchState={state.searchState}
            dispatch={dispatch}
            controls={controls}
            compact={compact}
          />
        )}
        
        {activePanel === 'queue' && showQueue && (
          <QueuePanel 
            nextTracks={state.playerState.nextTracks}
            controls={controls}
            compact={compact}
          />
        )}
      </div>
    </div>
  )
}

// Player controls and current track display
const PlayerPanel: React.FC<PlayerPanelProps> = ({ 
  playerState, 
  controls, 
  dispatch, 
  compact 
}) => {
  return (
    <div className={`player-panel ${compact ? 'compact' : ''}`}>
      {/* Current track info */}
      {playerState.currentTrack && (
        <CurrentTrackDisplay 
          track={playerState.currentTrack}
          isPlaying={playerState.isPlaying}
          compact={compact}
        />
      )}
      
      {/* Progress bar */}
      <ProgressBar 
        position={playerState.position}
        duration={playerState.duration}
        onSeek={(position) => controls.seek(position)}
        disabled={!playerState.isReady}
      />
      
      {/* Playback controls */}
      <PlaybackControls 
        isPlaying={playerState.isPlaying}
        isPaused={playerState.isPaused}
        isReady={playerState.isReady}
        onPlay={() => controls.play()}
        onPause={() => controls.pause()}
        onNext={() => controls.skipToNext()}
        onPrevious={() => controls.skipToPrevious()}
        compact={compact}
      />
      
      {/* Volume control */}
      <VolumeControl 
        volume={playerState.volume}
        onVolumeChange={(volume) => controls.setVolume(volume)}
        compact={compact}
      />
    </div>
  )
}

// Integrated search interface
const SearchPanel: React.FC<SearchPanelProps> = ({ 
  searchState, 
  dispatch, 
  controls, 
  compact 
}) => {
  return (
    <div className={`search-panel ${compact ? 'compact' : ''}`}>
      {/* Search input with history */}
      <SearchInput 
        query={searchState.query}
        onQueryChange={(query) => dispatch(searchActions.startSearch(query))}
        history={searchState.searchHistory}
        compact={compact}
      />
      
      {/* Category tabs */}
      <CategoryTabs 
        selectedCategory={searchState.selectedCategory}
        onCategoryChange={(category) => dispatch(searchActions.selectCategory(category))}
        results={searchState.results}
        compact={compact}
      />
      
      {/* Results display */}
      <SearchResults 
        results={searchState.results}
        selectedCategory={searchState.selectedCategory}
        onTrackSelect={(track) => {
          dispatch(searchActions.selectTrack(track))
          dispatch(playerActions.playTrack(track))
        }}
        onAddToQueue={(track) => controls.addToQueue(track.uri)}
        compact={compact}
      />
    </div>
  )
}
```

## Integration with MusicTab

### Callback Props and Event Handling

```typescript
// Integration interface for MusicTab
export interface SpotifyPlayerIntegration {
  onTrackChange: (track: SpotifyTrack | null) => void
  onPlaybackStateChange: (state: PlaybackState) => void
  onProviderStatusChange: (status: ProviderStatus) => void
  onError: (error: SpotifyPlayerError) => void
}

// Usage in MusicTab
const MusicTab: React.FC = () => {
  const [currentSpotifyTrack, setCurrentSpotifyTrack] = useState<SpotifyTrack | null>(null)
  const [spotifyStatus, setSpotifyStatus] = useState<ProviderStatus>('offline')
  
  const handleSpotifyTrackChange = useCallback((track: SpotifyTrack | null) => {
    setCurrentSpotifyTrack(track)
    
    // Update unified NOW PLAYING display
    if (track) {
      setCurrentTrack({
        id: track.id,
        title: track.name,
        artist: track.artists.map(a => a.name).join(', '),
        duration: formatDuration(track.duration_ms),
        file: `${track.name} (Spotify)`,
        path: track.uri
      })
    }
  }, [])
  
  const handleSpotifyPlaybackStateChange = useCallback((state: PlaybackState) => {
    // Sync with MusicTab playback state
    setIsPlaying(state.isPlaying)
    setIsPaused(state.isPaused)
    
    // Update progress if needed
    if (state.position !== undefined) {
      setProgress((state.position / state.duration) * 100)
      setCurrentTime(formatTime(state.position / 1000))
    }
  }, [])
  
  const handleSpotifyProviderStatusChange = useCallback((status: ProviderStatus) => {
    setSpotifyStatus(status)
  }, [])
  
  return (
    <div className="music-tab">
      {/* Existing NOW PLAYING section */}
      <NowPlayingSection 
        currentTrack={currentTrack}
        isPlaying={isPlaying}
        progress={progress}
        // ... other props
      />
      
      {/* Provider selection with status indicators */}
      <ProviderSelection 
        currentProvider={currentProvider}
        onProviderChange={setCurrentProvider}
        localStatus={musicServiceStatus}
        spotifyStatus={spotifyStatus}
      />
      
      {/* Unified Spotify interface */}
      {currentProvider === 'spotify' && (
        <SpotifyPlayer 
          onTrackChange={handleSpotifyTrackChange}
          onPlaybackStateChange={handleSpotifyPlaybackStateChange}
          onProviderStatusChange={handleSpotifyProviderStatusChange}
          onError={(error) => console.error('Spotify error:', error)}
          compact={false}
          showSearch={true}
          showQueue={true}
        />
      )}
    </div>
  )
}
```

## Socket.io Communication Patterns

### Event Emission Strategy

```typescript
// Centralized socket communication service
export class SpotifySocketService {
  constructor(private socket: Socket, private dispatch: Dispatch<SpotifyPlayerAction>) {}
  
  // Outbound events (Dashboard → CantinaOS)
  emitTrackPlay(track: SpotifyTrack) {
    const musicCommand = {
      action: 'play',
      track_name: track.name,
      track_id: track.id,
      provider: 'spotify',
      spotify_data: {
        track_id: track.id,
        track_uri: track.uri,
        track_name: track.name,
        artist: track.artists.map(a => a.name).join(', '),
        album: track.album?.name,
        duration_ms: track.duration_ms,
        preview_url: track.preview_url,
        album_art: track.album?.images?.[0]?.url
      }
    }
    
    this.socket.emit('music_command', musicCommand)
  }
  
  emitPlayerStatus(state: SpotifyPlayerState) {
    this.socket.emit('spotify_player_status', {
      device_id: state.playerState.deviceId,
      is_ready: state.playerState.isReady,
      is_active: state.playerState.isActive,
      is_playing: state.playerState.isPlaying,
      current_track: state.playerState.currentTrack,
      volume: state.playerState.volume
    })
  }
  
  emitAuthStatus(authState: SpotifyAuthState) {
    this.socket.emit('spotify_auth_status_update', {
      authenticated: authState.isAuthenticated,
      premium: authState.isPremium,
      user_id: authState.user?.id,
      error: authState.error
    })
  }
  
  // Inbound event handlers (CantinaOS → Dashboard)
  setupEventListeners() {
    const handlers = {
      'spotify_play_full_track': (data: any) => {
        this.dispatch(playerActions.playTrack(data.track))
      },
      
      'spotify_pause_track': () => {
        this.dispatch(playerActions.pauseTrack())
      },
      
      'spotify_resume_track': () => {
        this.dispatch(playerActions.resumeTrack())
      },
      
      'spotify_volume_change': (data: { volume: number }) => {
        this.dispatch(playerActions.setVolume(data.volume))
      },
      
      'spotify_auth_required': () => {
        this.dispatch(authActions.startAuth())
      },
      
      'spotify_device_activation_required': () => {
        this.dispatch({ type: 'REQUEST_DEVICE_ACTIVATION' })
      }
    }
    
    Object.entries(handlers).forEach(([event, handler]) => {
      this.socket.on(event, handler)
    })
    
    return () => {
      Object.keys(handlers).forEach(event => {
        this.socket.off(event)
      })
    }
  }
}
```

## Error Handling and Loading States

### Error Recovery System

```typescript
// Error classification and recovery
export const errorHandlers = {
  auth: {
    classify: (error: any): SpotifyPlayerError => ({
      type: 'auth',
      message: 'Authentication failed. Please try logging in again.',
      recoverable: true,
      retryAction: async () => {
        // Retry authentication flow
      }
    }),
    
    recover: async (error: SpotifyPlayerError, dispatch: Dispatch<SpotifyPlayerAction>) => {
      dispatch(authActions.startAuth())
    }
  },
  
  player: {
    classify: (error: any): SpotifyPlayerError => ({
      type: 'player',
      message: error.message || 'Player initialization failed',
      recoverable: true,
      retryAction: async () => {
        // Retry player initialization
      }
    }),
    
    recover: async (error: SpotifyPlayerError, dispatch: Dispatch<SpotifyPlayerAction>) => {
      dispatch(playerActions.initializePlayer())
    }
  },
  
  network: {
    classify: (error: any): SpotifyPlayerError => ({
      type: 'network',
      message: 'Network connection failed. Please check your internet connection.',
      recoverable: true,
      retryAction: async () => {
        // Retry last action
      }
    }),
    
    recover: async (error: SpotifyPlayerError, dispatch: Dispatch<SpotifyPlayerAction>) => {
      // Implement exponential backoff retry
      await new Promise(resolve => setTimeout(resolve, 2000))
      if (error.retryAction) {
        await error.retryAction()
      }
    }
  }
}

// Loading state management
export const LoadingStateManager = {
  states: {
    'initializing': 'Initializing Spotify SDK...',
    'authenticating': 'Connecting to Spotify...',
    'loading_player': 'Loading Web Player...',
    'activating_device': 'Activating playback device...',
    'searching': 'Searching Spotify library...',
    'loading_track': 'Loading track...'
  },
  
  getLoadingMessage: (state: SpotifyPlayerStates): string => {
    return LoadingStateManager.states[state] || 'Loading...'
  }
}
```

## Implementation Roadmap

### Phase 1: Core State Machine (Week 1)
- [ ] Implement state machine reducer and actions
- [ ] Create useSpotifyPlayerStateMachine hook
- [ ] Build state transition logic and validation
- [ ] Add comprehensive TypeScript interfaces
- [ ] Unit tests for state machine logic

### Phase 2: Authentication & Player Setup (Week 1)
- [ ] Integrate existing SpotifyContext authentication
- [ ] Implement device activation flow
- [ ] Add premium status checking
- [ ] Error handling for auth failures
- [ ] Integration tests with existing useSpotifyPlayer hook

### Phase 3: UI Components (Week 2)
- [ ] Build state-specific render components
- [ ] Create MainPlayerInterface with panel switching
- [ ] Implement PlayerPanel with controls
- [ ] Add responsive design and compact mode
- [ ] Component testing with React Testing Library

### Phase 4: Search Integration (Week 2)
- [ ] Integrate existing SpotifySearch functionality
- [ ] Build SearchPanel with category tabs
- [ ] Add search history and debouncing
- [ ] Implement track selection and queuing
- [ ] Search performance optimization

### Phase 5: Socket Integration (Week 3)
- [ ] Create SpotifySocketService class
- [ ] Implement bidirectional event communication
- [ ] Add socket event debugging and logging
- [ ] Integration with CantinaOS event system
- [ ] End-to-end testing with bridge

### Phase 6: MusicTab Integration (Week 3)
- [ ] Update MusicTab to use unified component
- [ ] Implement callback prop integration
- [ ] Add provider status synchronization
- [ ] Update NOW PLAYING section integration
- [ ] Regression testing for existing functionality

### Phase 7: Error Handling & Polish (Week 4)
- [ ] Implement comprehensive error recovery
- [ ] Add loading state management
- [ ] Performance optimization and memoization
- [ ] Accessibility improvements (ARIA labels, keyboard navigation)
- [ ] Documentation and examples

### Phase 8: Advanced Features (Week 4)
- [ ] Queue management interface
- [ ] Playlist integration
- [ ] Offline mode handling
- [ ] User preferences persistence
- [ ] Analytics and usage tracking

## Testing Strategy

### Unit Tests
- State machine reducer logic
- Action creator functions
- Individual component rendering
- Hook behavior and side effects
- Error handling and recovery

### Integration Tests
- Component state transitions
- Socket event communication
- Authentication flow
- Player initialization sequence
- Search and playback integration

### End-to-End Tests
- Complete user authentication flow
- Track search and playback
- Error scenarios and recovery
- Cross-browser compatibility
- Performance benchmarks

## Performance Considerations

### Optimization Strategies
- Lazy loading of search results
- Debounced search input (300ms)
- Memoized component renders
- Virtual scrolling for large result sets
- Efficient state updates with immer
- Background prefetching of album art

### Memory Management
- Cleanup of socket event listeners
- Spotify Player instance disposal
- Search result cache management
- Audio stream cleanup on unmount

## Accessibility Features

### ARIA Support
- Proper labeling of all interactive elements
- Screen reader announcements for state changes
- Keyboard navigation support
- Focus management during state transitions

### Keyboard Shortcuts
- Space: Play/Pause
- Left/Right arrows: Seek
- Up/Down arrows: Volume
- Enter: Select track
- Escape: Close modals/search

This comprehensive design provides a solid foundation for implementing the unified SpotifyPlayer component while maintaining compatibility with existing architecture and enabling future extensibility.