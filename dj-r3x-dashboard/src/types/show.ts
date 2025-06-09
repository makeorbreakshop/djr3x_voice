// Show Tab TypeScript interfaces and types

export interface ShowState {
  characterState: 'IDLE' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'DJING'
  systemMode: string
  isInteractive: boolean
}

export interface CharacterStateProps {
  characterState: ShowState['characterState']
}

export interface ConversationMessage {
  id: string
  speaker: 'visitor' | 'dj_r3x'
  text: string
  timestamp: Date
  isAnimating?: boolean
}

export interface MusicTrack {
  title: string
  artist: string
  duration?: string
  progress?: number
  volume?: number
  track_id?: string
}

export interface DJCommentary {
  text: string
  timestamp: Date
  type: 'track_intro' | 'transition' | 'general'
}

export interface ServiceStatus {
  name: string
  status: 'RUNNING' | 'STOPPED' | 'ERROR' | 'STARTING' | 'STOPPING'
  uptime?: number
  lastUpdate?: Date
}

export interface ActivityEvent {
  id: string
  type: 'system' | 'voice' | 'music' | 'dj' | 'service'
  title: string
  description: string
  timestamp: Date
  icon: string
  severity: 'info' | 'success' | 'warning' | 'error'
}

// CantinaOS Event Payloads (mapped to web interface)
export interface SystemModeChangePayload {
  mode: string
  current_mode?: string
  previous_mode?: string
  timestamp?: string
}

export interface VoiceEventPayload {
  text?: string
  transcript?: string
  confidence?: number
  timestamp?: string
}

export interface LLMResponsePayload {
  response?: string
  text?: string
  content?: string
  timestamp?: string
}

export interface MusicEventPayload {
  title?: string
  track_name?: string
  artist?: string
  duration?: string
  volume?: number
  level?: number
  timestamp?: string
}

export interface DJModePayload {
  is_active?: boolean
  dj_mode_active?: boolean
  auto_transition?: boolean
  timestamp?: string
}

export interface ServiceStatusPayload {
  service_name?: string
  service?: string
  status?: string
  uptime?: number
  message?: string
  timestamp?: string
}

// Star Wars UI Theme Types
export interface StarWarsTheme {
  colors: {
    primary: string
    secondary: string
    accent: string
    background: string
    text: string
  }
  fonts: {
    mono: string
    display: string
  }
  animations: {
    glow: string
    pulse: string
    scanline: string
  }
}