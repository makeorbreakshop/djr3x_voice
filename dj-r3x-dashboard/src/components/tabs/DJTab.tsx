'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'

interface Track {
  title: string
  artist: string
  duration: string
  track_id?: string
}

interface DJStatus {
  mode: string
  auto_transition?: boolean
  is_active?: boolean
}

interface CommentaryData {
  generated_count: number
  tracks_played: number
  session_duration: string
  last_commentary?: string
}

export default function DJTab() {
  const { socket } = useSocketContext()
  const [djModeActive, setDjModeActive] = useState(false)
  const [upcomingQueue, setUpcomingQueue] = useState<Track[]>([])
  const [commentary, setCommentary] = useState<CommentaryData>({
    generated_count: 0,
    tracks_played: 0,
    session_duration: '00:00:00',
    last_commentary: undefined
  })
  const [crossfadeStatus, setCrossfadeStatus] = useState('offline')
  const [commentaryStatus, setCommentaryStatus] = useState('offline')
  const [lastCommand, setLastCommand] = useState<string>('')

  // Socket event listeners
  useEffect(() => {
    if (!socket) return

    // Many events from the WebBridge wrap the real payload under a `data` key.
    // Helper to unwrap this pattern for easier access.
    const unwrap = (raw: any) => (raw && raw.data ? raw.data : raw)

    const handleDJStatus = (raw: any) => {
      const data = unwrap(raw) as DJStatus
      console.log('DJ status update:', data)
      
      if (data.is_active !== undefined) {
        setDjModeActive(data.is_active)
      }
    }

    const handleCrossfadeUpdate = (raw: any) => {
      const data = unwrap(raw)
      console.log('Crossfade update:', data)
      setCrossfadeStatus('active')
      
      // Reset crossfade status after completion
      setTimeout(() => {
        setCrossfadeStatus('ready')
      }, 6000) // 5s default + 1s buffer
    }

    const handleCommentaryUpdate = (raw: any) => {
      const data = unwrap(raw)
      console.log('Commentary update:', data)
      setCommentary(prev => ({
        ...prev,
        generated_count: prev.generated_count + 1,
        last_commentary: data.text || data.commentary
      }))
      setCommentaryStatus('active')
    }

    const handleServiceStatus = (raw: any) => {
      const data = unwrap(raw)
      if (data.service === 'BrainService') {
        setCommentaryStatus(data.status === 'RUNNING' ? 'ready' : 'offline')
      }
    }

    const handleQueueUpdate = (raw: any) => {
      const data = unwrap(raw)
      console.log('Queue update:', data)
      if (data.upcoming_queue) {
        setUpcomingQueue(data.upcoming_queue)
      } else if (data.next_track) {
        // Handle the case where only the next track is sent
        setUpcomingQueue([data.next_track])
      }
    }

    socket.on('dj_status', handleDJStatus)
    socket.on('crossfade_started', handleCrossfadeUpdate)
    socket.on('llm_response', handleCommentaryUpdate)
    socket.on('service_status_update', handleServiceStatus)
    socket.on('dj_queue_update', handleQueueUpdate)

    return () => {
      socket.off('dj_status', handleDJStatus)
      socket.off('crossfade_started', handleCrossfadeUpdate)
      socket.off('llm_response', handleCommentaryUpdate)
      socket.off('service_status_update', handleServiceStatus)
      socket.off('dj_queue_update', handleQueueUpdate)
    }
  }, [socket])

  const handleDJModeToggle = () => {
    if (!socket) return

    const newDJModeActive = !djModeActive
    const command = newDJModeActive ? 'dj start' : 'dj stop'
    
    // Update state immediately for responsive UI
    setDjModeActive(newDJModeActive)
    setLastCommand(command)
    
    // Send command exactly like CLI - using working simple command system
    socket.emit('command', {
      command: command
    })
  }

  const handleNextTrack = () => {
    if (!socket || !djModeActive) return

    setLastCommand('dj next')
    
    // Send command exactly like CLI - using working simple command system
    socket.emit('command', {
      command: 'dj next'
    })
  }

  return (
    <div className="space-y-6">
      {/* DJ Mode Control - Redesigned with state-based layout */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-6 sw-text-glow">
          DJ MODE CONTROL
        </h3>
        
        {!djModeActive ? (
          // Inactive State: Single Start Button
          <div className="flex flex-col items-center space-y-4">
            <button
              onClick={handleDJModeToggle}
              className="
                px-12 py-6 rounded-lg text-white font-bold text-xl uppercase
                transition-all duration-200 transform hover:scale-105
                bg-sw-green hover:bg-green-600 sw-border-glow
                shadow-lg shadow-green-500/20
              "
            >
              Start DJ Mode
            </button>
            <p className="text-sm text-sw-blue-300/70 text-center max-w-md">
              Click to activate automatic DJ mode with intelligent track selection and seamless transitions
            </p>
          </div>
        ) : (
          // Active State: Stop and Next Track Controls
          <div className="flex flex-col items-center space-y-6">
            <div className="flex items-center justify-center space-x-2 mb-4">
              <div className="w-3 h-3 bg-sw-green rounded-full animate-pulse"></div>
              <span className="text-lg text-sw-green uppercase font-bold tracking-wider">
                DJ MODE ACTIVE
              </span>
              <div className="w-3 h-3 bg-sw-green rounded-full animate-pulse"></div>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 w-full max-w-md">
              <button
                onClick={handleDJModeToggle}
                className="
                  px-6 py-4 rounded-lg text-white font-bold text-lg uppercase
                  transition-all duration-200 transform hover:scale-105
                  bg-sw-red hover:bg-red-600 sw-border-glow
                  shadow-lg shadow-red-500/20
                "
              >
                Stop DJ
              </button>
              <button
                onClick={handleNextTrack}
                className="
                  px-6 py-4 rounded-lg text-white font-bold text-lg uppercase
                  transition-all duration-200 transform hover:scale-105
                  bg-sw-blue-600 hover:bg-sw-blue-500 sw-border-glow
                  shadow-lg shadow-blue-500/20
                "
              >
                Next Track
              </button>
            </div>
            
            <p className="text-sm text-sw-blue-300/70 text-center">
              DJ mode is running with automatic track transitions and commentary generation
            </p>
            {lastCommand && (
              <p className="text-xs text-sw-blue-400/60 text-center">
                Last command: {lastCommand}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Track Queue */}
      <div className="sw-panel">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
            UPCOMING QUEUE
          </h3>
          {djModeActive && (
            <div className="flex items-center space-x-2 text-xs text-sw-blue-400">
              <div className="w-2 h-2 bg-sw-blue-400 rounded-full animate-pulse"></div>
              <span>Auto-generating</span>
            </div>
          )}
        </div>
        
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {upcomingQueue.length === 0 ? (
            <div className="text-center py-8 text-sw-blue-300/50">
              {djModeActive ? (
                <div className="space-y-2">
                  <div className="animate-spin w-6 h-6 border-2 border-sw-blue-500 border-t-transparent rounded-full mx-auto"></div>
                  <p>Generating track queue...</p>
                </div>
              ) : (
                'Start DJ mode to see upcoming tracks'
              )}
            </div>
          ) : (
            upcomingQueue.map((track, index) => (
              <div 
                key={`${track.track_id || track.title}-${index}`}
                className="p-3 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 hover:border-sw-blue-500/40 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <h4 className="text-sm font-medium text-sw-blue-100">{track.title}</h4>
                    <p className="text-xs text-sw-blue-300">{track.artist}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-sw-blue-300">{track.duration}</p>
                    <p className="text-xs text-sw-blue-400">#{index + 1}</p>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Commentary Status */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          COMMENTARY STATUS
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="text-center">
            <div className="text-2xl font-bold text-sw-blue-100 mb-1">
              {commentary.generated_count}
            </div>
            <div className="text-xs text-sw-blue-300 uppercase">Commentary Generated</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-sw-blue-100 mb-1">
              {commentary.tracks_played}
            </div>
            <div className="text-xs text-sw-blue-300 uppercase">Tracks Played</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-sw-blue-100 mb-1">
              {commentary.session_duration}
            </div>
            <div className="text-xs text-sw-blue-300 uppercase">Session Duration</div>
          </div>
        </div>

        <div className="mt-6">
          <h4 className="text-sm font-semibold text-sw-blue-200 mb-2">Last Commentary:</h4>
          <div className="bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20 p-4">
            <p className="text-sm text-sw-blue-300/70 italic">
              {commentary.last_commentary || 
                (djModeActive 
                  ? 'Waiting for next commentary...' 
                  : 'No commentary generated yet. Start DJ mode to begin automatic commentary.'
                )
              }
            </p>
          </div>
        </div>
      </div>

      {/* System Status */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          SYSTEM STATUS
        </h3>
        
        <div className="grid grid-cols-2 gap-4">
          <StatusCard 
            label="DJ Mode Service" 
            status={djModeActive ? "active" : "inactive"} 
          />
          <StatusCard 
            label="Track Queue" 
            status={upcomingQueue.length > 0 ? "ready" : "offline"} 
          />
          <StatusCard 
            label="Commentary Engine" 
            status={commentaryStatus as 'active' | 'inactive' | 'ready' | 'offline' | 'error'} 
          />
          <StatusCard 
            label="Crossfade Engine" 
            status={crossfadeStatus as 'active' | 'inactive' | 'ready' | 'offline' | 'error'} 
          />
        </div>
      </div>
    </div>
  )
}

interface StatusCardProps {
  label: string
  status: 'active' | 'inactive' | 'ready' | 'offline' | 'error'
}

function StatusCard({ label, status }: StatusCardProps) {
  const getStatusClass = () => {
    switch (status) {
      case 'active':
        return 'sw-status-online'
      case 'ready':
        return 'sw-status-warning'
      case 'error':
        return 'sw-status-offline'
      case 'inactive':
      case 'offline':
      default:
        return 'bg-sw-dark-600'
    }
  }

  const getStatusText = () => {
    switch (status) {
      case 'active':
        return 'ACTIVE'
      case 'ready':
        return 'READY'
      case 'error':
        return 'ERROR'
      case 'inactive':
        return 'INACTIVE'
      case 'offline':
      default:
        return 'OFFLINE'
    }
  }

  return (
    <div className="p-4 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium text-sw-blue-100">{label}</h4>
        <div className={`w-3 h-3 rounded-full ${getStatusClass()}`}></div>
      </div>
      <p className="text-xs text-sw-blue-300 uppercase font-semibold">
        {getStatusText()}
      </p>
    </div>
  )
}