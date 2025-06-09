'use client'

import { useState, useEffect, useCallback } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'

interface ActivityEvent {
  id: string
  type: string
  description: string
  timestamp: Date
  isNew?: boolean
}

export default function LiveActivityFeed() {
  const { socket } = useSocketContext()
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [isNewEventAnimating, setIsNewEventAnimating] = useState(false)

  const addEvent = useCallback((type: string, description: string) => {
    const newEvent: ActivityEvent = {
      id: `${Date.now()}-${Math.random()}`,
      type,
      description,
      timestamp: new Date(),
      isNew: true
    }
    
    setEvents(prev => {
      const updated = [newEvent, ...prev.slice(0, 19)] // Keep last 20 events
      return updated
    })
    
    // Trigger new event animation
    setIsNewEventAnimating(true)
    setTimeout(() => setIsNewEventAnimating(false), 1000)
    
    // Remove "new" flag after animation
    setTimeout(() => {
      setEvents(prev => prev.map(event => 
        event.id === newEvent.id ? { ...event, isNew: false } : event
      ))
    }, 2000)
  }, [])

  // CantinaOS event integration
  useEffect(() => {
    if (!socket) return

    const handleServiceStatus = (data: any) => {
      const serviceName = data.service_name || data.service || 'Unknown Service'
      const status = data.status || 'unknown'
      addEvent('system_update', `${serviceName} status: ${status}`)
    }

    const handleSystemModeChange = (data: any) => {
      const mode = data.mode || data.current_mode || 'unknown'
      addEvent('mode_change', `System mode changed to: ${mode.toUpperCase()}`)
    }

    const handleVoiceEvent = (data: any) => {
      addEvent('voice_activity', 'Guest interaction detected')
    }

    const handleMusicEvent = (data: any) => {
      const action = data.action || 'unknown'
      const trackName = data.track?.title || data.track_name || 'Unknown Track'
      addEvent('music_activity', `Music ${action}: ${trackName}`)
    }

    const handleDJModeChange = (data: any) => {
      const isActive = data.is_active || data.dj_mode_active
      addEvent('dj_mode', `DJ Mode ${isActive ? 'activated' : 'deactivated'}`)
    }

    // Subscribe to various CantinaOS events
    socket.on('service_status_update', handleServiceStatus)
    socket.on('system_mode_change', handleSystemModeChange)
    socket.on('voice_listening_started', handleVoiceEvent)
    socket.on('transcription_final', handleVoiceEvent)
    socket.on('speech_synthesis_started', handleVoiceEvent)
    socket.on('music_status', handleMusicEvent)
    socket.on('dj_mode_changed', handleDJModeChange)

    return () => {
      socket.off('service_status_update', handleServiceStatus)
      socket.off('system_mode_change', handleSystemModeChange)
      socket.off('voice_listening_started', handleVoiceEvent)
      socket.off('transcription_final', handleVoiceEvent)
      socket.off('speech_synthesis_started', handleVoiceEvent)
      socket.off('music_status', handleMusicEvent)
      socket.off('dj_mode_changed', handleDJModeChange)
    }
  }, [socket, addEvent])

  // Add some demo events for testing
  useEffect(() => {
    if (events.length === 0) {
      addEvent('system_startup', 'Cantina Entertainment System online')
      setTimeout(() => addEvent('service_check', 'All systems operational'), 1000)
    }
  }, [addEvent, events.length])

  const getEventIcon = (type: string) => {
    switch (type.toLowerCase()) {
      case 'system_update':
      case 'system_startup':
        return '⚙️'
      case 'mode_change':
        return '🔄'
      case 'voice_activity':
        return '🎤'
      case 'music_activity':
        return '🎵'
      case 'dj_mode':
        return '🎧'
      case 'service_check':
        return '✅'
      default: return 'ℹ️'
    }
  }

  return (
    <div className="h-full w-full flex flex-col bg-transparent">
      <div className="flex-1 p-4 overflow-y-auto">
        
        {events.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <div className="text-cyan-400 font-mono text-lg mb-4">MONITORING SYSTEMS</div>
              <div className="text-yellow-400 font-mono text-sm">
                Real-time events will appear here
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {events.map((event) => (
              <div key={event.id} className="border-l-4 border-cyan-400 bg-slate-700 p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-yellow-400 font-mono text-xs font-bold">
                    {getEventIcon(event.type)} {event.type.toUpperCase().replace('_', ' ')}
                  </span>
                  <span className="text-cyan-400 font-mono text-xs">
                    {event.timestamp.toLocaleTimeString()}
                  </span>
                </div>
                <p className="text-white font-mono text-sm">
                  {event.description}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}