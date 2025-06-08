'use client'

import { useEffect, useState, useRef } from 'react'
import { io, Socket } from 'socket.io-client'

export interface SystemStatus {
  cantina_os_connected: boolean
  services: Record<string, any>
  timestamp: string
}

export interface VoiceStatus {
  status: 'idle' | 'recording' | 'processing' | 'error'
  timestamp: string
}

export interface TranscriptionUpdate {
  text: string
  confidence: number
  final: boolean
  timestamp: string
}

export interface MusicStatus {
  action: string
  track_id?: string
  volume?: number
  timestamp: string
}

export interface DJStatus {
  mode: 'active' | 'inactive'
  auto_transition: boolean
  timestamp: string
}

export interface SystemModeStatus {
  current_mode: 'IDLE' | 'AMBIENT' | 'INTERACTIVE'
  previous_mode?: string
  timestamp: string
}

export interface ModeTransitionStatus {
  old_mode: string
  new_mode: string
  status: 'started' | 'completed' | 'failed'
  error?: string
  timestamp: string
}

export interface PerformanceMetrics {
  events_per_minute: number
  cpu_usage: number
  memory_usage: number
  uptime: string
}

export interface LogEntry {
  id: string
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  service: string
  message: string
}

interface SocketEvents {
  system_status: (data: SystemStatus) => void
  voice_status: (data: VoiceStatus) => void
  transcription_update: (data: TranscriptionUpdate) => void
  music_status: (data: MusicStatus) => void
  dj_status: (data: DJStatus) => void
  system_mode_change: (data: SystemModeStatus) => void
  mode_transition: (data: ModeTransitionStatus) => void
  performance_metrics: (data: PerformanceMetrics) => void
  service_status_update: (data: any) => void
  cantina_event: (data: any) => void
  system_log: (data: any) => void
  error: (data: any) => void
  event_replay: (data: any) => void
}

export const useSocket = () => {
  const [socket, setSocket] = useState<Socket | null>(null)
  const [connected, setConnected] = useState(false)
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>({ 
    status: 'idle', 
    timestamp: new Date().toISOString() 
  })
  const [lastTranscription, setLastTranscription] = useState<TranscriptionUpdate | null>(null)
  const [musicStatus, setMusicStatus] = useState<MusicStatus | null>(null)
  const [djStatus, setDJStatus] = useState<DJStatus | null>(null)
  const [systemMode, setSystemMode] = useState<SystemModeStatus>({ 
    current_mode: 'IDLE', 
    timestamp: new Date().toISOString() 
  })
  const [modeTransition, setModeTransition] = useState<ModeTransitionStatus | null>(null)
  const [performanceMetrics, setPerformanceMetrics] = useState<PerformanceMetrics | null>(null)
  const [eventCount, setEventCount] = useState(0)
  const [logs, setLogs] = useState<LogEntry[]>([])
  
  const socketRef = useRef<Socket | null>(null)
  const maxLogs = 100

  useEffect(() => {
    const newSocket = io('http://localhost:8000', {
      transports: ['websocket'],
      autoConnect: true,
    })

    socketRef.current = newSocket
    setSocket(newSocket)

    // Connection events
    newSocket.on('connect', () => {
      console.log('Connected to bridge service')
      setConnected(true)
      
      // Subscribe to all events
      newSocket.emit('subscribe_events', {
        events: ['voice', 'music', 'system', 'dj', 'leds']
      })
    })

    newSocket.on('disconnect', () => {
      console.log('Disconnected from bridge service')
      setConnected(false)
    })

    newSocket.on('connect_error', (error) => {
      console.error('Connection error:', error)
      setConnected(false)
    })

    // System events
    newSocket.on('system_status', (data: SystemStatus) => {
      setSystemStatus(data)
    })

    // Voice events
    newSocket.on('voice_status', (data: VoiceStatus) => {
      setVoiceStatus(data)
    })

    newSocket.on('transcription_update', (data: TranscriptionUpdate) => {
      setLastTranscription(data)
    })

    // Music events
    newSocket.on('music_status', (data: MusicStatus) => {
      setMusicStatus(data)
    })

    // DJ events
    newSocket.on('dj_status', (data: DJStatus) => {
      setDJStatus(data)
    })

    // System mode events
    newSocket.on('system_mode_change', (data: SystemModeStatus) => {
      console.log('System mode changed:', data)
      setSystemMode(data)
    })

    newSocket.on('mode_transition', (data: ModeTransitionStatus) => {
      console.log('Mode transition:', data)
      setModeTransition(data)
    })

    // Performance metrics
    newSocket.on('performance_metrics', (data: PerformanceMetrics) => {
      setPerformanceMetrics(data)
    })

    // Function to handle system events as log entries (moved from SystemTab)
    const handleSystemEvent = (data: any) => {
      // Handle system_log events with nested data structure
      const logData = data.data || data
      const message = logData.message || data.message || JSON.stringify(data)
      const service = logData.service || data.service || data.topic || 'System'
      const level = logData.level || data.level || 'INFO'
      const timestamp = logData.timestamp || data.timestamp || new Date().toISOString()
      
      const logEntry: LogEntry = {
        id: Date.now() + Math.random().toString(),
        timestamp: new Date(timestamp).toLocaleString(),
        level,
        service,
        message
      }
      
      setLogs(prev => {
        const newLogs = [...prev, logEntry]
        return newLogs.slice(-maxLogs)
      })
    }

    // Service status updates
    newSocket.on('service_status_update', (data: any) => {
      console.log('Service status update:', data)
      // Trigger re-render of system status
      setSystemStatus(prev => prev ? { ...prev } : null)
    })

    // General CantinaOS events
    newSocket.on('cantina_event', (data: any) => {
      console.log('CantinaOS event:', data)
      setEventCount(prev => prev + 1)
      
      // Handle as potential log entry
      handleSystemEvent(data)
    })

    // System log events
    newSocket.on('system_log', (data: any) => {
      console.log('System log:', data)
      setEventCount(prev => prev + 1)
      
      // Handle as log entry
      handleSystemEvent(data)
    })

    // Error events
    newSocket.on('error', (data: any) => {
      console.error('Bridge error:', data)
    })

    // Event replay for reconnections
    newSocket.on('event_replay', (data: any) => {
      console.log('Replayed event:', data)
    })

    return () => {
      newSocket.close()
    }
  }, [])

  // Socket command functions
  const sendVoiceCommand = (action: 'start' | 'stop', text?: string) => {
    if (socket) {
      socket.emit('voice_command', { action, text })
    }
  }

  const sendMusicCommand = (action: string, trackId?: string, volume?: number) => {
    if (socket) {
      socket.emit('music_command', { 
        action, 
        track_id: trackId, 
        volume 
      })
    }
  }

  const sendDJCommand = (action: string, autoTransition?: boolean, interval?: number) => {
    if (socket) {
      socket.emit('dj_command', { 
        action, 
        auto_transition: autoTransition, 
        interval 
      })
    }
  }

  const sendSystemCommand = (action: string, mode?: string) => {
    if (socket) {
      socket.emit('system_command', { 
        action, 
        mode 
      })
    }
  }

  return {
    socket,
    connected,
    systemStatus,
    voiceStatus,
    lastTranscription,
    musicStatus,
    djStatus,
    systemMode,
    modeTransition,
    performanceMetrics,
    eventCount,
    logs,
    sendVoiceCommand,
    sendMusicCommand,
    sendDJCommand,
    sendSystemCommand,
  }
}