'use client'

import { useEffect, useState, useRef } from 'react'
import { io, Socket } from 'socket.io-client'
import { 
  VoiceCommandSchema,
  MusicCommandSchema, 
  DJCommandSchema,
  SystemCommandSchema,
  VoiceActionEnum,
  MusicActionEnum,
  DJActionEnum,
  SystemActionEnum,
  SystemModeEnum,
  WebCommandResponse,
  WebCommandError
} from '../types/schemas'

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

export interface ConversationMessage {
  id: string
  speaker: 'user' | 'dj_r3x'
  text: string
  timestamp: string
  confidence?: number
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
  const [conversationHistory, setConversationHistory] = useState<ConversationMessage[]>([])
  
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
      console.log('🔌 Connected to bridge service at http://localhost:8000')
      setConnected(true)
      
      // Subscribe to all events
      newSocket.emit('subscribe_events', {
        events: ['voice', 'music', 'system', 'dj', 'leds']
      })
    })

    newSocket.on('disconnect', () => {
      console.log('🔌 Disconnected from bridge service')
      setConnected(false)
    })

    // Add generic event listener to see all events
    newSocket.onAny((eventName, ...args) => {
      console.log(`🔌 Socket event received: ${eventName}`, args)
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
    newSocket.on('voice_status', (data: any) => {
      console.log('🎤 [Voice] Voice status update received:', data)
      // Handle nested data structure from WebBridge (same fix as music status)
      const voiceData = data.data || data
      console.log('🎤 [Voice] Voice data processed:', voiceData)
      setVoiceStatus(voiceData)
    })

    newSocket.on('transcription_update', (data: any) => {
      console.log('🎤 [Voice] Transcription update received:', data)
      // Handle nested data structure from WebBridge (same fix as music status)
      const transcriptionData = data.data || data
      console.log('🎤 [Voice] Transcription data processed:', transcriptionData)
      setLastTranscription(transcriptionData)
      
      // Add final user transcriptions to conversation history
      if (transcriptionData.final && transcriptionData.text) {
        const userMessage: ConversationMessage = {
          id: `user-${Date.now()}`,
          speaker: 'user',
          text: transcriptionData.text,
          timestamp: transcriptionData.timestamp || new Date().toISOString(),
          confidence: transcriptionData.confidence
        }
        setConversationHistory(prev => [...prev, userMessage])
      }
    })

    // Music events
    newSocket.on('music_status', (data: MusicStatus) => {
      const unwrappedData = (data as any).data || data
      console.log('🎵 [useSocket] Music status received:', data)
      console.log('🎵 [useSocket] Unwrapped music status:', unwrappedData)
      setMusicStatus(unwrappedData)
    })

    // DJ events
    newSocket.on('dj_status', (data: DJStatus) => {
      setDJStatus(data)
    })

    // System mode events
    newSocket.on('system_mode_change', (data: any) => {
      console.log('🎯 [useSocket] System mode change received:', data)
      // Handle nested data structure from WebBridge (same fix as music status)
      const systemData = data.data || data
      console.log('🎯 [useSocket] System mode data processed:', systemData)
      setSystemMode(systemData)
    })

    newSocket.on('mode_transition', (data: any) => {
      console.log('🔄 [useSocket] Mode transition received:', data)
      // Handle nested data structure from WebBridge (same fix as music status)
      const transitionData = data.data || data
      console.log('🔄 [useSocket] Mode transition data processed:', transitionData)
      setModeTransition(transitionData)
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

    // LLM Response events for conversation history
    newSocket.on('llm_response', (data: any) => {
      console.log('🤖 [LLM] Response received:', data)
      // Handle nested data structure from WebBridge
      const responseData = data.data || data
      
      if (responseData.response || responseData.text || responseData.content) {
        const djMessage: ConversationMessage = {
          id: `dj_r3x-${Date.now()}`,
          speaker: 'dj_r3x',
          text: responseData.response || responseData.text || responseData.content,
          timestamp: responseData.timestamp || new Date().toISOString()
        }
        setConversationHistory(prev => [...prev, djMessage])
      }
    })

    // Event replay for reconnections
    newSocket.on('event_replay', (data: any) => {
      console.log('Replayed event:', data)
    })

    return () => {
      newSocket.close()
    }
  }, [])

  // Type-safe Socket command functions with generated schemas
  const sendVoiceCommand = (
    action: VoiceActionEnum, 
    options?: { callback?: (response: WebCommandResponse | WebCommandError) => void }
  ) => {
    if (socket) {
      const command: Omit<VoiceCommandSchema, 'source' | 'timestamp' | 'command_id'> = {
        action
      }
      
      if (options?.callback) {
        socket.emit('voice_command', command, options.callback)
      } else {
        socket.emit('voice_command', command)
      }
    }
  }

  const sendMusicCommand = (
    action: MusicActionEnum,
    options?: {
      track_name?: string
      track_id?: string
      volume_level?: number
      callback?: (response: WebCommandResponse | WebCommandError) => void
    }
  ) => {
    if (socket) {
      const command: Omit<MusicCommandSchema, 'source' | 'timestamp' | 'command_id'> = {
        action,
        ...(options?.track_name && { track_name: options.track_name }),
        ...(options?.track_id && { track_id: options.track_id }),
        ...(options?.volume_level !== undefined && { volume_level: options.volume_level })
      }
      
      if (options?.callback) {
        socket.emit('music_command', command, options.callback)
      } else {
        socket.emit('music_command', command)
      }
    }
  }

  const sendDJCommand = (
    action: DJActionEnum, 
    options?: {
      auto_transition?: boolean
      transition_duration?: number
      genre_preference?: string
      callback?: (response: WebCommandResponse | WebCommandError) => void
    }
  ) => {
    if (socket) {
      const command: Omit<DJCommandSchema, 'source' | 'timestamp' | 'command_id'> = {
        action,
        ...(options?.auto_transition !== undefined && { auto_transition: options.auto_transition }),
        ...(options?.transition_duration !== undefined && { transition_duration: options.transition_duration }),
        ...(options?.genre_preference && { genre_preference: options.genre_preference })
      }
      
      if (options?.callback) {
        socket.emit('dj_command', command, options.callback)
      } else {
        socket.emit('dj_command', command)
      }
    }
  }

  const sendSystemCommand = (
    action: SystemActionEnum, 
    options?: {
      mode?: SystemModeEnum
      restart_delay?: number
      callback?: (response: WebCommandResponse | WebCommandError) => void
    }
  ) => {
    if (socket) {
      const command: Omit<SystemCommandSchema, 'source' | 'timestamp' | 'command_id'> = {
        action,
        ...(options?.mode && { mode: options.mode }),
        ...(options?.restart_delay !== undefined && { restart_delay: options.restart_delay })
      }
      
      if (options?.callback) {
        socket.emit('system_command', command, options.callback)
      } else {
        socket.emit('system_command', command)
      }
    }
  }

  // Type guard functions for response validation
  const isWebCommandError = (response: any): response is WebCommandError => {
    return response && typeof response === 'object' && response.error === true
  }

  const isWebCommandResponse = (response: any): response is WebCommandResponse => {
    return response && typeof response === 'object' && typeof response.success === 'boolean'
  }

  // Helper function to handle command responses with proper error logging
  const handleCommandResponse = (
    commandType: string,
    response: any,
    onSuccess?: (data: any) => void,
    onError?: (error: WebCommandError) => void
  ) => {
    if (isWebCommandError(response)) {
      console.error(`❌ ${commandType} command failed:`, response.message)
      if (response.validation_errors?.length) {
        console.error('Validation errors:', response.validation_errors)
      }
      onError?.(response)
    } else if (isWebCommandResponse(response)) {
      if (response.success) {
        console.log(`✅ ${commandType} command successful:`, response.message)
        onSuccess?.(response.data)
      } else {
        console.error(`❌ ${commandType} command failed:`, response.message)
        onError?.({
          error: true,
          message: response.message,
          command: commandType,
          validation_errors: [],
          timestamp: response.timestamp || new Date().toISOString()
        })
      }
    } else {
      console.warn(`⚠️ ${commandType} received unexpected response format:`, response)
    }
  }

  // Enhanced command functions with response handling
  const sendVoiceCommandWithResponse = (
    action: VoiceActionEnum,
    onSuccess?: (data: any) => void,
    onError?: (error: WebCommandError) => void
  ) => {
    sendVoiceCommand(action, {
      callback: (response) => handleCommandResponse('Voice', response, onSuccess, onError)
    })
  }

  const sendMusicCommandWithResponse = (
    action: MusicActionEnum,
    options?: {
      track_name?: string
      track_id?: string
      volume_level?: number
    },
    onSuccess?: (data: any) => void,
    onError?: (error: WebCommandError) => void
  ) => {
    sendMusicCommand(action, {
      ...options,
      callback: (response) => handleCommandResponse('Music', response, onSuccess, onError)
    })
  }

  const sendDJCommandWithResponse = (
    action: DJActionEnum,
    options?: {
      auto_transition?: boolean
      transition_duration?: number
      genre_preference?: string
    },
    onSuccess?: (data: any) => void,
    onError?: (error: WebCommandError) => void
  ) => {
    sendDJCommand(action, {
      ...options,
      callback: (response) => handleCommandResponse('DJ', response, onSuccess, onError)
    })
  }

  const sendSystemCommandWithResponse = (
    action: SystemActionEnum,
    options?: {
      mode?: SystemModeEnum
      restart_delay?: number
    },
    onSuccess?: (data: any) => void,
    onError?: (error: WebCommandError) => void
  ) => {
    sendSystemCommand(action, {
      ...options,
      callback: (response) => handleCommandResponse('System', response, onSuccess, onError)
    })
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
    conversationHistory,
    clearConversationHistory: () => setConversationHistory([]),
    // Type-safe command functions (without response handling)
    sendVoiceCommand,
    sendMusicCommand,
    sendDJCommand,
    sendSystemCommand,
    // Enhanced command functions with response handling
    sendVoiceCommandWithResponse,
    sendMusicCommandWithResponse,
    sendDJCommandWithResponse,
    sendSystemCommandWithResponse,
    // Type guards and utilities
    isWebCommandError,
    isWebCommandResponse,
    handleCommandResponse,
    // Note: Enum types are imported and used directly in function signatures
    // Components should import them directly from '../types/schemas'
  }
}