'use client'

import { useEffect, useState } from 'react'
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
} from 'lucide-react'
import { Slider } from '@/components/ui/slider'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Progress } from '@/components/ui/progress'

export default function VoiceTab() {
  const { 
    voiceStatus, 
    lastTranscription, 
    sendVoiceCommand,
    sendSystemCommand,
    connected,
    systemStatus,
    systemMode,
    modeTransition 
  } = useSocketContext()

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

  // Track interaction phase for two-phase recording
  const [interactionPhase, setInteractionPhase] = useState<'idle' | 'engaged' | 'recording'>('idle')

  // Client-side hydration effect
  useEffect(() => {
    setIsClient(true)
  }, [])

  // Update interaction phase based on system mode
  useEffect(() => {
    if (systemMode.current_mode === 'INTERACTIVE' && voiceStatus.status === 'recording') {
      setInteractionPhase('recording')
    } else if (systemMode.current_mode === 'INTERACTIVE') {
      setInteractionPhase('engaged')
    } else {
      setInteractionPhase('idle')
    }
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

  const isRecording = voiceStatus.status === 'recording'
  const isProcessing = voiceStatus.status === 'processing'

  const handleVoiceInteraction = () => {
    if (interactionPhase === 'idle') {
      // Phase 1: Engage INTERACTIVE mode
      sendSystemCommand(SystemActionEnum.SET_MODE, { mode: SystemModeEnum.INTERACTIVE })
    } else if (interactionPhase === 'engaged') {
      // Phase 2: Start recording
      sendVoiceCommand(VoiceActionEnum.START)
    } else if (interactionPhase === 'recording') {
      // Stop recording
      sendVoiceCommand(VoiceActionEnum.STOP)
    }
  }

  return (
    <div className="space-y-6">
      {/* System Mode Display */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          SYSTEM MODE STATUS
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="space-y-2">
            <div className="text-sm text-sw-blue-300">Current Mode</div>
            <div className={`text-2xl font-bold ${
              systemMode.current_mode === 'IDLE' ? 'text-sw-blue-400' :
              systemMode.current_mode === 'AMBIENT' ? 'text-sw-yellow' :
              systemMode.current_mode === 'INTERACTIVE' ? 'text-sw-green' : 'text-sw-blue-400'
            }`}>
              {systemMode.current_mode}
            </div>
          </div>
          <div className="space-y-2">
            <div className="text-sm text-sw-blue-300">Interaction Phase</div>
            <div className={`text-lg font-semibold ${
              interactionPhase === 'idle' ? 'text-sw-blue-400' :
              interactionPhase === 'engaged' ? 'text-sw-yellow' :
              interactionPhase === 'recording' ? 'text-sw-green animate-pulse' : 'text-sw-blue-400'
            }`}>
              {interactionPhase.toUpperCase()}
            </div>
          </div>
          <div className="space-y-2">
            <div className="text-sm text-sw-blue-300">Mode Transition</div>
            <div className={`text-sm ${
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
              <div className="text-xs text-sw-red/70">{modeTransition.error}</div>
            )}
          </div>
        </div>
      </div>

      {/* Voice Control */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-6 sw-text-glow">
          VOICE INTERACTION CONTROL
        </h3>
        
        <div className="flex justify-center mb-6">
          <button
            onClick={handleVoiceInteraction}
            disabled={!connected}
            className={`
              w-32 h-32 rounded-full text-white font-bold text-lg
              transition-all duration-200 transform hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed
              ${interactionPhase === 'recording' 
                ? 'bg-sw-red hover:bg-red-600 animate-pulse' 
                : interactionPhase === 'engaged'
                ? 'bg-sw-green hover:bg-green-600 sw-border-glow animate-pulse'
                : 'bg-sw-blue-600 hover:bg-sw-blue-500 sw-border-glow'
              }
            `}
          >
            {interactionPhase === 'recording' ? 'STOP' : 
             interactionPhase === 'engaged' ? 'RECORD' : 
             'ENGAGE'}
          </button>
        </div>

        <div className="text-center">
          <p className="text-sm text-sw-blue-300/70">
            {!connected 
              ? 'Bridge service disconnected'
              : interactionPhase === 'recording'
                ? 'Recording in progress... Click to stop' 
                : interactionPhase === 'engaged'
                ? 'System ready for voice input. Click to start recording.'
                : 'Click to engage INTERACTIVE mode'
            }
          </p>
          {!connected && (
            <p className="text-xs text-sw-red/70 mt-1">
              Connect to CantinaOS to enable voice recording
            </p>
          )}
          {connected && interactionPhase === 'idle' && (
            <p className="text-xs text-sw-blue-400/70 mt-1">
              Two-phase interaction: Engage → Record (matches CLI behavior)
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Real-time Transcription */}
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            TRANSCRIPTION
          </h3>
          <div className="space-y-4">
            <div className="h-40 bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20 p-4 overflow-y-auto">
              {lastTranscription ? (
                <div className="text-sw-blue-100">{lastTranscription.text}</div>
              ) : (
                <div className="text-sw-blue-300/50 text-sm italic">
                  Voice transcription will appear here...
                </div>
              )}
            </div>
            
            {lastTranscription && lastTranscription.confidence > 0 && (
              <div className="flex items-center space-x-2">
                <span className="text-xs text-sw-blue-300">Confidence:</span>
                <div className="flex-1 bg-sw-dark-700 rounded-full h-2">
                  <div 
                    className="bg-sw-blue-500 h-2 rounded-full transition-all duration-300"
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

        {/* Processing Status */}
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            PROCESSING STATUS
          </h3>
          <div className="space-y-3">
            <StatusItem 
              label="Voice Input" 
              status={pipelineStatus.voiceInput as any}
              subtitle={systemStatus?.services?.deepgram_direct_mic?.status === 'online' ? 'Deepgram Connected' : 'Service Offline'}
            />
            <StatusItem 
              label="Speech Recognition" 
              status={pipelineStatus.speechRecognition as any}
              subtitle={lastTranscription?.confidence ? `${Math.round(lastTranscription.confidence * 100)}% confidence` : undefined}
            />
            <StatusItem 
              label="AI Processing" 
              status={pipelineStatus.aiProcessing as any}
              subtitle={systemStatus?.services?.gpt_service?.status === 'online' ? 'GPT Connected' : 'Service Offline'}
            />
            <StatusItem 
              label="Response Synthesis" 
              status={pipelineStatus.responseSynthesis as any}
              subtitle={systemStatus?.services?.elevenlabs_service?.status === 'online' ? 'ElevenLabs Connected' : 'Service Offline'}
            />
            <StatusItem 
              label="Audio Playback" 
              status={pipelineStatus.audioPlayback as any}
              subtitle="VLC Output"
            />
          </div>
        </div>
      </div>

      {/* Voice Levels */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          AUDIO LEVELS
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="block text-sm text-sw-blue-300 mb-2">Input Level</label>
            <div className="bg-sw-dark-700 rounded-lg h-4 overflow-hidden">
              <div className="h-full bg-gradient-to-r from-sw-green via-sw-yellow to-sw-red w-0 transition-all duration-100"></div>
            </div>
          </div>
          <div>
            <label className="block text-sm text-sw-blue-300 mb-2">Output Level</label>
            <div className="bg-sw-dark-700 rounded-lg h-4 overflow-hidden">
              <div className="h-full bg-gradient-to-r from-sw-blue-500 to-sw-blue-300 w-0 transition-all duration-100"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

interface StatusItemProps {
  label: string
  status: 'active' | 'processing' | 'idle' | 'error'
  subtitle?: string
}

function StatusItem({ label, status, subtitle }: StatusItemProps) {
  const getStatusClass = () => {
    switch (status) {
      case 'active':
        return 'sw-status-online'
      case 'processing':
        return 'sw-status-warning animate-pulse'
      case 'error':
        return 'sw-status-offline'
      case 'idle':
      default:
        return 'bg-sw-dark-600'
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
    <div className="flex items-center justify-between p-3 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20">
      <div className="flex-1">
        <div className="text-sm text-sw-blue-200">{label}</div>
        {subtitle && (
          <div className="text-xs text-sw-blue-300/70 mt-1">{subtitle}</div>
        )}
      </div>
      <div className="flex items-center space-x-2">
        <div className={`w-2 h-2 rounded-full ${getStatusClass()}`}></div>
        <span className="text-xs text-sw-blue-300 font-mono uppercase">
          {getStatusText()}
        </span>
      </div>
    </div>
  )
}