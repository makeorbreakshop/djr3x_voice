'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'
import { useSpotifyContext } from '../../contexts/SpotifyContext'
import { SpotifyWebPlayer } from '../spotify/SpotifyWebPlayer'
import { SpotifySearch, SpotifySearchResult, SpotifyTrack } from '../spotify/SpotifySearch'
import { SpotifyTrackResults } from '../spotify/SpotifyTrackResults'

interface Track {
  id: string
  title: string
  artist: string
  duration: string
  file: string
  path?: string
}

interface MusicStatus {
  action: string
  track?: any
  track_name?: string
  volume?: number
}

type MusicProvider = 'local' | 'spotify'

export default function MusicTab() {
  const { socket } = useSocketContext()
  const { authState } = useSpotifyContext()
  const [currentProvider, setCurrentProvider] = useState<MusicProvider>('local')
  const [isPlaying, setIsPlaying] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null)
  const [volume, setVolume] = useState(75)
  const [progress, setProgress] = useState(0)
  const [currentTime, setCurrentTime] = useState('0:00')
  const [isClient, setIsClient] = useState(false)
  const [searchTerm, setSearchTerm] = useState('')
  const [tracks, setTracks] = useState<Track[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [musicServiceStatus, setMusicServiceStatus] = useState('offline')
  const [vlcBackendStatus, setVlcBackendStatus] = useState('offline')
  const [queue, setQueue] = useState<Track[]>([])
  const [ducking, setDucking] = useState(false)
  const [spotifySearchResults, setSpotifySearchResults] = useState<SpotifySearchResult>({
    tracks: [],
    albums: [],
    artists: [],
    playlists: []
  })
  const [selectedSpotifyTrack, setSelectedSpotifyTrack] = useState<SpotifyTrack | null>(null)

  // --- REBUILT PROGRESS TRACKING SYSTEM ---
  const timerRef = useRef<NodeJS.Timeout | null>(null)
  const timingDataRef = useRef<{
    startTime: number | null
    duration: number | null
  }>({ startTime: null, duration: null })

  // This is the single, stable function for the timer.
  // It reads the latest data from the ref, preventing stale closures.
  const updateProgress = useCallback(() => {
    const { startTime, duration } = timingDataRef.current
    if (startTime === null || duration === null || isPaused) {
      return
    }

    const elapsed = (Date.now() / 1000) - startTime
    const currentPosition = Math.max(0, Math.min(elapsed, duration))
    const progressPercent = (currentPosition / duration) * 100

    setProgress(progressPercent)
    setCurrentTime(formatTime(currentPosition))

    if (currentPosition >= duration) {
      setIsPlaying(false)
      setIsPaused(false)
    }
  }, [isPaused]) // Only depends on isPaused state

  // This effect is the single source of truth for managing the timer's lifecycle.
  useEffect(() => {
    if (isPlaying && !isPaused) {
      // Start the timer with 100ms intervals for smooth progress
      timerRef.current = setInterval(updateProgress, 100)
    } else {
      // Stop the timer
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }

    // Cleanup function to stop the timer when the component unmounts
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
      }
    }
  }, [isPlaying, isPaused, updateProgress])

  const resetProgress = useCallback(() => {
    timingDataRef.current = { startTime: null, duration: null }
    setProgress(0)
    setCurrentTime('0:00')
  }, [])
  // --- END REBUILT SYSTEM ---

  // Debounce mechanism to prevent rapid track selection
  const [isSelectingTrack, setIsSelectingTrack] = useState(false)
  const trackSelectionTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // Format time helper
  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${String(secs).padStart(2, '0')}`
  }

  // Client-side hydration fix
  useEffect(() => {
    setIsClient(true)
  }, [])

  // Load music library on component mount
  useEffect(() => {
    loadMusicLibrary()
  }, [])

  // Socket event listeners
  useEffect(() => {
    if (!socket) return

    const handleMusicStatus = (data: any) => {
      // WebBridge wraps payload in {topic, data, timestamp} structure
      const musicData = data.data || data
      
      if (musicData.action === 'started') {
        setIsPaused(false)
        setIsSelectingTrack(false)
        
        if (musicData.track) {
          // Extract filename from filepath for display
          const filename = musicData.track.filepath ? musicData.track.filepath.split('/').pop() || musicData.track.title : musicData.track.title;
          
          const track: Track = {
            id: musicData.track.track_id || musicData.track.title || '',
            title: musicData.track.title || '',
            artist: musicData.track.artist || 'Unknown Artist',
            duration: musicData.track.duration ? `${Math.floor(musicData.track.duration / 60)}:${String(Math.floor(musicData.track.duration % 60)).padStart(2, '0')}` : '0:00',
            file: filename,
            path: musicData.track.filepath || ''
          }
          setCurrentTrack(track)
          
          // Store backend timing data directly in the ref
          const duration = musicData.duration || musicData.track?.duration
          const startTimestamp = musicData.start_timestamp
          
          if (startTimestamp) {
            timingDataRef.current = {
              startTime: startTimestamp,
              duration: (duration && duration > 0) ? duration : 180,
            }
            // Set isPlaying to true, which will trigger the useEffect to start the timer.
            setIsPlaying(true)
            // Immediately update progress to prevent "nothing happens" feel
            updateProgress()
          } else {
            console.error('🎵 [MusicTab] No start_timestamp provided in music status event')
          }
        }
      } else if (musicData.action === 'stopped') {
        setIsPlaying(false)
        setIsPaused(false)
        setCurrentTrack(null)
        setIsSelectingTrack(false)
        resetProgress()
      } else if (musicData.action === 'paused') {
        setIsPlaying(false)
        setIsPaused(true)
      } else if (musicData.action === 'resumed') {
        // Backend sends new start_timestamp on resume - use it
        const newStartTimestamp = musicData.start_timestamp
        if (newStartTimestamp) {
          timingDataRef.current.startTime = newStartTimestamp
        }
        setIsPaused(false)
        setIsPlaying(true)
      }
      
      if (musicData.volume !== undefined) {
        setVolume(musicData.volume)
      }
    }

    const handleMusicLibraryUpdated = (data: any) => {
      loadMusicLibrary()
    }

    const handleMusicProgress = (data: any) => {
      // Server-side progress events are ignored - using client-side calculation
    }

    const handleServiceStatus = (data: any) => {
      if (data.service === 'MusicController') {
        setMusicServiceStatus(data.status === 'RUNNING' ? 'online' : 'offline')
      }
    }

    const handleMusicQueue = (data: any) => {
      // Handle queue updates from backend
      const queueData = data.data || data
      if (queueData.action === 'queue_updated') {
        // Note: We're managing queue locally for UI responsiveness
        // Backend queue is separate for actual playback logic
      }
    }

    // Spotify event handlers
    const handleSpotifyPlayFullTrack = (data: any) => {
      console.log('Received Spotify full track play request:', data)
      // This event comes from CantinaOS requesting the dashboard to play a full track
      // The SpotifyWebPlayer component should handle this automatically
    }

    const handleSpotifyOfferLocalAlternative = (data: any) => {
      console.log('Spotify offering local alternative:', data)
      const { requested_track, requested_artist, reason } = data
      
      // Show user a notification about the local alternative suggestion
      // For now, just log it - could implement a toast notification later
      console.log(`Spotify unavailable (${reason}). Consider adding "${requested_track}" by ${requested_artist} to your local library.`)
    }

    socket.on('music_status', handleMusicStatus)
    socket.on('music_progress', handleMusicProgress)
    socket.on('music_library_updated', handleMusicLibraryUpdated)
    socket.on('music_queue', handleMusicQueue)
    socket.on('service_status_update', handleServiceStatus)
    socket.on('spotify_play_full_track', handleSpotifyPlayFullTrack)
    socket.on('spotify_offer_local_alternative', handleSpotifyOfferLocalAlternative)

    return () => {
      socket.off('music_status', handleMusicStatus)
      socket.off('music_progress', handleMusicProgress)
      socket.off('music_library_updated', handleMusicLibraryUpdated)
      socket.off('music_queue', handleMusicQueue)
      socket.off('service_status_update', handleServiceStatus)
      socket.off('spotify_play_full_track', handleSpotifyPlayFullTrack)
      socket.off('spotify_offer_local_alternative', handleSpotifyOfferLocalAlternative)
    }
  }, [socket, resetProgress, updateProgress])

  const loadMusicLibrary = async () => {
    try {
      setIsLoading(true)
      const response = await fetch('http://localhost:8000/api/music/library')
      const data = await response.json()
      
      if (data.tracks) {
        setTracks(data.tracks)
      }
    } catch (error) {
      console.error('Error loading music library:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const filteredTracks = tracks.filter(track =>
    track.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
    track.artist.toLowerCase().includes(searchTerm.toLowerCase())
  )

  const handlePlayPause = () => {
    if (!socket) return

    if (isPlaying) {
      // Currently playing, so pause it
      socket.emit('music_command', {
        action: 'pause'
      })
    } else if (isPaused && currentTrack) {
      // Currently paused, so resume
      socket.emit('music_command', {
        action: 'resume'
      })
    } else if (currentTrack) {
      // Not playing and not paused, so start playing
      socket.emit('music_command', {
        action: 'play',
        track_name: currentTrack.title,
        track_id: currentTrack.id
      })
    }
  }

  const handleTrackSelect = useCallback((track: Track) => {
    if (!socket) return

    // Prevent rapid track selection
    if (isSelectingTrack) {
      console.log('Track selection in progress, ignoring click')
      return
    }

    // Clear any existing timeout
    if (trackSelectionTimeoutRef.current) {
      clearTimeout(trackSelectionTimeoutRef.current)
    }

    // Set selecting state
    setIsSelectingTrack(true)

    // Send the command
    socket.emit('music_command', {
      action: 'play',
      track_name: track.title,
      track_id: track.id
    })

    // Reset selecting state after a delay
    trackSelectionTimeoutRef.current = setTimeout(() => {
      setIsSelectingTrack(false)
    }, 1000) // 1 second cooldown between track selections
  }, [socket, isSelectingTrack])

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (trackSelectionTimeoutRef.current) {
        clearTimeout(trackSelectionTimeoutRef.current)
      }
    }
  }, [])

  const handleVolumeChange = (newVolume: number) => {
    setVolume(newVolume)
    
    if (socket) {
      socket.emit('music_command', {
        action: 'volume',
        volume: newVolume
      })
    }
  }

  const handleStop = () => {
    if (!socket) return

    socket.emit('music_command', {
      action: 'stop'
    })
  }

  const handleNext = () => {
    if (!socket) return

    socket.emit('music_command', {
      action: 'next'
    })
  }

  // Spotify-specific handlers
  const handleSpotifySearchResults = useCallback((results: SpotifySearchResult) => {
    setSpotifySearchResults(results)
  }, [])

  const handleSpotifyTrackSelect = useCallback((track: SpotifyTrack) => {
    setSelectedSpotifyTrack(track)
    console.log('Selected Spotify track:', track.name, 'by', track.artists.map(a => a.name).join(', '))
  }, [])

  const handleSpotifyAddToQueue = useCallback((track: SpotifyTrack) => {
    console.log('Added to Spotify queue:', track.name, 'by', track.artists.map(a => a.name).join(', '))
    // The actual queue addition is handled by the SpotifyTrackResults component
  }, [])

  return (
    <div className="space-y-6">
      {/* Current Track Display */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          NOW PLAYING
        </h3>
        
        {currentTrack ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="text-xl font-bold text-sw-blue-100">{currentTrack.title}</h4>
                <p className="text-sw-blue-300">{currentTrack.artist}</p>
              </div>
              <div className="text-right">
                <p className="text-sm text-sw-blue-300">{currentTrack.duration}</p>
                <p className="text-xs text-sw-blue-400">{currentTrack.file}</p>
              </div>
            </div>

            {/* Progress Bar */}
            <div className="space-y-2">
              <div className="bg-sw-dark-700 rounded-full h-2 overflow-hidden">
                <div 
                  className="bg-sw-blue-500 h-2 rounded-full transition-all duration-100 ease-linear"
                  style={{ width: isClient ? `${progress}%` : '0%' }}
                ></div>
              </div>
              <div className="flex justify-between text-xs text-sw-blue-400">
                <span>{currentTime}</span>
                <span>{currentTrack.duration}</span>
              </div>
            </div>

            {/* Playback Controls */}
            <div className="flex items-center justify-center space-x-4">
              <button 
                onClick={handleStop}
                className="sw-button"
                disabled={!currentTrack}
              >
                ⏹
              </button>
              <button 
                onClick={handlePlayPause}
                className="sw-button w-12 h-12 rounded-full text-lg"
                disabled={!currentTrack}
              >
                {isPlaying ? '⏸' : isPaused ? '▶' : '▶'}
              </button>
              <button 
                onClick={handleNext}
                className="sw-button"
                disabled={!currentTrack}
              >
                ⏭
              </button>
            </div>
          </div>
        ) : (
          <div className="text-center py-8 text-sw-blue-300/50">
            No track selected. Choose a track from the library below.
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Music Library */}
        <div className="lg:col-span-2 sw-panel">
          {/* Provider Selection */}
          <div className="mb-6">
            <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow mb-4">
              MUSIC PROVIDER
            </h3>
            <div className="flex space-x-6">
              <label className="flex items-center space-x-2 cursor-pointer">
                <input
                  type="radio"
                  value="local"
                  checked={currentProvider === 'local'}
                  onChange={(e) => setCurrentProvider(e.target.value as MusicProvider)}
                  className="w-4 h-4 text-sw-blue-500 bg-sw-dark-700 border-sw-blue-600 focus:ring-sw-blue-500 focus:ring-2"
                />
                <span className="text-sw-blue-200 font-medium">Local Library</span>
                <div className={`w-2 h-2 rounded-full ml-2 ${
                  musicServiceStatus === 'online' ? 'bg-green-400' : 'bg-red-400'
                }`} />
              </label>
              <label className="flex items-center space-x-2 cursor-pointer">
                <input
                  type="radio"
                  value="spotify"
                  checked={currentProvider === 'spotify'}
                  onChange={(e) => setCurrentProvider(e.target.value as MusicProvider)}
                  className="w-4 h-4 text-sw-blue-500 bg-sw-dark-700 border-sw-blue-600 focus:ring-sw-blue-500 focus:ring-2"
                />
                <span className="text-sw-blue-200 font-medium">Spotify</span>
                <div className={`w-2 h-2 rounded-full ml-2 ${
                  authState.isAuthenticated && authState.isPremium ? 'bg-green-400' : 
                  authState.isAuthenticated && !authState.isPremium ? 'bg-yellow-400' :
                  'bg-red-400'
                }`} />
              </label>
            </div>
          </div>

          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
              {currentProvider === 'local' ? 'MUSIC LIBRARY' : 'SPOTIFY PLAYER'}
            </h3>
            {currentProvider === 'local' && (
              <input
                type="text"
                placeholder="Search tracks..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="px-3 py-1 bg-sw-dark-700 border border-sw-blue-600/30 rounded text-sw-blue-100 text-sm focus:outline-none focus:border-sw-blue-500"
              />
            )}
          </div>

          {/* Content based on selected provider */}
          {currentProvider === 'local' ? (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {isLoading ? (
                <div className="text-center py-8 text-sw-blue-300/50">
                  Loading music library...
                </div>
              ) : filteredTracks.length === 0 ? (
                <div className="text-center py-8 text-sw-blue-300/50">
                  {searchTerm ? 'No tracks match your search.' : 'No music tracks available.'}
                </div>
              ) : (
                filteredTracks.map((track) => (
                  <div
                    key={track.id}
                    className={`
                      p-3 rounded-lg border border-sw-blue-600/20
                      transition-all duration-200 hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50
                      ${currentTrack?.title === track.title ? 'bg-sw-blue-600/20 border-sw-blue-500' : 'bg-sw-dark-700/30'}
                    `}
                  >
                    <div className="flex items-center justify-between">
                      <div 
                        className={`flex-1 cursor-pointer ${isSelectingTrack ? 'opacity-50 cursor-wait' : ''}`}
                        onClick={() => handleTrackSelect(track)}
                        title={isSelectingTrack ? 'Please wait...' : `Play ${track.title}`}
                      >
                        <h4 className="font-medium text-sw-blue-100">{track.title}</h4>
                        <p className="text-sm text-sw-blue-300">{track.artist}</p>
                      </div>
                      <div className="flex items-center space-x-3">
                        <div className="text-right">
                          <p className="text-sm text-sw-blue-300">{track.duration}</p>
                          {currentTrack?.title === track.title && isPlaying && (
                            <p className="text-xs text-sw-green">PLAYING</p>
                          )}
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            // Send queue command to backend
                            if (socket) {
                              socket.emit('music_command', {
                                action: 'queue',
                                track_name: track.title,
                                track_id: track.id
                              })
                            }
                            // Also update local queue for UI feedback
                            setQueue(prev => [...prev, track])
                          }}
                          className="text-sw-blue-400 hover:text-sw-blue-300 text-sm"
                          title="Add to queue"
                        >
                          +
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : (
            <div className="space-y-6">
              {/* Spotify Web Player */}
              <div className="bg-sw-dark-700/30 border border-sw-blue-600/20 rounded-lg p-4">
                <SpotifyWebPlayer 
                  className="bg-transparent border-0 p-0"
                  showAuth={true}
                  showDeviceActivation={true}
                />
              </div>

              {/* Enhanced Spotify Search */}
              {authState.isAuthenticated && (
                <div className="space-y-4">
                  <h4 className="text-md font-semibold text-sw-blue-100 sw-text-glow">
                    SEARCH SPOTIFY
                  </h4>
                  <SpotifySearch
                    onTrackSelect={handleSpotifyTrackSelect}
                    onSearchResults={handleSpotifySearchResults}
                  />
                  
                  {/* Track Results */}
                  {spotifySearchResults.tracks.length > 0 && (
                    <div className="space-y-2">
                      <h5 className="text-sm font-medium text-sw-blue-200">
                        Search Results ({spotifySearchResults.tracks.length} tracks)
                      </h5>
                      <div className="max-h-64 overflow-y-auto">
                        <SpotifyTrackResults
                          tracks={spotifySearchResults.tracks}
                          onTrackSelect={handleSpotifyTrackSelect}
                          onAddToQueue={handleSpotifyAddToQueue}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Queue Management */}
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            QUEUE
          </h3>
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {queue.length === 0 ? (
              <div className="text-center py-6 text-sw-blue-300/50">
                No tracks in queue
              </div>
            ) : (
              queue.map((track, index) => (
                <div
                  key={`${track.id}-${index}`}
                  className="p-2 bg-sw-dark-700/30 rounded border border-sw-blue-600/20"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <h4 className="font-medium text-sw-blue-100 text-sm">{track.title}</h4>
                      <p className="text-xs text-sw-blue-300">{track.artist}</p>
                    </div>
                    <div className="flex items-center space-x-2">
                      <span className="text-xs text-sw-blue-400">#{index + 1}</span>
                      <button 
                        className="text-sw-red hover:text-sw-red/80 text-xs"
                        onClick={() => {
                          const newQueue = [...queue]
                          newQueue.splice(index, 1)
                          setQueue(newQueue)
                        }}
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Audio Status */}
        <div className="space-y-6">
          <div className="sw-panel">
            <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
              AUDIO STATUS
            </h3>
            <div className="space-y-3">
              <StatusIndicator label="Music Service" status={musicServiceStatus as "online" | "offline" | "warning"} />
              <StatusIndicator label="VLC Backend" status={vlcBackendStatus as "online" | "offline" | "warning"} />
              <StatusIndicator label="Audio Ducking" status={isPlaying ? 'online' : 'offline'} />
              <StatusIndicator label="Crossfade" status="offline" />
              <StatusIndicator 
                label="Spotify Auth" 
                status={
                  authState.isAuthenticated && authState.isPremium ? 'online' :
                  authState.isAuthenticated && !authState.isPremium ? 'warning' :
                  'offline'
                } 
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

interface StatusIndicatorProps {
  label: string
  status: 'online' | 'offline' | 'warning'
}

function StatusIndicator({ label, status }: StatusIndicatorProps) {
  const getStatusClass = () => {
    switch (status) {
      case 'online':
        return 'sw-status-online'
      case 'warning':
        return 'sw-status-warning'
      case 'offline':
      default:
        return 'sw-status-offline'
    }
  }

  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-sw-blue-200">{label}</span>
      <div className={`w-3 h-3 rounded-full ${getStatusClass()}`}></div>
    </div>
  )
}