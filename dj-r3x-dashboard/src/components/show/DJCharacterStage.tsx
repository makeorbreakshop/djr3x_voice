'use client'

import { useState, useEffect } from 'react'

interface DJCharacterStageProps {
  characterState: 'IDLE' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'DJING'
}

export default function DJCharacterStage({ characterState }: DJCharacterStageProps) {
  const [animationKey, setAnimationKey] = useState(0)
  const [isClient, setIsClient] = useState(false)
  const [waveformHeights, setWaveformHeights] = useState<number[]>([])
  const [waveformAnimations, setWaveformAnimations] = useState<number[]>([])

  // Client-side hydration guard and waveform initialization
  useEffect(() => {
    setIsClient(true)
    // Generate stable random values for waveform on client side only
    const heights = Array.from({ length: 32 }, () => Math.random() * 100)
    const animations = Array.from({ length: 32 }, () => 0.5 + Math.random() * 0.5)
    setWaveformHeights(heights)
    setWaveformAnimations(animations)
  }, [])

  // Trigger animation refresh on state change
  useEffect(() => {
    setAnimationKey(prev => prev + 1)
    // Regenerate waveform on state change (client side only)
    if (isClient) {
      const heights = Array.from({ length: 32 }, () => Math.random() * 100)
      const animations = Array.from({ length: 32 }, () => 0.5 + Math.random() * 0.5)
      setWaveformHeights(heights)
      setWaveformAnimations(animations)
    }
  }, [characterState, isClient])

  const getCharacterStyles = () => {
    switch (characterState) {
      case 'IDLE':
        return {
          glow: 'animate-idle-glow sw-holo-flicker',
          animation: 'animate-pulse-subtle',
          color: 'sw-color-cantina',
          borderColor: 'border-sw-blue-300',
          background: 'sw-cantina-panel'
        }
      case 'LISTENING':
        return {
          glow: 'shadow-lg shadow-sw-green/40 sw-radar-sweep',
          animation: '',
          color: 'sw-color-cantina',
          borderColor: 'border-sw-green',
          background: 'sw-cantina-panel'
        }
      case 'THINKING':
        return {
          glow: 'shadow-lg shadow-sw-yellow/40 sw-signal-glitch',
          animation: 'animate-pulse',
          color: 'sw-color-targeting',
          borderColor: 'border-sw-yellow',
          background: 'sw-cantina-panel'
        }
      case 'SPEAKING':
        return {
          glow: 'animate-speaking-pulse sw-event-ripple',
          animation: '',
          color: 'sw-color-cantina',
          borderColor: 'border-sw-blue-400',
          background: 'sw-cantina-panel'
        }
      case 'DJING':
        return {
          glow: 'shadow-lg shadow-sw-green/60 sw-radar-sweep',
          animation: 'animate-pulse',
          color: 'sw-color-cantina',
          borderColor: 'border-sw-green',
          background: 'sw-cantina-panel'
        }
      default:
        return {
          glow: 'animate-idle-glow sw-holo-flicker',
          animation: 'animate-pulse-subtle',
          color: 'sw-color-cantina',
          borderColor: 'border-sw-blue-300',
          background: 'sw-cantina-panel'
        }
    }
  }

  const styles = getCharacterStyles()

  return (
    <div className="h-full w-full flex flex-col">
      {/* Main character display inspired by voice pattern reference */}
      <div className="flex-1 bg-slate-800 border border-cyan-600 relative p-8">
        {/* Grid background like in reference */}
        <div className="absolute inset-4 opacity-20">
          <div className="w-full h-full" style={{
            backgroundImage: `
              linear-gradient(cyan 1px, transparent 1px),
              linear-gradient(90deg, cyan 1px, transparent 1px)
            `,
            backgroundSize: '20px 20px'
          }}></div>
        </div>

        {/* Character in center with state display */}
        <div className="relative z-10 h-full flex flex-col items-center justify-center">
          {/* Large DJ R3X character */}
          <div className="text-8xl mb-4">🤖</div>
          
          {/* State indicator */}
          <div className="bg-cyan-800 text-yellow-400 px-6 py-2 font-mono font-bold tracking-wider border border-cyan-400">
            DJ R3X • {characterState}
          </div>
          
          {/* RX-24 identifier */}
          <div className="text-cyan-400 font-mono text-sm mt-2">
            RX-24 ENTERTAINMENT UNIT
          </div>

          {/* Waveform display when active */}
          {(characterState === 'LISTENING' || characterState === 'SPEAKING') && (
            <div className="absolute bottom-16 left-1/2 transform -translate-x-1/2 w-80 h-16">
              <div className="bg-slate-900 border border-cyan-400 h-full relative">
                {/* Animated waveform */}
                <div className="absolute bottom-0 left-0 right-0 h-1 bg-cyan-400"></div>
                <div className="absolute bottom-0 left-1/2 transform -translate-x-1/2 w-64 h-8">
                  <div className="w-full h-full flex items-end justify-center space-x-1">
                    {[...Array(32)].map((_, i) => (
                      <div
                        key={i}
                        className="bg-cyan-400 w-1"
                        style={{
                          height: isClient && waveformHeights[i] !== undefined ? `${waveformHeights[i]}%` : '50%',
                          animation: isClient && waveformAnimations[i] !== undefined ? `pulse ${waveformAnimations[i]}s ease-in-out infinite` : 'pulse 0.75s ease-in-out infinite'
                        }}
                      ></div>
                    ))}
                  </div>
                </div>
              </div>
              
              {/* Status indicator */}
              <div className="text-center text-cyan-400 font-mono text-xs mt-2">
                {characterState.toUpperCase()}
              </div>
            </div>
          )}

          {/* Corner status readouts when thinking */}
          {characterState === 'THINKING' && (
            <>
              <div className="absolute top-4 left-4 text-yellow-400 font-mono text-xs">
                PROCESSING...
              </div>
              <div className="absolute top-4 right-4 text-cyan-400 font-mono text-xs">
                NEURAL ACTIVE
              </div>
            </>
          )}
        </div>

        {/* Corner status indicator like in reference */}
        <div className="absolute bottom-4 right-4 text-cyan-400 font-mono text-xs">
          {characterState}
        </div>
      </div>
    </div>
  )
}