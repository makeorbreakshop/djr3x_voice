'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '@/contexts/SocketContext'

export type SystemMode = 'IDLE' | 'AMBIENT' | 'INTERACTIVE'

interface SystemModeControlProps {
  currentMode?: SystemMode
  onModeChange?: (mode: SystemMode) => void
  disabled?: boolean
}

interface ModeDefinition {
  id: SystemMode
  name: string
  description: string
  capabilities: string[]
  icon: string
  color: string
  glowColor: string
}

const MODES: ModeDefinition[] = [
  {
    id: 'IDLE',
    name: 'Idle',
    description: 'Low-power listening state',
    capabilities: [
      'Wake word detection',
      'Basic status monitoring',
      'Minimal LED patterns'
    ],
    icon: '🟡',
    color: 'sw-blue-600',
    glowColor: 'sw-blue-400'
  },
  {
    id: 'AMBIENT',
    name: 'Ambient',
    description: 'Background music & voice commands',
    capabilities: [
      'Background music playback',
      'Voice command processing',
      'Eye light animations',
      'Music controls'
    ],
    icon: '🎵',
    color: 'sw-yellow',
    glowColor: 'sw-yellow'
  },
  {
    id: 'INTERACTIVE',
    name: 'Interactive',
    description: 'Full conversation mode',
    capabilities: [
      'Continuous voice monitoring',
      'Full AI conversation',
      'Dynamic music selection',
      'Real-time transcription',
      'Advanced LED patterns'
    ],
    icon: '🤖',
    color: 'sw-green',
    glowColor: 'sw-green'
  }
]

// Helper functions for consistent Tailwind classes
const getActiveClasses = (color: string) => {
  const colorMap: Record<string, string> = {
    'sw-blue-600': 'border-sw-blue-600 bg-sw-blue-600/10',
    'sw-yellow': 'border-sw-yellow bg-sw-yellow/10',
    'sw-green': 'border-sw-green bg-sw-green/10'
  }
  return `${colorMap[color] || 'border-sw-blue-600 bg-sw-blue-600/10'} shadow-lg scale-105 sw-border-glow`
}

const getHoverClasses = (color: string) => {
  const colorMap: Record<string, string> = {
    'sw-blue-600': 'hover:border-sw-blue-600/70 hover:bg-sw-blue-600/5',
    'sw-yellow': 'hover:border-sw-yellow/70 hover:bg-sw-yellow/5',
    'sw-green': 'hover:border-sw-green/70 hover:bg-sw-green/5'
  }
  return `border-sw-blue-600/50 bg-sw-dark-700/30 ${colorMap[color] || 'hover:border-sw-blue-600/70 hover:bg-sw-blue-600/5'} hover:scale-102`
}

const getTextColor = (color: string) => {
  const colorMap: Record<string, string> = {
    'sw-blue-600': 'text-sw-blue-600',
    'sw-yellow': 'text-sw-yellow',
    'sw-green': 'text-sw-green'
  }
  return colorMap[color] || 'text-sw-blue-600'
}

const getBgColor = (color: string) => {
  const colorMap: Record<string, string> = {
    'sw-blue-600': 'bg-sw-blue-600',
    'sw-yellow': 'bg-sw-yellow',
    'sw-green': 'bg-sw-green'
  }
  return colorMap[color] || 'bg-sw-blue-600'
}

