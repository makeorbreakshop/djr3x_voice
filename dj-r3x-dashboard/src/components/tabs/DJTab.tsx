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
  const [autoTransition, setAutoTransition] = useState(true)
  const [transitionInterval, setTransitionInterval] = useState(300) // seconds
  const [crossfadeDuration, setCrossfadeDuration] = useState(5) // seconds
  const [upcomingQueue, setUpcomingQueue] = useState<Track[]>([])
  const [commentary, setCommentary] = useState<CommentaryData>({
    generated_count: 0,
    tracks_played: 0,
    session_duration: '00:00:00',
    last_commentary: undefined
  })
  const [crossfadeStatus, setCrossfadeStatus] = useState('offline')
  const [commentaryStatus, setCommentaryStatus] = useState('offline')

  // Socket event listeners
  useEffect(() => {
    if (!socket) return

    const handleDJStatus = (data: DJStatus) => {
      console.log('DJ status update:', data)
      
      if (data.is_active !== undefined) {
        setDjModeActive(data.is_active)
      }
      
      if (data.auto_transition !== undefined) {
        setAutoTransition(data.auto_transition)
      }
    }

    const handleCrossfadeUpdate = (data: any) => {
      console.log('Crossfade update:', data)
      setCrossfadeStatus('active')
      
      // Reset crossfade status after completion
      setTimeout(() => {
        setCrossfadeStatus('ready')
      }, (crossfadeDuration + 1) * 1000)
    }

    const handleCommentaryUpdate = (data: any) => {
      console.log('Commentary update:', data)
      setCommentary(prev => ({
        ...prev,
        generated_count: prev.generated_count + 1,
        last_commentary: data.text || data.commentary
      }))
      setCommentaryStatus('active')
    }

    const handleServiceStatus = (data: any) => {
      if (data.service === 'BrainService') {
        setCommentaryStatus(data.status === 'RUNNING' ? 'ready' : 'offline')
      }
    }

    socket.on('dj_status', handleDJStatus)
    socket.on('crossfade_started', handleCrossfadeUpdate)
    socket.on('llm_response', handleCommentaryUpdate)
    socket.on('service_status_update', handleServiceStatus)

    return () => {
      socket.off('dj_status', handleDJStatus)
      socket.off('crossfade_started', handleCrossfadeUpdate)
      socket.off('llm_response', handleCommentaryUpdate)
      socket.off('service_status_update', handleServiceStatus)
    }
  }, [socket, crossfadeDuration])

  const handleDJModeToggle = () => {
    if (!socket) return

    const newDJModeActive = !djModeActive
    
    socket.emit('dj_command', {
      action: newDJModeActive ? 'start' : 'stop',
      auto_transition: autoTransition,
      interval: transitionInterval
    })
  }

  const handleNextTrack = () => {
    if (!socket || !djModeActive) return

    socket.emit('dj_command', {
      action: 'next'
    })
  }

  const handleSettingsUpdate = () => {
    if (!socket || !djModeActive) return

    // Send updated settings to CantinaOS
    socket.emit('dj_command', {
      action: 'update_settings',
      auto_transition: autoTransition,
      interval: transitionInterval,
      crossfade_duration: crossfadeDuration
    })
  }

  // Update settings when they change (if DJ mode is active)
  useEffect(() => {
    if (djModeActive) {
      handleSettingsUpdate()
    }
  }, [autoTransition, transitionInterval, crossfadeDuration, djModeActive])

  return (
    <div className="space-y-6">
      {/* DJ Mode Control */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-6 sw-text-glow">
          DJ MODE CONTROL
        </h3>
        
        <div className="flex items-center justify-center mb-6">
          <button
            onClick={handleDJModeToggle}
            className={`
              px-8 py-4 rounded-lg text-white font-bold text-lg uppercase
              transition-all duration-200 transform hover:scale-105
              ${djModeActive 
                ? 'bg-sw-red hover:bg-red-600 sw-border-glow' 
                : 'bg-sw-green hover:bg-green-600 sw-border-glow'
              }
            `}
          >
            {djModeActive ? 'Stop DJ Mode' : 'Start DJ Mode'}
          </button>
        </div>

        <div className="text-center">
          <p className="text-sm text-sw-blue-300/70 mb-2">
            {djModeActive 
              ? 'DJ Mode is active - Automatic track transitions enabled' 
              : 'Click to activate automatic DJ mode'
            }
          </p>
          {djModeActive && (
            <div className="flex items-center justify-center space-x-2">
              <div className="w-2 h-2 bg-sw-green rounded-full animate-pulse"></div>
              <span className="text-xs text-sw-green uppercase font-semibold">LIVE</span>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Auto-Transition Settings */}
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            AUTO-TRANSITION SETTINGS
          </h3>
          
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="text-sm text-sw-blue-300">Auto-Transition</label>
              <button
                onClick={() => setAutoTransition(!autoTransition)}
                className={`
                  w-12 h-6 rounded-full transition-colors duration-200
                  ${autoTransition ? 'bg-sw-blue-600' : 'bg-sw-dark-600'}
                `}
              >
                <div className={`
                  w-4 h-4 bg-white rounded-full transition-transform duration-200 m-1
                  ${autoTransition ? 'translate-x-6' : 'translate-x-0'}
                `}></div>
              </button>
            </div>

            <div>
              <label className="block text-sm text-sw-blue-300 mb-2">
                Transition Interval: {Math.floor(transitionInterval / 60)}:{(transitionInterval % 60).toString().padStart(2, '0')}
              </label>
              <input
                type="range"
                min="60"
                max="600"
                value={transitionInterval}
                onChange={(e) => setTransitionInterval(parseInt(e.target.value))}
                className="w-full accent-sw-blue-500"
                disabled={!autoTransition}
              />
            </div>

            <div>
              <label className="block text-sm text-sw-blue-300 mb-2">
                Crossfade Duration: {crossfadeDuration}s
              </label>
              <input
                type="range"
                min="1"
                max="15"
                value={crossfadeDuration}
                onChange={(e) => setCrossfadeDuration(parseInt(e.target.value))}
                className="w-full accent-sw-blue-500"
                disabled={!autoTransition}
              />
            </div>
          </div>
        </div>

        {/* Track Queue */}
        <div className="sw-panel">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
              UPCOMING QUEUE
            </h3>
            <button 
              onClick={handleNextTrack}
              className="sw-button text-sm"
              disabled={!djModeActive}
            >
              Next Track
            </button>
          </div>
          
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {upcomingQueue.length === 0 ? (
              <div className="text-center py-6 text-sw-blue-300/50">
                {djModeActive ? 'Queue will populate automatically...' : 'Start DJ mode to see upcoming tracks'}
              </div>
            ) : (
              upcomingQueue.map((track, index) => (
                <div 
                  key={`${track.track_id || track.title}-${index}`}
                  className="p-3 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20"
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
      </div>

      {/* Commentary Generation */}
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

      {/* DJ Mode Status */}
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