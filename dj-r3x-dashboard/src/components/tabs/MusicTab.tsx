'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'

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

export default function MusicTab() {
  const { socket } = useSocketContext()
  const [isPlaying, setIsPlaying] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [currentTrack, setCurrentTrack] = useState<Track | null>(null)
  const [volume, setVolume] = useState(75)
  const [progress, setProgress] = useState(0)
  const [currentTime, setCurrentTime] = useState('0:00')
  const [searchTerm, setSearchTerm] = useState('')
  const [tracks, setTracks] = useState<Track[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [musicServiceStatus, setMusicServiceStatus] = useState('offline')
  const [vlcBackendStatus, setVlcBackendStatus] = useState('offline')
  const [queue, setQueue] = useState<Track[]>([])
  const [ducking, setDucking] = useState(false)

  // Load music library on component mount
  useEffect(() => {
    loadMusicLibrary()
  }, [])

  // Socket event listeners
  useEffect(() => {
    if (!socket) return

    const handleMusicStatus = (data: any) => {
      console.log('🎵 [MusicTab] Music status update received:', data)
      
      // WebBridge wraps payload in {topic, data, timestamp} structure
      const musicData = data.data || data
      console.log('🎵 [MusicTab] Track data received:', musicData.track)
      console.log('🎵 [MusicTab] Current isPlaying state:', isPlaying)
      console.log('🎵 [MusicTab] Current currentTrack state:', currentTrack)
      
      if (musicData.action === 'started') {
        setIsPlaying(true)
        setIsPaused(false)
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
          console.log('🎵 [MusicTab] Created track object:', track)
          setCurrentTrack(track)
          console.log('🎵 [MusicTab] Updated currentTrack state')
        }
      } else if (musicData.action === 'stopped') {
        console.log('🎵 [MusicTab] Music stopped event received')
        setIsPlaying(false)
        setIsPaused(false)
        setProgress(0)
        setCurrentTime('0:00')
      } else if (musicData.action === 'paused') {
        console.log('🎵 [MusicTab] Music paused event received')
        setIsPlaying(false)
        setIsPaused(true)
      } else if (musicData.action === 'resumed') {
        console.log('🎵 [MusicTab] Music resumed event received')
        setIsPlaying(true)
        setIsPaused(false)
      } else {
        console.log('🎵 [MusicTab] Unknown action received:', musicData.action)
      }
      
      if (musicData.volume !== undefined) {
        setVolume(musicData.volume)
      }
    }

    const handleMusicLibraryUpdated = (data: any) => {
      console.log('Music library updated:', data)
      loadMusicLibrary()
    }

    const handleMusicProgress = (data: any) => {
      // WebBridge wraps payload in {topic, data, timestamp} structure
      const progressData = data.data || data
      
      if (progressData.action === 'progress') {
        // Update progress bar with real-time position
        setProgress(progressData.progress_percent || 0)
        
        // Update current time display
        const positionSec = progressData.position_sec || 0
        const minutes = Math.floor(positionSec / 60)
        const seconds = Math.floor(positionSec % 60)
        setCurrentTime(`${minutes}:${String(seconds).padStart(2, '0')}`)
        
        console.log(`🎵 [MusicTab] Progress update: ${progressData.position_sec?.toFixed(1)}s / ${progressData.duration_sec?.toFixed(1)}s (${progressData.progress_percent?.toFixed(1)}%)`)
      }
    }

    const handleServiceStatus = (data: any) => {
      if (data.service === 'MusicController') {
        setMusicServiceStatus(data.status === 'RUNNING' ? 'online' : 'offline')
      }
    }

    const handleMusicQueue = (data: any) => {
      console.log('🎵 [MusicTab] Queue update received:', data)
      // Handle queue updates from backend
      const queueData = data.data || data
      if (queueData.action === 'queue_updated') {
        console.log('🎵 [MusicTab] Queue updated - length:', queueData.queue_length)
        // Note: We're managing queue locally for UI responsiveness
        // Backend queue is separate for actual playback logic
      }
    }

    socket.on('music_status', handleMusicStatus)
    socket.on('music_progress', handleMusicProgress)
    socket.on('music_library_updated', handleMusicLibraryUpdated)
    socket.on('music_queue', handleMusicQueue)
    socket.on('service_status_update', handleServiceStatus)

    return () => {
      socket.off('music_status', handleMusicStatus)
      socket.off('music_progress', handleMusicProgress)
      socket.off('music_library_updated', handleMusicLibraryUpdated)
      socket.off('music_queue', handleMusicQueue)
      socket.off('service_status_update', handleServiceStatus)
    }
  }, [socket])

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

  const handleTrackSelect = (track: Track) => {
    if (!socket) return

    socket.emit('music_command', {
      action: 'play',
      track_name: track.title,
      track_id: track.id
    })
  }

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
                  className="bg-sw-blue-500 h-2 rounded-full transition-all duration-1000"
                  style={{ width: `${progress}%` }}
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
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
              MUSIC LIBRARY
            </h3>
            <input
              type="text"
              placeholder="Search tracks..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="px-3 py-1 bg-sw-dark-700 border border-sw-blue-600/30 rounded text-sw-blue-100 text-sm focus:outline-none focus:border-sw-blue-500"
            />
          </div>

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
                      className="flex-1 cursor-pointer"
                      onClick={() => handleTrackSelect(track)}
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

        {/* Volume and Controls */}
        <div className="space-y-6">
          <div className="sw-panel">
            <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
              VOLUME
            </h3>
            <div className="space-y-4">
              <div className="flex items-center space-x-3">
                <span className="text-sm text-sw-blue-300">🔊</span>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={volume}
                  onChange={(e) => handleVolumeChange(parseInt(e.target.value))}
                  className="flex-1 accent-sw-blue-500"
                />
                <span className="text-sm text-sw-blue-200 font-mono w-8">{volume}</span>
              </div>
              
              <div className="bg-sw-dark-700 rounded-full h-2 overflow-hidden relative">
                <div 
                  className={`h-2 rounded-full transition-all duration-200 ${
                    ducking ? 'bg-sw-yellow' : 'bg-sw-blue-500'
                  }`}
                  style={{ width: `${volume}%` }}
                ></div>
                {ducking && (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-xs text-sw-dark font-bold">DUCKED</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="sw-panel">
            <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
              AUDIO STATUS
            </h3>
            <div className="space-y-3">
              <StatusIndicator label="Music Service" status={musicServiceStatus as "online" | "offline" | "warning"} />
              <StatusIndicator label="VLC Backend" status={vlcBackendStatus as "online" | "offline" | "warning"} />
              <StatusIndicator label="Audio Ducking" status={isPlaying ? 'online' : 'offline'} />
              <StatusIndicator label="Crossfade" status="offline" />
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