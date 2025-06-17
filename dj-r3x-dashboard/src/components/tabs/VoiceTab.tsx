'use client'

import { useEffect, useState, useRef } from 'react'
import { useSocketContext } from '@/contexts/SocketContext'
import AudioSpectrum from '../AudioSpectrum.dynamic'
import { VoiceActionEnum, SystemActionEnum, SystemModeEnum } from '../../types/schemas'
import {
  Activity,
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Zap,
  Cpu,
  Server,
  Cloud,
  BrainCircuit,
  Settings,
  Bot,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'

export default function VoiceTab() {
  const { 
    voiceStatus, 
    lastTranscription, 
    sendVoiceCommand,
    socket,
    connected,
    systemStatus,
    systemMode,
    modeTransition,
    conversationHistory,
    clearConversationHistory
  } = useSocketContext()
  
  // Ref for auto-scrolling conversation
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Client-side hydration fix
  const [isClient, setIsClient] = useState(false)

  // Track processing pipeline status
  const [pipelineStatus, setPipelineStatus] = useState({
    voiceInput: 'idle',
    speechRecognition: 'idle', 
    aiProcessing: 'idle',
    responseSynthesis: 'idle',
    audioPlayback: 'idle'
  })

  // Track interaction phase for two-phase recording (like DJTab's djModeActive)
  const [interactionPhase, setInteractionPhase] = useState<'idle' | 'engaged' | 'recording'>('idle')
  
  // UI State for collapsible sections
  const [showAdvancedStatus, setShowAdvancedStatus] = useState(false)
  
  // Auto-scroll to bottom when new messages arrive
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  // Client-side hydration effect
  useEffect(() => {
    setIsClient(true)
  }, [])
  
  // Auto-scroll when conversation updates
  useEffect(() => {
    scrollToBottom()
  }, [conversationHistory])

  // Sync interaction phase with voice status and system mode (but let local state take precedence like DJTab)
  useEffect(() => {
    // Only update from server state when recording status changes or system goes to IDLE
    if (voiceStatus.status === 'recording' && interactionPhase !== 'recording') {
      setInteractionPhase('recording')
    } else if (voiceStatus.status === 'idle' && systemMode.current_mode === 'IDLE' && interactionPhase !== 'idle') {
      // System returned to IDLE - sync with server state
      setInteractionPhase('idle')
    }
    // Don't override local state for 'engaged' phase - let user actions drive the UI
  }, [systemMode.current_mode, voiceStatus.status])

  // Update pipeline status based on voice events and completion feedback
  useEffect(() => {
    if (voiceStatus.status === 'recording') {
      setPipelineStatus(prev => ({
        ...prev,
        voiceInput: 'active',
        speechRecognition: 'idle',
        aiProcessing: 'idle',
        responseSynthesis: 'idle',
        audioPlayback: 'idle'
      }))
    } else if (voiceStatus.status === 'processing') {
      setPipelineStatus(prev => ({
        ...prev,
        voiceInput: 'idle',
        speechRecognition: 'processing',
        aiProcessing: lastTranscription?.final ? 'processing' : 'idle',
        responseSynthesis: 'idle',
        audioPlayback: 'idle'
      }))
    } else if (voiceStatus.status === 'idle' && systemMode.current_mode === 'IDLE') {
      // Voice interaction completed, reset pipeline
      setPipelineStatus(prev => ({
        ...prev,
        voiceInput: 'idle',
        speechRecognition: 'idle',
        aiProcessing: 'idle',
        responseSynthesis: 'idle',
        audioPlayback: 'idle'
      }))
    }
  }, [voiceStatus, lastTranscription, systemMode.current_mode])

  // Update pipeline status for speech synthesis and playback based on system mode transitions
  useEffect(() => {
    if (modeTransition?.status === 'completed' && modeTransition?.new_mode === 'IDLE') {
      // Voice interaction completed - show completion status briefly
      setPipelineStatus(prev => ({
        ...prev,
        aiProcessing: 'idle',
        responseSynthesis: 'idle',
        audioPlayback: 'idle'
      }))
    } else if (modeTransition?.status === 'started' && modeTransition?.old_mode === 'INTERACTIVE') {
      // System transitioning back to IDLE - show synthesis and playback as active
      setPipelineStatus(prev => ({
        ...prev,
        responseSynthesis: 'active',
        audioPlayback: 'active'
      }))
    } else if (systemMode.current_mode === 'INTERACTIVE' && voiceStatus.status === 'idle' && lastTranscription?.final) {
      // AI processing and speech synthesis active
      setPipelineStatus(prev => ({
        ...prev,
        aiProcessing: 'processing',
        responseSynthesis: 'processing'
      }))
    }
  }, [modeTransition, systemMode.current_mode, voiceStatus.status, lastTranscription])

  // Socket event listeners for voice recording state synchronization
  useEffect(() => {
    if (!socket) return

    // Helper to unwrap WebBridge payload format (same pattern as DJTab)
    const unwrap = (raw: any) => (raw && raw.data ? raw.data : raw)

    // Handle voice status changes from backend - WebBridge sends VOICE_LISTENING_STOPPED as "voice_status" events
    const handleVoiceStatusChange = (raw: any) => {
      const data = unwrap(raw)
      console.log('Voice status change:', data)
      
      // Check if this is a "processing" status which indicates recording stopped
      // WebBridge sends status: "processing" when VOICE_LISTENING_STOPPED event occurs
      // Should switch back to "engaged" state (showing TALK button) not idle
      if (data.status === 'processing' && interactionPhase === 'recording') {
        console.log('Voice recording stopped detected - switching back to engaged')
        setInteractionPhase('engaged')
      }
    }

    socket.on('voice_status', handleVoiceStatusChange)

    return () => {
      socket.off('voice_status', handleVoiceStatusChange)
    }
  }, [socket, interactionPhase])

  const isRecording = voiceStatus.status === 'recording'
  const isProcessing = voiceStatus.status === 'processing'

  const handleVoiceInteraction = () => {
    if (interactionPhase === 'idle') {
      // Phase 1: Engage INTERACTIVE mode using simple CLI command like DJ Mode
      // Update state immediately for responsive UI (like DJTab)
      setInteractionPhase('engaged')
      
      if (socket) {
        socket.emit('command', { command: 'engage' })
      }
    } else if (interactionPhase === 'engaged') {
      // Phase 2: Start recording using correct MIC_RECORDING_START event (like MouseInputService)
      // Update state immediately for responsive UI
      setInteractionPhase('recording')
      
      if (socket) {
        socket.emit('voice_recording_start', {})
      }
    } else if (interactionPhase === 'recording') {
      // Stop recording using correct MIC_RECORDING_STOP event (like MouseInputService)
      // Update state immediately for responsive UI (like DJTab pattern)
      setInteractionPhase('engaged')
      
      if (socket) {
        socket.emit('voice_recording_stop', {})
      }
    }
  }

  const handleDisengage = () => {
    // Return to IDLE mode using simple CLI command
    // Update state immediately for responsive UI (like DJTab)
    setInteractionPhase('idle')
    
    // Clear conversation history on disengage (as planned in dev log)
    clearConversationHistory()
    
    if (socket) {
      socket.emit('command', { command: 'disengage' })
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Compact Status Header */}
      <div className="sw-panel mb-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-6">
            <div className="flex items-center space-x-2">
              <span className="text-sm text-sw-blue-300">System:</span>
              <span className={`text-sm font-semibold ${
                systemMode.current_mode === 'IDLE' ? 'text-sw-blue-400' :
                systemMode.current_mode === 'AMBIENT' ? 'text-sw-yellow' :
                systemMode.current_mode === 'INTERACTIVE' ? 'text-sw-green' : 'text-sw-blue-400'
              }`}>
                {systemMode.current_mode}
              </span>
            </div>
            <div className="flex items-center space-x-2">
              <span className="text-sm text-sw-blue-300">Phase:</span>
              <span className={`text-sm font-semibold ${
                interactionPhase === 'idle' ? 'text-sw-blue-400' :
                interactionPhase === 'engaged' ? 'text-sw-yellow' :
                interactionPhase === 'recording' ? 'text-sw-green animate-pulse' : 'text-sw-blue-400'
              }`}>
                {interactionPhase.toUpperCase()}
              </span>
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <div className={`w-2 h-2 rounded-full ${connected ? 'bg-sw-green' : 'bg-sw-red'}`}></div>
            <span className="text-sm text-sw-blue-300">
              {connected ? 'CANTINA OS ONLINE' : 'DISCONNECTED'}
            </span>
          </div>
        </div>
      </div>

      {/* Three-Panel Layout */}
      <div className="flex-1 grid grid-cols-12 gap-4">
        {/* Left Sidebar - Voice Controls */}
        <div className="col-span-12 lg:col-span-3 space-y-4">
          <div className="sw-panel p-4">
            <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
              VOICE CONTROLS
            </h3>
            
            {/* Control Buttons */}
            <div className="space-y-3 mb-6">
              {interactionPhase === 'idle' ? (
                <button
                  onClick={handleVoiceInteraction}
                  disabled={!connected}
                  className="w-full h-16 rounded-lg text-white font-bold text-sm bg-sw-blue-600 hover:bg-sw-blue-500 sw-border-glow transition-all duration-200 transform hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  ENGAGE
                </button>
              ) : (
                <div className="space-y-3">
                  <button
                    onClick={handleVoiceInteraction}
                    disabled={!connected}
                    className={`
                      w-full h-16 rounded-lg text-white font-bold text-sm transition-all duration-200 transform hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed
                      ${interactionPhase === 'recording'
                        ? 'bg-sw-red hover:bg-red-600 animate-pulse'
                        : 'bg-sw-green hover:bg-green-600 sw-border-glow'
                      }
                    `}
                  >
                    {interactionPhase === 'recording' ? 'STOP' : 'TALK'}
                  </button>
                  <button
                    onClick={handleDisengage}
                    disabled={!connected}
                    className="w-full h-12 rounded-lg text-white font-bold text-sm bg-sw-yellow hover:bg-yellow-600 sw-border-glow transition-all duration-200 transform hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    DISENGAGE
                  </button>
                </div>
              )}
            </div>

            {/* Status Message */}
            <div className="text-center">
              <p className="text-xs text-sw-blue-300/70">
                {!connected 
                  ? 'Bridge service disconnected'
                  : interactionPhase === 'recording'
                    ? 'Recording... Click STOP to finish' 
                    : interactionPhase === 'engaged'
                    ? 'Ready for voice input'
                    : 'Click ENGAGE to start'
                }
              </p>
            </div>
          </div>

          {/* Audio Levels */}
          <div className="sw-panel p-4">
            <h3 className="text-sm font-semibold text-sw-blue-100 mb-3 sw-text-glow">
              AUDIO LEVELS
            </h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-sw-blue-300 mb-1">Input</label>
                <div className="bg-sw-dark-700 rounded h-2 overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-sw-green via-sw-yellow to-sw-red w-0 transition-all duration-100"></div>
                </div>
              </div>
              <div>
                <label className="block text-xs text-sw-blue-300 mb-1">Output</label>
                <div className="bg-sw-dark-700 rounded h-2 overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-sw-blue-500 to-sw-blue-300 w-0 transition-all duration-100"></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Main Center - Conversation Area */}
        <div className="col-span-12 lg:col-span-6">
          <div className="sw-panel h-full flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
                CONVERSATION
              </h3>
              {conversationHistory.length > 0 && (
                <button
                  onClick={clearConversationHistory}
                  className="text-xs text-sw-blue-400 hover:text-sw-blue-300 px-2 py-1 rounded border border-sw-blue-600/30 hover:border-sw-blue-500/50 transition-colors"
                >
                  Clear
                </button>
              )}
            </div>
            
            {/* Chat Messages Container - Takes full remaining height */}
            <div className="flex-1 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4 overflow-y-auto min-h-0">
              {conversationHistory.length === 0 ? (
                <div className="h-full flex items-center justify-center text-center">
                  <div>
                    <div className="text-sw-blue-300/50 text-base italic mb-3">
                      Start a conversation with DJ R3X
                    </div>
                    <div className="text-sm text-sw-blue-400/60 mb-2">
                      Click ENGAGE → TALK to begin
                    </div>
                    <div className="text-xs text-sw-blue-400/40">
                      Your voice interactions will appear here
                    </div>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  {conversationHistory.map((message, index) => (
                    <div key={message.id} className={`flex ${message.speaker === 'user' ? 'justify-end' : 'justify-start'}`}>
                      <div className={`max-w-[80%] ${message.speaker === 'user' ? 'ml-4' : 'mr-4'}`}>
                        {/* Speaker identifier */}
                        <div className={`flex items-center space-x-2 mb-2 ${message.speaker === 'user' ? 'justify-end' : 'justify-start'}`}>
                          <span className={`text-xs font-mono font-bold ${
                            message.speaker === 'user' ? 'text-sw-yellow' : 'text-sw-blue-300'
                          }`}>
                            {message.speaker === 'user' ? 'USER' : 'DJ R3X'}
                          </span>
                          <span className="text-xs text-sw-blue-400/50 font-mono">
                            {new Date(message.timestamp).toLocaleTimeString()}
                          </span>
                          {message.confidence && (
                            <span className="text-xs text-sw-green/60 font-mono">
                              {Math.round(message.confidence * 100)}%
                            </span>
                          )}
                        </div>
                        
                        {/* Message bubble */}
                        <div className={`
                          p-4 rounded-lg text-base leading-relaxed
                          ${message.speaker === 'user' 
                            ? 'bg-sw-yellow/10 border border-sw-yellow/30 text-sw-blue-100' 
                            : 'bg-sw-blue-600/10 border border-sw-blue-400/30 text-sw-blue-200'
                          }
                        `}>
                          {message.text}
                        </div>
                      </div>
                    </div>
                  ))}
                  
                  {/* Auto-scroll target */}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </div>
            
            {/* Latest Transcription Confidence */}
            {lastTranscription && lastTranscription.confidence > 0 && (
              <div className="flex items-center space-x-2 mt-3 pt-3 border-t border-sw-blue-600/20">
                <span className="text-xs text-sw-blue-300">Confidence:</span>
                <div className="flex-1 bg-sw-dark-700 rounded-full h-2">
                  <div 
                    className="bg-sw-green h-2 rounded-full transition-all duration-300"
                    style={{ width: isClient ? `${lastTranscription.confidence * 100}%` : '0%' }}
                  ></div>
                </div>
                <span className="text-xs text-sw-blue-200 font-mono">
                  {Math.round(lastTranscription.confidence * 100)}%
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Right Sidebar - Status Information */}
        <div className="col-span-12 lg:col-span-3 space-y-4">
          {/* Service Status */}
          <div className="sw-panel p-4">
            <h3 className="text-sm font-semibold text-sw-blue-100 mb-3 sw-text-glow">
              SERVICE STATUS
            </h3>
            <div className="space-y-2">
              <ServiceStatusItem 
                label="Voice Input" 
                status={systemStatus?.services?.deepgram_direct_mic?.status === 'online' ? 'online' : 'offline'}
                subtitle="Deepgram"
              />
              <ServiceStatusItem 
                label="AI Processing" 
                status={systemStatus?.services?.gpt_service?.status === 'online' ? 'online' : 'offline'}
                subtitle="GPT"
              />
              <ServiceStatusItem 
                label="Voice Synthesis" 
                status={systemStatus?.services?.elevenlabs_service?.status === 'online' ? 'online' : 'offline'}
                subtitle="ElevenLabs"
              />
            </div>
          </div>

          {/* Processing Pipeline */}
          <div className="sw-panel p-4">
            <h3 className="text-sm font-semibold text-sw-blue-100 mb-3 sw-text-glow">
              PIPELINE STATUS
            </h3>
            <div className="space-y-2">
              <PipelineStageItem 
                label="Recording" 
                status={pipelineStatus.voiceInput as any}
              />
              <PipelineStageItem 
                label="Recognition" 
                status={pipelineStatus.speechRecognition as any}
              />
              <PipelineStageItem 
                label="Processing" 
                status={pipelineStatus.aiProcessing as any}
              />
              <PipelineStageItem 
                label="Synthesis" 
                status={pipelineStatus.responseSynthesis as any}
              />
              <PipelineStageItem 
                label="Playback" 
                status={pipelineStatus.audioPlayback as any}
              />
            </div>
          </div>

          {/* Advanced Status (Collapsible) */}
          <div className="sw-panel p-4">
            <button
              onClick={() => setShowAdvancedStatus(!showAdvancedStatus)}
              className="flex items-center justify-between w-full text-left"
            >
              <h3 className="text-sm font-semibold text-sw-blue-100 sw-text-glow">
                ADVANCED STATUS
              </h3>
              {showAdvancedStatus ? (
                <ChevronUp className="w-4 h-4 text-sw-blue-400" />
              ) : (
                <ChevronDown className="w-4 h-4 text-sw-blue-400" />
              )}
            </button>
            
            {showAdvancedStatus && (
              <div className="mt-3 space-y-3">
                {/* Mode Transition */}
                <div>
                  <div className="text-xs text-sw-blue-300 mb-1">Mode Transition</div>
                  <div className={`text-xs ${
                    modeTransition?.status === 'started' ? 'text-sw-yellow' :
                    modeTransition?.status === 'completed' ? 'text-sw-green' :
                    modeTransition?.status === 'failed' ? 'text-sw-red' : 'text-sw-blue-400'
                  }`}>
                    {modeTransition ? 
                      `${modeTransition.old_mode} → ${modeTransition.new_mode}` : 
                      'No active transition'
                    }
                  </div>
                  {modeTransition?.error && (
                    <div className="text-xs text-sw-red/70 mt-1">{modeTransition.error}</div>
                  )}
                </div>

                {/* Latest Transcription Details */}
                {lastTranscription && (
                  <div>
                    <div className="text-xs text-sw-blue-300 mb-1">Latest Transcription</div>
                    <div className="text-xs text-sw-blue-200 bg-sw-dark-700/50 rounded p-2 max-h-20 overflow-y-auto">
                      {lastTranscription.text || 'No transcription'}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// Service Status Component
interface ServiceStatusItemProps {
  label: string
  status: 'online' | 'offline'
  subtitle?: string
}

function ServiceStatusItem({ label, status, subtitle }: ServiceStatusItemProps) {
  return (
    <div className="flex items-center justify-between text-xs">
      <div>
        <div className="text-sw-blue-200">{label}</div>
        {subtitle && <div className="text-sw-blue-400/60">{subtitle}</div>}
      </div>
      <div className="flex items-center space-x-2">
        <div className={`w-2 h-2 rounded-full ${status === 'online' ? 'bg-sw-green' : 'bg-sw-red'}`}></div>
        <span className={`font-mono ${status === 'online' ? 'text-sw-green' : 'text-sw-red'}`}>
          {status.toUpperCase()}
        </span>
      </div>
    </div>
  )
}

// Pipeline Stage Component
interface PipelineStageItemProps {
  label: string
  status: 'active' | 'processing' | 'idle' | 'error'
}

function PipelineStageItem({ label, status }: PipelineStageItemProps) {
  const getStatusClass = () => {
    switch (status) {
      case 'active':
        return 'bg-sw-green'
      case 'processing':
        return 'bg-sw-yellow animate-pulse'
      case 'error':
        return 'bg-sw-red'
      case 'idle':
      default:
        return 'bg-sw-blue-600'
    }
  }

  const getStatusText = () => {
    switch (status) {
      case 'active':
        return 'ACTIVE'
      case 'processing':
        return 'PROCESSING'
      case 'error':
        return 'ERROR'
      case 'idle':
      default:
        return 'IDLE'
    }
  }

  return (
    <div className="flex items-center justify-between text-xs">
      <div className="text-sw-blue-200">{label}</div>
      <div className="flex items-center space-x-2">
        <div className={`w-2 h-2 rounded-full ${getStatusClass()}`}></div>
        <span className="text-sw-blue-300 font-mono">
          {getStatusText()}
        </span>
      </div>
    </div>
  )
}