export default function SystemModeControl({ 
  currentMode = 'IDLE', 
  onModeChange,
  disabled = false 
}: SystemModeControlProps) {
  const { socket, connected, systemStatus } = useSocketContext()
  const [localMode, setLocalMode] = useState<SystemMode>(currentMode)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const [transitionTarget, setTransitionTarget] = useState<SystemMode | null>(null)

  // Update local mode when prop changes
  useEffect(() => {
    setLocalMode(currentMode)
  }, [currentMode])

  // Listen for mode change events from CantinaOS
  useEffect(() => {
    if (!socket) return

    const handleModeChange = (data: any) => {
      console.log('Mode change event received:', data)
      const newMode = data.current_mode || data.mode || data.new_mode
      if (newMode) {
        const mode = newMode.toUpperCase() as SystemMode
        setLocalMode(mode)
        setIsTransitioning(false)
        setTransitionTarget(null)
      }
    }

    const handleModeTransition = (data: any) => {
      console.log('Mode transition event:', data)
      if (data.status === 'started') {
        setIsTransitioning(true)
        setTransitionTarget(data.new_mode?.toUpperCase() as SystemMode)
      } else if (data.status === 'completed' || data.status === 'failed') {
        setIsTransitioning(false)
        setTransitionTarget(null)
        if (data.status === 'completed' && data.new_mode) {
          setLocalMode(data.new_mode.toUpperCase() as SystemMode)
        }
      }
    }

    socket.on('system_mode_change', handleModeChange)
    socket.on('mode_transition', handleModeTransition)

    return () => {
      socket.off('system_mode_change', handleModeChange)
      socket.off('mode_transition', handleModeTransition)
    }
  }, [socket])

  const handleModeSelect = async (targetMode: SystemMode) => {
    if (disabled || !connected || isTransitioning || targetMode === localMode) {
      return
    }

    setIsTransitioning(true)
    setTransitionTarget(targetMode)

    // Send mode change request via socket
    if (socket) {
      socket.emit('system_command', {
        action: 'set_mode',
        mode: targetMode.toLowerCase()
      })
    }

    // Call parent handler if provided
    if (onModeChange) {
      onModeChange(targetMode)
    }

    // Timeout fallback in case we don't receive confirmation
    const timeoutId = setTimeout(() => {
      setIsTransitioning(false)
      setTransitionTarget(null)
      // Force update to target mode if transition takes too long
      setLocalMode(targetMode)
    }, 3000)

    // Store timeout ID for cleanup
    return () => clearTimeout(timeoutId)
  }

  const getModePosition = (mode: SystemMode): number => {
    return MODES.findIndex(m => m.id === mode)
  }

  const canTransitionTo = (targetMode: SystemMode): boolean => {
    if (!connected || disabled || isTransitioning) return false
    
    const currentPos = getModePosition(localMode)
    const targetPos = getModePosition(targetMode)
    
    // Allow transitions to adjacent modes or same mode
    return Math.abs(currentPos - targetPos) <= 1
  }

  const getServiceImpact = (mode: SystemMode) => {
    const baseServices = ['CantinaOS Core', 'Event Bus']
    
    switch (mode) {
      case 'IDLE':
        return [...baseServices, 'Mode Manager']
      case 'AMBIENT':
        return [...baseServices, 'Mode Manager', 'Music Controller', 'Eye Controller']
      case 'INTERACTIVE':
        return [...baseServices, 'Mode Manager', 'Music Controller', 'Eye Controller', 'Deepgram', 'GPT Service', 'ElevenLabs']
      default:
        return baseServices
    }
  }

  const currentModeData = MODES.find(m => m.id === localMode)
  const transitionModeData = transitionTarget ? MODES.find(m => m.id === transitionTarget) : null

  return (
    <div className="sw-panel">
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
          SYSTEM MODE CONTROL
        </h3>
        <div className="flex items-center space-x-2">
          <div className={`w-2 h-2 rounded-full ${connected ? 'sw-status-online animate-pulse' : 'sw-status-offline'}`}></div>
          <span className="text-xs text-sw-blue-300">
            {connected ? 'CantinaOS Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      {/* Current Mode Display */}
      <div className="mb-8">
        <div className="text-center">
          <div className="text-sm text-sw-blue-300 mb-2 uppercase tracking-wide">Current System Mode</div>
          {isTransitioning ? (
            <div className="space-y-3">
              <div className="text-2xl font-bold text-sw-yellow animate-pulse">
                TRANSITIONING...
              </div>
              <div className="text-sm text-sw-blue-300">
                {localMode} → {transitionTarget}
              </div>
              <div className="w-full bg-sw-dark-700 rounded-full h-2">
                <div className="bg-sw-yellow h-2 rounded-full animate-pulse" style={{ width: '60%' }}></div>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <div className={`text-3xl font-bold ${currentModeData ? getTextColor(currentModeData.color) : 'text-sw-blue-100'} sw-text-glow`}>
                {currentModeData?.icon} {currentModeData?.name.toUpperCase()}
              </div>
              <div className="text-sm text-sw-blue-300">
                {currentModeData?.description}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Mode Selection Buttons */}
      <div className="mb-8">
        <div className="text-sm text-sw-blue-300 mb-4 text-center">Select Target Mode</div>
        <div className="flex items-center justify-center space-x-4">
          {MODES.map((mode, index) => {
            const isActive = mode.id === localMode
            const isTarget = mode.id === transitionTarget
            const canTransition = canTransitionTo(mode.id)
            const isDisabled = disabled || !connected || (isTransitioning && !isTarget)

            return (
              <div key={mode.id} className="flex flex-col items-center">
                {/* Connection line to next mode */}
                {index < MODES.length - 1 && (
                  <div className="absolute w-12 h-0.5 bg-sw-blue-600/30 mt-16 ml-20"></div>
                )}
                
                <button
                  onClick={() => handleModeSelect(mode.id)}
                  disabled={isDisabled}
                  className={`
                    relative w-32 h-32 rounded-xl transition-all duration-300 transform
                    border-2 flex flex-col items-center justify-center p-4
                    ${isActive
                      ? getActiveClasses(mode.color)
                      : isTarget
                      ? 'border-sw-yellow bg-sw-yellow/10 animate-pulse'
                      : canTransition
                      ? getHoverClasses(mode.color)
                      : 'border-sw-dark-600 bg-sw-dark-800/30 opacity-50 cursor-not-allowed'
                    }
                  `}
                >
                  <div className="text-2xl mb-2">{mode.icon}</div>
                  <div className={`text-sm font-semibold ${
                    isActive ? getTextColor(mode.color) : 
                    isTarget ? 'text-sw-yellow' :
                    canTransition ? 'text-sw-blue-200' : 'text-sw-blue-400'
                  }`}>
                    {mode.name}
                  </div>
                  <div className="text-xs text-sw-blue-400 text-center mt-1">
                    {mode.description}
                  </div>
                  
                  {/* Active indicator */}
                  {isActive && (
                    <div className={`absolute -top-1 -right-1 w-4 h-4 rounded-full ${getBgColor(mode.color)} animate-pulse`}></div>
                  )}
                  
                  {/* Transition indicator */}
                  {isTarget && (
                    <div className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-sw-yellow animate-spin border-2 border-sw-dark-900"></div>
                  )}
                </button>
                
                <div className="text-xs text-sw-blue-400 mt-2 text-center">
                  {mode.name}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Mode Capabilities */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
          <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">
            Current Capabilities
          </h4>
          <div className="space-y-2">
            {currentModeData?.capabilities.map((capability, index) => (
              <div key={index} className="flex items-center space-x-2">
                <div className={`w-2 h-2 rounded-full ${getBgColor(currentModeData.color)}`}></div>
                <span className="text-xs text-sw-blue-300">{capability}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
          <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">
            Active Services
          </h4>
          <div className="space-y-2">
            {getServiceImpact(localMode).map((service, index) => (
              <div key={index} className="flex items-center justify-between">
                <span className="text-xs text-sw-blue-300">{service}</span>
                <div className="w-2 h-2 rounded-full sw-status-online"></div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Mode Transition Help */}
      {!connected && (
        <div className="mt-6 p-3 bg-sw-red/10 border border-sw-red/30 rounded-lg">
          <div className="flex items-center space-x-2">
            <div className="w-2 h-2 rounded-full bg-sw-red animate-pulse"></div>
            <span className="text-sm text-sw-red">CantinaOS connection required for mode changes</span>
          </div>
        </div>
      )}

      {isTransitioning && (
        <div className="mt-6 p-3 bg-sw-yellow/10 border border-sw-yellow/30 rounded-lg">
          <div className="flex items-center space-x-2">
            <div className="w-2 h-2 rounded-full bg-sw-yellow animate-pulse"></div>
            <span className="text-sm text-sw-yellow">
              Mode transition in progress... Please wait
            </span>
          </div>
        </div>
      )}
    </div>
  )
}