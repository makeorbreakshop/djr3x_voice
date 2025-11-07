'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'
import DJCharacterStage from '../show/DJCharacterStage'
import ConversationDisplay from '../show/ConversationDisplay'
import DJPerformanceCenter from '../show/DJPerformanceCenter'
import SystemStatusIndicators from '../show/SystemStatusIndicators'
import LiveActivityFeed from '../show/LiveActivityFeed'

interface ShowState {
  characterState: 'IDLE' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'DJING'
  systemMode: string
  isInteractive: boolean
}

export default function ShowTab() {
  const { socket } = useSocketContext()
  const [showState, setShowState] = useState<ShowState>({
    characterState: 'IDLE',
    systemMode: 'IDLE',
    isInteractive: false
  })
  const [systemHealthStatus, setSystemHealthStatus] = useState<'optimal' | 'warning' | 'critical'>('optimal')

  // CantinaOS event integration
  useEffect(() => {
    if (!socket) return

    const handleSystemModeChange = (data: any) => {
      console.log('Show Tab - System mode change:', data)
      setShowState(prev => ({
        ...prev,
        systemMode: data.mode || data.current_mode || 'IDLE',
        isInteractive: (data.mode || data.current_mode) === 'INTERACTIVE'
      }))
    }

    const handleVoiceListeningStarted = () => {
      setShowState(prev => ({ ...prev, characterState: 'LISTENING' }))
    }

    const handleVoiceListeningStopped = () => {
      setShowState(prev => ({ ...prev, characterState: 'THINKING' }))
    }

    const handleTranscriptionFinal = () => {
      setShowState(prev => ({ ...prev, characterState: 'THINKING' }))
    }

    const handleSpeechSynthesisStarted = () => {
      setShowState(prev => ({ ...prev, characterState: 'SPEAKING' }))
    }

    const handleSpeechSynthesisEnded = () => {
      setShowState(prev => ({ 
        ...prev, 
        characterState: prev.isInteractive ? 'LISTENING' : 'IDLE' 
      }))
    }

    const handleDJModeChanged = (data: any) => {
      if (data.is_active || data.dj_mode_active) {
        setShowState(prev => ({ ...prev, characterState: 'DJING' }))
      } else {
        setShowState(prev => ({ 
          ...prev, 
          characterState: prev.isInteractive ? 'LISTENING' : 'IDLE' 
        }))
      }
    }

    // Subscribe to CantinaOS events
    socket.on('system_mode_change', handleSystemModeChange)
    socket.on('voice_listening_started', handleVoiceListeningStarted)
    socket.on('voice_listening_stopped', handleVoiceListeningStopped)
    socket.on('transcription_final', handleTranscriptionFinal)
    socket.on('speech_synthesis_started', handleSpeechSynthesisStarted)
    socket.on('speech_synthesis_ended', handleSpeechSynthesisEnded)
    socket.on('dj_mode_changed', handleDJModeChanged)

    return () => {
      socket.off('system_mode_change', handleSystemModeChange)
      socket.off('voice_listening_started', handleVoiceListeningStarted)
      socket.off('voice_listening_stopped', handleVoiceListeningStopped)
      socket.off('transcription_final', handleTranscriptionFinal)
      socket.off('speech_synthesis_started', handleSpeechSynthesisStarted)
      socket.off('speech_synthesis_ended', handleSpeechSynthesisEnded)
      socket.off('dj_mode_changed', handleDJModeChanged)
    }
  }, [socket])

  return (
    <div className="h-full w-full bg-black relative overflow-hidden">
      {/* Bold header strip */}
      <div className="absolute top-0 left-0 right-0 h-16 bg-gradient-to-r from-teal-900 to-cyan-800 border-b-2 border-cyan-400 z-50">
        <div className="flex items-center justify-between h-full px-6">
          <div className="flex items-center space-x-4">
            <div className="text-yellow-400 text-lg font-mono font-bold tracking-wider">
              DJ R3X ENTERTAINMENT INTERFACE
            </div>
            <div className="text-cyan-400 text-sm font-mono">
              CANTINA: BATUU-SYS-6630-S
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <div className={`w-3 h-3 rounded-full ${
              systemHealthStatus === 'optimal' ? 'bg-green-400' :
              systemHealthStatus === 'warning' ? 'bg-yellow-400' :
              'bg-red-400'
            } animate-pulse`}></div>
            <SystemStatusIndicators />
          </div>
        </div>
      </div>

      {/* Main content area with authentic Star Wars layout */}
      <div className="h-full pt-16 p-4 bg-gradient-to-br from-slate-900 via-blue-900 to-slate-800">
        
        {/* Top section - Character and Communication */}
        <div className="grid grid-cols-2 gap-6 h-2/3 mb-6">
          
          {/* Left: Voice Pattern Display (inspired by reference) */}
          <div className="bg-gradient-to-br from-slate-800 to-slate-900 border-2 border-cyan-400 relative">
            {/* Corner brackets */}
            <div className="absolute top-0 left-0 w-8 h-8 border-l-4 border-t-4 border-yellow-400"></div>
            <div className="absolute top-0 right-0 w-8 h-8 border-r-4 border-t-4 border-yellow-400"></div>
            <div className="absolute bottom-0 left-0 w-8 h-8 border-l-4 border-b-4 border-yellow-400"></div>
            <div className="absolute bottom-0 right-0 w-8 h-8 border-r-4 border-b-4 border-yellow-400"></div>
            
            {/* Header */}
            <div className="bg-cyan-800 text-yellow-400 font-mono font-bold text-sm px-4 py-2 border-b border-cyan-400">
              VOICE PATTERN • STYLE: DROID
            </div>
            
            {/* Character display */}
            <div className="h-full p-6 flex flex-col">
              <DJCharacterStage characterState={showState.characterState} />
              
              {/* System status at bottom */}
              <div className="mt-auto grid grid-cols-3 gap-4 text-xs font-mono">
                <div className="text-cyan-400">
                  <div>AUDIO INPUT:</div>
                  <div className="text-white">{showState.characterState === 'LISTENING' ? 'ACTIVE' : 'STANDBY'}</div>
                </div>
                <div className="text-cyan-400">
                  <div>PROCESSING:</div>
                  <div className="text-white">{showState.characterState === 'THINKING' ? 'ACTIVE' : 'IDLE'}</div>
                </div>
                <div className="text-cyan-400">
                  <div>AUDIO OUTPUT:</div>
                  <div className="text-white">{showState.characterState === 'SPEAKING' ? 'ACTIVE' : 'STANDBY'}</div>
                </div>
              </div>
            </div>
          </div>

          {/* Right: Communication Log */}
          <div className="bg-gradient-to-br from-slate-800 to-slate-900 border-2 border-cyan-400 relative">
            {/* Corner brackets */}
            <div className="absolute top-0 left-0 w-8 h-8 border-l-4 border-t-4 border-yellow-400"></div>
            <div className="absolute top-0 right-0 w-8 h-8 border-r-4 border-t-4 border-yellow-400"></div>
            <div className="absolute bottom-0 left-0 w-8 h-8 border-l-4 border-b-4 border-yellow-400"></div>
            <div className="absolute bottom-0 right-0 w-8 h-8 border-r-4 border-b-4 border-yellow-400"></div>
            
            {/* Header */}
            <div className="bg-cyan-800 text-yellow-400 font-mono font-bold text-sm px-4 py-2 border-b border-cyan-400">
              COMMUNICATION LOG • CANTINA: BATUU-SYS-6630-S
            </div>
            
            <ConversationDisplay />
          </div>
        </div>

        {/* Bottom section - Performance and Activity */}
        <div className="grid grid-cols-3 gap-6 h-1/3">
          
          {/* Music/Performance Center - spans 2 columns */}
          <div className="col-span-2 bg-gradient-to-br from-slate-800 to-slate-900 border-2 border-cyan-400 relative">
            {/* Corner brackets */}
            <div className="absolute top-0 left-0 w-6 h-6 border-l-4 border-t-4 border-yellow-400"></div>
            <div className="absolute top-0 right-0 w-6 h-6 border-r-4 border-t-4 border-yellow-400"></div>
            <div className="absolute bottom-0 left-0 w-6 h-6 border-l-4 border-b-4 border-yellow-400"></div>
            <div className="absolute bottom-0 right-0 w-6 h-6 border-r-4 border-b-4 border-yellow-400"></div>
            
            {/* Header */}
            <div className="bg-cyan-800 text-yellow-400 font-mono font-bold text-sm px-4 py-2 border-b border-cyan-400">
              ENTERTAINMENT CENTER • NOW SPINNING TRACKS
            </div>
            
            <DJPerformanceCenter />
          </div>

          {/* Activity Feed */}
          <div className="bg-gradient-to-br from-slate-800 to-slate-900 border-2 border-cyan-400 relative">
            {/* Corner brackets */}
            <div className="absolute top-0 left-0 w-6 h-6 border-l-4 border-t-4 border-yellow-400"></div>
            <div className="absolute top-0 right-0 w-6 h-6 border-r-4 border-t-4 border-yellow-400"></div>
            <div className="absolute bottom-0 left-0 w-6 h-6 border-l-4 border-b-4 border-yellow-400"></div>
            <div className="absolute bottom-0 right-0 w-6 h-6 border-r-4 border-b-4 border-yellow-400"></div>
            
            {/* Header */}
            <div className="bg-cyan-800 text-yellow-400 font-mono font-bold text-sm px-4 py-2 border-b border-cyan-400">
              SYSTEM ACTIVITY
            </div>
            
            <LiveActivityFeed />
          </div>
        </div>

        {/* Bottom status bar */}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-r from-teal-900 to-cyan-800 border-t-2 border-cyan-400 text-yellow-400 font-mono text-sm px-6 py-2">
          <div className="flex justify-between">
            <span>FORMER STAR TOURS PILOT - NOW SPINNING TRACKS</span>
            <span>READY TO DROP SOME BEATS</span>
          </div>
        </div>
      </div>

      {/* Enhanced ambient environmental effects */}
      <div className="absolute inset-0 pointer-events-none">
        {/* Worn metal texture background */}
        <div className="absolute inset-0 sw-worn-metal opacity-5"></div>
        
        {/* Enhanced environmental status with complete diegetic integration */}
        <div className="absolute bottom-4 left-4 sw-depth-layer-4 sw-cantina-panel sw-signal-glitch">
          <div className="text-xs sw-color-cantina font-mono sw-terminal-text">
            <div className="flex items-center space-x-4">
              <div className="sw-operational-status">
                <span className="sw-color-targeting">SYSTEM STATUS:</span> {showState.systemMode.toUpperCase()}
              </div>
              <div className="sw-operational-status">
                <span className="sw-color-targeting">DROID STATE:</span> {showState.characterState}
              </div>
              <div className="sw-operational-status">
                <span className="sw-color-targeting">MODE:</span> {showState.isInteractive ? 'INTERACTIVE' : 'ENTERTAINMENT'}
              </div>
            </div>
            <div className="mt-1 text-xs sw-color-cantina/60 sw-system-identifier">
              CANTINA AMBIENCE: ACTIVE • PATRON ENTERTAINMENT: ENABLED • HEALTH: {systemHealthStatus.toUpperCase()}
            </div>
            <div className="mt-1 text-xs sw-color-targeting/40 sw-system-identifier">
              ENTERTAINMENT LICENSE: ACTIVE • GALACTIC ENTERTAINMENT GUILD CERTIFIED
            </div>
          </div>
        </div>

        {/* Enhanced event notification with complete terminal styling */}
        <div className="absolute bottom-4 right-4 sw-depth-layer-4 sw-event-ripple sw-cantina-panel">
          <div className="text-xs sw-color-cantina font-mono sw-terminal-text">
            <div className="flex items-center space-x-2">
              <div className="sw-targeting-reticle w-2 h-2"></div>
              <div className="w-1.5 h-1.5 sw-status-cantina"></div>
              <span className="sw-diegetic-header">ALL SYSTEMS NOMINAL</span>
            </div>
            <div className="text-xs sw-color-targeting/60 mt-1 sw-system-identifier">
              ENTERTAINMENT PROTOCOL: ACTIVE
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}