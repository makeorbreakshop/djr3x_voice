'use client'

import React, { useState, useEffect, useCallback } from 'react'
import { useSpotifyContext } from '@/contexts/SpotifyContext'
import { useSpotifyPlayer } from '@/hooks/useSpotifyPlayer'
import { useSocketContext } from '@/contexts/SocketContext'
import { 
  PlayIcon, 
  PauseIcon, 
  ForwardIcon, 
  BackwardIcon,
  SpeakerWaveIcon,
  SpeakerXMarkIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
  MagnifyingGlassIcon,
  ClockIcon
} from '@heroicons/react/24/solid'

// Spotify API interfaces
export interface SpotifyTrack {
  id: string
  name: string
  artists: Array<{ id: string; name: string; uri: string }>
  album?: {
    id: string
    name: string
    images?: Array<{ url: string }>
  }
  duration_ms: number
  uri: string
  preview_url?: string | null
  popularity?: number
  explicit?: boolean
}

export interface SpotifyAlbum {
  id: string
  name: string
  artists: Array<{ id: string; name: string }>
  images?: Array<{ url: string }>
  total_tracks?: number
  release_date?: string
}

export interface SpotifyArtist {
  id: string
  name: string
  images?: Array<{ url: string }>
  followers?: { total: number }
  genres?: string[]
}

export interface SpotifyPlaylist {
  id: string
  name: string
  owner?: { display_name: string }
  images?: Array<{ url: string }>
  tracks?: { total: number }
}

export interface SpotifySearchResult {
  tracks: SpotifyTrack[]
  albums: SpotifyAlbum[]
  artists: SpotifyArtist[]
  playlists: SpotifyPlaylist[]
}

export interface SpotifyPlayerProps {
  onTrackSelect?: (track: SpotifyTrack) => void
  onSearchResults?: (results: SpotifySearchResult) => void
  className?: string
}

