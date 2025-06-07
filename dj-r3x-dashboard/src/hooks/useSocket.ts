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

export interface PerformanceMetrics {
  events_per_minute: number
  cpu_usage: number
  memory_usage: number
  uptime: string
}

interface SocketEvents {
  system_status: (data: SystemStatus) => void
  voice_status: (data: VoiceStatus) => void
  transcription_update: (data: TranscriptionUpdate) => void
  music_status: (data: MusicStatus) => void
  dj_status: (data: DJStatus) => void
  performance_metrics: (data: PerformanceMetrics) => void
  service_status_update: (data: any) => void
  cantina_event: (data: any) => void
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
  const [performanceMetrics, setPerformanceMetrics] = useState<PerformanceMetrics | null>(null)
  const [eventCount, setEventCount] = useState(0)
  
  const socketRef = useRef<Socket | null>(null)

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

    // Performance metrics
    newSocket.on('performance_metrics', (data: PerformanceMetrics) => {
      setPerformanceMetrics(data)
    })

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

  return {
    socket,
    connected,
    systemStatus,
    voiceStatus,
    lastTranscription,
    musicStatus,
    djStatus,
    performanceMetrics,
    eventCount,
    sendVoiceCommand,
    sendMusicCommand,
    sendDJCommand,
  }
}