export const SpotifyPlayer: React.FC<SpotifyPlayerProps> = ({
  onTrackSelect,
  onSearchResults,
  className = ''
}) => {
  const { authState, authenticate, logout, sdk } = useSpotifyContext()
  const { player, state, controls, isSDKReady, initializePlayer, disconnectPlayer } = useSpotifyPlayer()
  const { socket } = useSocketContext()
  
  // UI State
  const [showVolumeSlider, setShowVolumeSlider] = useState(false)
  const [currentView, setCurrentView] = useState<'auth' | 'loading' | 'search' | 'player'>('auth')
  
  // Search State
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState<SpotifySearchResult>({
    tracks: [],
    albums: [],
    artists: [],
    playlists: []
  })
  const [isSearching, setIsSearching] = useState(false)
  const [searchHistory, setSearchHistory] = useState<string[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [selectedCategory, setSelectedCategory] = useState<'tracks' | 'albums' | 'artists' | 'playlists'>('tracks')

  // Determine current view based on state
  useEffect(() => {
    if (!authState.isAuthenticated) {
      setCurrentView('auth')
    } else if (!authState.isPremium) {
      setCurrentView('auth') // Will show premium requirement
    } else if (authState.isLoading || !isSDKReady || state.isLoading) {
      setCurrentView('loading')
    } else if (state.isReady) {
      setCurrentView('search')
    } else {
      setCurrentView('loading')
    }
  }, [authState, isSDKReady, state])

  // Load search history from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem('spotify-search-history')
      if (saved) {
        setSearchHistory(JSON.parse(saved))
      }
    } catch (error) {
      console.error('Failed to load search history:', error)
    }
  }, [])

  // Save search history to localStorage
  const saveSearchHistory = useCallback((history: string[]) => {
    try {
      localStorage.setItem('spotify-search-history', JSON.stringify(history))
    } catch (error) {
      console.error('Failed to save search history:', error)
    }
  }, [])

  // Search functionality
  const performSearch = useCallback(async (query: string) => {
    if (!sdk || !authState.isAuthenticated || !query.trim()) return

    try {
      setIsSearching(true)

      const results = await sdk.search(
        query,
        ['track', 'album', 'artist', 'playlist'],
        'US',
        20
      )

      const searchResult: SpotifySearchResult = {
        tracks: results.tracks.items,
        albums: results.albums.items,
        artists: results.artists.items,
        playlists: results.playlists.items
      }

      setSearchResults(searchResult)
      onSearchResults?.(searchResult)

      // Add to search history
      if (query.trim() && !searchHistory.includes(query.trim())) {
        const newHistory = [query.trim(), ...searchHistory.slice(0, 9)]
        setSearchHistory(newHistory)
        saveSearchHistory(newHistory)
      }
    } catch (error) {
      console.error('Search failed:', error)
      setSearchResults({ tracks: [], albums: [], artists: [], playlists: [] })
    } finally {
      setIsSearching(false)
    }
  }, [sdk, authState.isAuthenticated, searchHistory, saveSearchHistory, onSearchResults])

  // Debounced search
  useEffect(() => {
    if (!searchTerm.trim() || !sdk || !authState.isAuthenticated) {
      setSearchResults({ tracks: [], albums: [], artists: [], playlists: [] })
      return
    }

    const timeoutId = setTimeout(() => {
      performSearch(searchTerm)
    }, 300)

    return () => clearTimeout(timeoutId)
  }, [searchTerm, sdk, authState.isAuthenticated, performSearch])

  // Helper functions
  const formatTime = (ms: number): string => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  const getProgressPercentage = (): number => {
    if (state.duration === 0) return 0
    return (state.position / state.duration) * 100
  }

  const getTrackQualityIndicator = (track: SpotifyTrack): string => {
    if (authState.isPremium) {
      return '🎵 Full Song'
    }
    return track.preview_url ? '🔍 Preview' : '🚫 No Preview'
  }

  // Event handlers
  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (state.duration === 0) return
    
    const rect = e.currentTarget.getBoundingClientRect()
    const clickX = e.clientX - rect.left
    const percentage = clickX / rect.width
    const newPosition = Math.floor(percentage * state.duration)
    
    controls.seek(newPosition).catch(console.error)
  }

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVolume = parseFloat(e.target.value)
    controls.setVolume(newVolume).catch(console.error)
  }

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (searchTerm.trim()) {
      performSearch(searchTerm)
      setShowHistory(false)
    }
  }

  const handleHistorySelect = (term: string) => {
    setSearchTerm(term)
    setShowHistory(false)
    performSearch(term)
  }

  const clearSearchHistory = () => {
    setSearchHistory([])
    saveSearchHistory([])
  }

  const handleTrackPlay = async (track: SpotifyTrack) => {
    try {
      console.log('🎵 [SpotifyPlayer] handleTrackPlay called for track:', track.name)
      
      if (socket) {
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
            album_art: track.album?.images?.[0]?.url,
            has_premium: authState.isPremium,
            is_explicit: track.explicit,
            device_id: state.deviceId
          }
        }
        
        socket.emit('music_command', musicCommand)
        console.log('🎵 [SpotifyPlayer] ✅ Successfully emitted music_command')
      }
      
      onTrackSelect?.(track)
    } catch (error) {
      console.error('🎵 [SpotifyPlayer] ❌ Failed to play track:', error)
    }
  }

  // Authentication view
  if (currentView === 'auth') {
    if (!authState.isAuthenticated) {
      return (
        <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
          <div className="text-center">
            <h3 className="text-lg font-semibold text-sw-blue-200 mb-4">
              Spotify Authentication Required
            </h3>
            
            {authState.isLoading ? (
              <div className="flex items-center justify-center space-x-2">
                <ArrowPathIcon className="h-5 w-5 text-sw-blue-400 animate-spin" />
                <span className="text-sw-blue-300">Connecting to Spotify...</span>
              </div>
            ) : (
              <>
                <p className="text-sw-blue-300 mb-4">
                  Connect your Spotify Premium account to enable Web Playback
                </p>
                
                {authState.error && (
                  <div className="bg-red-900/20 border border-red-600/30 rounded-lg p-3 mb-4">
                    <div className="flex items-center space-x-2">
                      <ExclamationTriangleIcon className="h-5 w-5 text-red-400" />
                      <span className="text-red-300 text-sm">{authState.error}</span>
                    </div>
                  </div>
                )}
                
                <button
                  onClick={authenticate}
                  disabled={authState.isLoading}
                  className="bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium py-2 px-6 rounded-lg transition-colors"
                >
                  Connect Spotify
                </button>
              </>
            )}
          </div>
        </div>
      )
    }

    // Premium requirement check
    if (!authState.isPremium) {
      return (
        <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
          <div className="text-center">
            <h3 className="text-lg font-semibold text-yellow-400 mb-4">
              Spotify Premium Required
            </h3>
            <p className="text-sw-blue-300 mb-4">
              Web Playback SDK requires a Spotify Premium subscription
            </p>
            <div className="flex justify-center space-x-4">
              <a
                href="https://www.spotify.com/premium/"
                target="_blank"
                rel="noopener noreferrer"
                className="bg-green-600 hover:bg-green-700 text-white font-medium py-2 px-4 rounded-lg transition-colors"
              >
                Upgrade to Premium
              </a>
              <button
                onClick={logout}
                className="bg-sw-dark-600 hover:bg-sw-dark-700 text-sw-blue-300 font-medium py-2 px-4 rounded-lg transition-colors"
              >
                Disconnect
              </button>
            </div>
          </div>
        </div>
      )
    }
  }

  // Loading view
  if (currentView === 'loading') {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <ArrowPathIcon className="h-8 w-8 text-sw-blue-400 animate-spin mx-auto mb-4" />
          <p className="text-sw-blue-300">
            {!isSDKReady ? 'Loading Spotify SDK...' : 'Initializing player...'}
          </p>
        </div>
      </div>
    )
  }

  // Error state
  if (state.error) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <ExclamationTriangleIcon className="h-8 w-8 text-red-400 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-red-400 mb-2">Player Error</h3>
          <p className="text-red-300 mb-4">{state.error}</p>
          <div className="flex justify-center space-x-4">
            <button
              onClick={() => initializePlayer().catch(console.error)}
              className="bg-sw-blue-600 hover:bg-sw-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors"
            >
              Retry
            </button>
            <button
              onClick={disconnectPlayer}
              className="bg-sw-dark-600 hover:bg-sw-dark-700 text-sw-blue-300 font-medium py-2 px-4 rounded-lg transition-colors"
            >
              Reset
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Device activation needed
  if (state.isReady && !state.isActive) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <h3 className="text-lg font-semibold text-sw-blue-200 mb-4">
            Activate DJ R3X Web Player
          </h3>
          <p className="text-sw-blue-300 mb-4">
            Click below to make this your active Spotify device
          </p>
          <button
            onClick={() => controls.activateDevice().catch(console.error)}
            className="bg-sw-blue-600 hover:bg-sw-blue-700 text-white font-medium py-2 px-6 rounded-lg transition-colors"
          >
            Activate Device
          </button>
        </div>
      </div>
    )
  }

  // Main search and player interface
  return (
    <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 space-y-6 ${className}`}>
      {/* Header with Status */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-sw-blue-200">
          DJ R3X Spotify Player
        </h3>
        <div className="flex items-center space-x-2">
          <div className={`w-2 h-2 rounded-full ${state.isReady ? 'bg-green-400' : 'bg-red-400'}`} />
          <span className="text-sm text-sw-blue-300">
            {state.isReady ? 'Ready' : 'Not Ready'}
          </span>
        </div>
      </div>

      {/* Current Track Info */}
      {state.currentTrack && (
        <div className="p-4 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20">
          <div className="flex items-center space-x-4">
            {state.currentTrack.album?.images?.[0] && (
              <img
                src={state.currentTrack.album.images[0].url}
                alt={state.currentTrack.album.name}
                className="w-16 h-16 rounded-lg shadow-lg"
              />
            )}
            <div className="flex-1 min-w-0">
              <h4 className="text-lg font-medium text-sw-blue-100 truncate">
                {state.currentTrack.name}
              </h4>
              <p className="text-sm text-sw-blue-300 truncate">
                {state.currentTrack.artists.map((artist: any) => artist.name).join(', ')}
              </p>
              {state.currentTrack.album && (
                <p className="text-xs text-sw-blue-400 truncate">
                  {state.currentTrack.album.name}
                </p>
              )}
            </div>
          </div>

          {/* Progress Bar */}
          <div className="mt-4">
            <div className="flex items-center space-x-2 mb-2">
              <span className="text-xs text-sw-blue-400">
                {formatTime(state.position)}
              </span>
              <div 
                className="flex-1 h-2 bg-sw-dark-600 rounded-full cursor-pointer"
                onClick={handleSeek}
              >
                <div 
                  className="h-full bg-sw-blue-500 rounded-full transition-all duration-300"
                  style={{ width: `${getProgressPercentage()}%` }}
                />
              </div>
              <span className="text-xs text-sw-blue-400">
                {formatTime(state.duration)}
              </span>
            </div>
          </div>

          {/* Playback Controls */}
          <div className="flex items-center justify-center space-x-4 mt-4">
            <button
              onClick={() => controls.skipToPrevious().catch(console.error)}
              disabled={!state.isReady}
              className="p-2 text-sw-blue-300 hover:text-sw-blue-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              title="Previous Track"
            >
              <BackwardIcon className="h-6 w-6" />
            </button>

            <button
              onClick={() => controls.togglePlayPause().catch(console.error)}
              disabled={!state.isReady}
              className="p-3 bg-sw-blue-600 hover:bg-sw-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-full transition-colors"
              title={state.isPlaying ? 'Pause' : 'Play'}
            >
              {state.isPlaying ? (
                <PauseIcon className="h-6 w-6" />
              ) : (
                <PlayIcon className="h-6 w-6" />
              )}
            </button>

            <button
              onClick={() => controls.skipToNext().catch(console.error)}
              disabled={!state.isReady}
              className="p-2 text-sw-blue-300 hover:text-sw-blue-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              title="Next Track"
            >
              <ForwardIcon className="h-6 w-6" />
            </button>
          </div>

          {/* Volume Control */}
          <div className="flex items-center justify-center space-x-2 mt-4">
            <button
              onClick={() => setShowVolumeSlider(!showVolumeSlider)}
              className="p-1 text-sw-blue-300 hover:text-sw-blue-100 transition-colors"
              title={`Volume: ${Math.round(state.volume * 100)}%`}
            >
              {state.volume === 0 ? (
                <SpeakerXMarkIcon className="h-5 w-5" />
              ) : (
                <SpeakerWaveIcon className="h-5 w-5" />
              )}
            </button>
            
            {showVolumeSlider && (
              <div className="flex items-center space-x-2">
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={state.volume}
                  onChange={handleVolumeChange}
                  className="w-20 h-1 bg-sw-dark-600 rounded-lg appearance-none cursor-pointer"
                />
                <span className="text-xs text-sw-blue-400 w-8 text-right">
                  {Math.round(state.volume * 100)}%
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Search Interface */}
      <div className="space-y-4">
        <h4 className="text-md font-medium text-sw-blue-200">Search Spotify</h4>
        
        {/* Search Input */}
        <form onSubmit={handleSearchSubmit} className="relative">
          <div className="relative">
            <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-sw-blue-400" />
            <input
              type="text"
              placeholder="Search tracks, artists, albums, playlists..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              onFocus={() => setShowHistory(searchHistory.length > 0)}
              className="w-full pl-10 pr-4 py-2 bg-sw-dark-700 border border-sw-blue-600/30 rounded-lg text-sw-blue-100 placeholder-sw-blue-400 focus:outline-none focus:border-sw-blue-500 focus:ring-1 focus:ring-sw-blue-500"
            />
            {isSearching && (
              <div className="absolute right-3 top-1/2 transform -translate-y-1/2">
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-sw-blue-500"></div>
              </div>
            )}
          </div>

          {/* Search History Dropdown */}
          {showHistory && searchHistory.length > 0 && (
            <div className="absolute z-10 w-full mt-1 bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg shadow-lg">
              <div className="p-2 border-b border-sw-blue-600/20">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-sw-blue-400 font-medium">Recent Searches</span>
                  <button
                    type="button"
                    onClick={clearSearchHistory}
                    className="text-xs text-sw-blue-400 hover:text-sw-blue-300"
                  >
                    Clear
                  </button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto">
                {searchHistory.map((term, index) => (
                  <button
                    key={index}
                    type="button"
                    onClick={() => handleHistorySelect(term)}
                    className="w-full px-3 py-2 text-left text-sw-blue-200 hover:bg-sw-dark-700 flex items-center space-x-2"
                  >
                    <ClockIcon className="h-3 w-3 text-sw-blue-400" />
                    <span className="truncate">{term}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </form>

        {/* Category Tabs */}
        {(searchResults.tracks.length > 0 || searchResults.albums.length > 0 || 
          searchResults.artists.length > 0 || searchResults.playlists.length > 0) && (
          <div className="flex space-x-1 bg-sw-dark-700/50 rounded-lg p-1">
            {[
              { key: 'tracks' as const, label: 'Tracks', count: searchResults.tracks.length },
              { key: 'albums' as const, label: 'Albums', count: searchResults.albums.length },
              { key: 'artists' as const, label: 'Artists', count: searchResults.artists.length },
              { key: 'playlists' as const, label: 'Playlists', count: searchResults.playlists.length }
            ].map(({ key, label, count }) => (
              <button
                key={key}
                onClick={() => setSelectedCategory(key)}
                className={`px-3 py-2 text-sm font-medium rounded-md transition-colors ${
                  selectedCategory === key
                    ? 'bg-sw-blue-600 text-white'
                    : 'text-sw-blue-300 hover:text-sw-blue-100 hover:bg-sw-dark-600'
                }`}
              >
                {label} ({count})
              </button>
            ))}
          </div>
        )}

        {/* Search Results */}
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {selectedCategory === 'tracks' && searchResults.tracks.map((track) => (
            <div
              key={track.id}
              className="p-3 bg-sw-dark-700/30 border border-sw-blue-600/20 rounded-lg hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50 transition-all cursor-pointer"
              onClick={() => handleTrackPlay(track)}
            >
              <div className="flex items-center space-x-3">
                {track.album?.images?.[2] && (
                  <img
                    src={track.album.images[2].url}
                    alt={track.album.name}
                    className="w-12 h-12 rounded shadow-lg"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <h4 className="font-medium text-sw-blue-100 truncate">{track.name}</h4>
                  <p className="text-sm text-sw-blue-300 truncate">
                    {track.artists.map(artist => artist.name).join(', ')}
                  </p>
                  <p className="text-xs text-sw-blue-400 truncate">{track.album?.name}</p>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className="text-sm text-sw-blue-300">{formatTime(track.duration_ms)}</p>
                  <p className="text-xs text-sw-blue-400">{getTrackQualityIndicator(track)}</p>
                </div>
              </div>
            </div>
          ))}

          {/* No Results */}
          {searchTerm && !isSearching && 
           searchResults.tracks.length === 0 && 
           searchResults.albums.length === 0 && 
           searchResults.artists.length === 0 && 
           searchResults.playlists.length === 0 && (
            <div className="text-center py-8 text-sw-blue-300/50">
              No results found for &quot;{searchTerm}&quot;
            </div>
          )}

          {/* Empty State */}
          {!searchTerm && (
            <div className="text-center py-8 text-sw-blue-300/50">
              Start typing to search Spotify&apos;s library...
            </div>
          )}
        </div>
      </div>

      {/* Status Information */}
      {state.isBuffering && (
        <div className="flex items-center justify-center space-x-2">
          <ArrowPathIcon className="h-4 w-4 text-sw-blue-400 animate-spin" />
          <span className="text-sm text-sw-blue-300">Buffering...</span>
        </div>
      )}

      {/* Device Information */}
      <div className="pt-4 border-t border-sw-blue-600/20">
        <div className="flex items-center justify-between text-xs text-sw-blue-400">
          <span>Device: {state.deviceId ? `DJ R3X (${state.deviceId.slice(0, 8)}...)` : 'Unknown'}</span>
          <span>Status: {state.isActive ? 'Active' : 'Inactive'}</span>
        </div>
      </div>
    </div>
  )
}