'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'

interface MusicTrack {
  title: string
  artist: string
  duration?: string
  progress?: number
  volume?: number
}

interface DJCommentary {
  text: string
  timestamp: Date
  type: 'track_intro' | 'transition' | 'general'
}

export default function DJPerformanceCenter() {
  const { socket } = useSocketContext()
  const [currentTrack, setCurrentTrack] = useState<MusicTrack | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isDucking, setIsDucking] = useState(false)
  const [queue, setQueue] = useState<MusicTrack[]>([])
  const [commentary, setCommentary] = useState<DJCommentary[]>([])
  const [djModeActive, setDjModeActive] = useState(false)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const [transitionProgress, setTransitionProgress] = useState(0)
  const [nextTrackCountdown, setNextTrackCountdown] = useState<number | null>(null)
  const [isClient, setIsClient] = useState(false)

  // Client-side hydration guard
  useEffect(() => {
    setIsClient(true)
  }, [])

  // CantinaOS event integration
  useEffect(() => {
    if (!socket) return

    const handleMusicPlaybackStarted = (data: any) => {
      const track: MusicTrack = {
        title: data.title || data.track_name || 'Unknown Track',
        artist: data.artist || 'Unknown Artist',
        duration: data.duration || '0:00',
        progress: 0,
        volume: data.volume || 100
      }
      
      setCurrentTrack(track)
      setIsPlaying(true)
      setIsDucking(false)
    }

    const handleMusicPlaybackStopped = () => {
      setIsPlaying(false)
      setCurrentTrack(null)
    }

    const handleMusicVolumeChanged = (data: any) => {
      const newVolume = data.volume || data.level || 50
      setIsDucking(newVolume < 80) // Consider ducking if volume below 80%
      
      if (currentTrack) {
        setCurrentTrack(prev => prev ? { ...prev, volume: newVolume } : null)
      }
    }

    const handleDJModeChanged = (data: any) => {
      setDjModeActive(data.is_active || data.dj_mode_active || false)
    }

    const handleCrossfadeStarted = (data: any) => {
      // Add transition commentary
      const newCommentary: DJCommentary = {
        text: `Transitioning to next track... Smooth crossfade initiated.`,
        timestamp: new Date(),
        type: 'transition'
      }
      
      setCommentary(prev => [newCommentary, ...prev.slice(0, 4)]) // Keep last 5
    }

    const handleLLMResponse = (data: any) => {
      // Check if this is DJ commentary (music-related response)
      const responseText = data.response || data.text || data.content || ''
      if (responseText && responseText.toLowerCase().includes('music')) {
        const newCommentary: DJCommentary = {
          text: responseText,
          timestamp: new Date(),
          type: 'general'
        }
        
        setCommentary(prev => [newCommentary, ...prev.slice(0, 4)])
      }
    }

    // Subscribe to events
    socket.on('music_playback_started', handleMusicPlaybackStarted)
    socket.on('music_playback_stopped', handleMusicPlaybackStopped)
    socket.on('music_volume_changed', handleMusicVolumeChanged)
    socket.on('dj_mode_changed', handleDJModeChanged)
    socket.on('crossfade_started', handleCrossfadeStarted)
    socket.on('llm_response', handleLLMResponse)

    return () => {
      socket.off('music_playback_started', handleMusicPlaybackStarted)
      socket.off('music_playback_stopped', handleMusicPlaybackStopped)
      socket.off('music_volume_changed', handleMusicVolumeChanged)
      socket.off('dj_mode_changed', handleDJModeChanged)
      socket.off('crossfade_started', handleCrossfadeStarted)
      socket.off('llm_response', handleLLMResponse)
    }
  }, [socket])

  return (
    <div className="h-full w-full flex bg-transparent">
      <div className="flex-1 p-4 grid grid-cols-2 gap-6">
        
        {/* Now Playing Display */}
        <div className="bg-slate-700 border border-cyan-600 p-4">
          <div className="text-cyan-400 font-mono font-bold text-sm mb-4">NOW PLAYING</div>
          
          {currentTrack ? (
            <div className="space-y-3">
              {/* Album art placeholder with holographic frame */}
              <div className="relative">
                <div className="w-32 h-32 bg-sw-dark-700 rounded-lg border-2 border-sw-green/50 flex items-center justify-center mx-auto relative overflow-hidden">
                  <div className="text-4xl">🎵</div>
                  
                  {/* Holographic glow effect */}
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-sw-green/10 to-transparent animate-pulse"></div>
                  
                  {/* Corner details */}
                  <div className="absolute top-1 left-1 w-2 h-2 border-l border-t border-sw-green/80"></div>
                  <div className="absolute top-1 right-1 w-2 h-2 border-r border-t border-sw-green/80"></div>
                  <div className="absolute bottom-1 left-1 w-2 h-2 border-l border-b border-sw-green/80"></div>
                  <div className="absolute bottom-1 right-1 w-2 h-2 border-r border-b border-sw-green/80"></div>
                </div>
                
                {/* Volume/ducking indicator */}
                {isDucking && (
                  <div className="absolute -top-2 -right-2 bg-sw-yellow text-black text-xs px-2 py-1 rounded font-mono">
                    DUCKED
                  </div>
                )}
              </div>

              {/* Track info */}
              <div className="text-center space-y-1">
                <h4 className="text-base font-mono text-sw-blue-100 font-bold">
                  {currentTrack.title}
                </h4>
                <p className="text-sm text-sw-blue-300">
                  {currentTrack.artist}
                </p>
                <p className="text-xs text-sw-blue-400 font-mono">
                  {currentTrack.duration} • VOL: {currentTrack.volume || 100}%
                </p>
              </div>

              {/* Progress bar */}
              <div className="space-y-1">
                <div className="w-full bg-sw-dark-700 rounded-full h-2 border border-sw-blue-600/30">
                  <div 
                    className="bg-gradient-to-r from-sw-green to-sw-blue-400 h-full rounded-full transition-all duration-1000"
                    style={{ width: isClient ? `${currentTrack.progress || 0}%` : '0%' }}
                  ></div>
                </div>
                <div className="flex justify-between text-xs text-sw-blue-400 font-mono">
                  <span>PLAYING</span>
                  <span>LOOP: INFINITE</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-sw-blue-400/50">
              <div className="text-4xl mb-3">🎼</div>
              <p className="text-sm font-mono">NO TRACK SELECTED</p>
              <p className="text-xs mt-2 opacity-60">
                Waiting for music playback to begin...
              </p>
            </div>
          )}
        </div>

        {/* DJ Commentary Stream */}
        <div className="space-y-4">
          <h3 className="text-xs font-mono text-sw-blue-300 tracking-wider">DJ COMMENTARY STREAM</h3>
          
          <div className="space-y-3 max-h-48 overflow-y-auto scrollbar-thin scrollbar-track-sw-dark-800 scrollbar-thumb-sw-blue-600/50">
            {commentary.length > 0 ? (
              commentary.map((comment, index) => (
                <div 
                  key={index}
                  className="bg-sw-dark-700/40 border border-sw-green/20 rounded-lg p-3"
                >
                  <div className="flex items-center space-x-2 mb-2">
                    <div className={`w-1.5 h-1.5 rounded-full ${
                      comment.type === 'transition' ? 'bg-sw-yellow' :
                      comment.type === 'track_intro' ? 'bg-sw-green' :
                      'bg-sw-blue-400'
                    }`}></div>
                    <span className="text-xs text-sw-blue-400 font-mono">
                      {comment.timestamp.toLocaleTimeString()}
                    </span>
                    <span className="text-xs text-sw-green font-mono uppercase">
                      {comment.type.replace('_', ' ')}
                    </span>
                  </div>
                  <p className="text-sm text-sw-blue-100 font-mono leading-relaxed">
                    {comment.text}
                  </p>
                </div>
              ))
            ) : (
              <div className="text-center py-6 text-sw-blue-400/50">
                <div className="text-3xl mb-3">💭</div>
                <p className="text-sm font-mono">COMMENTARY STANDBY</p>
                <p className="text-xs mt-2 opacity-60">
                  DJ commentary will appear here during performance
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Queue Preview & Transition Info */}
        <div className="space-y-4 col-span-1">
          <h3 className="text-xs font-mono text-sw-blue-300 tracking-wider">COMING UP NEXT</h3>
          
          {/* Transition Status */}
          {isTransitioning && (
            <div className="bg-sw-yellow/10 border border-sw-yellow/30 rounded-lg p-3 mb-4">
              <div className="flex items-center space-x-2 mb-2">
                <div className="w-2 h-2 bg-sw-yellow rounded-full animate-pulse"></div>
                <span className="text-xs text-sw-yellow font-mono font-bold">CROSSFADE ACTIVE</span>
              </div>
              <div className="w-full bg-sw-dark-700 rounded-full h-2 border border-sw-yellow/30">
                <div 
                  className="bg-gradient-to-r from-sw-yellow to-sw-green h-full rounded-full transition-all duration-100"
                  style={{ width: isClient ? `${transitionProgress}%` : '0%' }}
                ></div>
              </div>
              <div className="text-xs text-sw-yellow/80 font-mono mt-1">
                MIXING: {transitionProgress.toFixed(0)}%
              </div>
            </div>
          )}
          
          {/* Auto DJ Countdown */}
          {djModeActive && nextTrackCountdown !== null && (
            <div className="bg-sw-green/10 border border-sw-green/30 rounded-lg p-3 mb-4">
              <div className="flex items-center space-x-2 mb-2">
                <div className="w-2 h-2 bg-sw-green rounded-full animate-pulse"></div>
                <span className="text-xs text-sw-green font-mono font-bold">AUTO-TRANSITION</span>
              </div>
              <div className="text-center">
                <div className="text-2xl font-mono text-sw-green font-bold">
                  {nextTrackCountdown}s
                </div>
                <div className="text-xs text-sw-green/80 font-mono">
                  UNTIL NEXT TRACK
                </div>
              </div>
            </div>
          )}
          
          {/* Queue Display */}
          <div className="space-y-2 max-h-48 overflow-y-auto scrollbar-thin scrollbar-track-sw-dark-800 scrollbar-thumb-sw-blue-600/50">
            {queue.length > 0 ? (
              queue.slice(0, 5).map((track, index) => (
                <div 
                  key={index}
                  className={`
                    bg-sw-dark-700/40 border rounded-lg p-3 transition-all duration-300
                    ${index === 0 
                      ? 'border-sw-green/50 bg-sw-green/5' 
                      : 'border-sw-blue-600/20'
                    }
                  `}
                >
                  <div className="flex items-center space-x-3">
                    <div className={`
                      w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold
                      ${index === 0 
                        ? 'bg-sw-green/20 text-sw-green border border-sw-green/50' 
                        : 'bg-sw-blue-600/20 text-sw-blue-400 border border-sw-blue-600/50'
                      }
                    `}>
                      {index === 0 ? '▶' : index + 1}
                    </div>
                    
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-mono text-sw-blue-100 truncate">
                        {track.title}
                      </div>
                      <div className="text-xs text-sw-blue-300 truncate">
                        {track.artist}
                      </div>
                      <div className="text-xs text-sw-blue-400/60 font-mono">
                        {track.duration}
                      </div>
                    </div>
                  </div>
                  
                  {index === 0 && (
                    <div className="mt-2 text-xs text-sw-green font-mono animate-pulse">
                      NEXT UP
                    </div>
                  )}
                </div>
              ))
            ) : (
              <div className="text-center py-6 text-sw-blue-400/50">
                <div className="text-3xl mb-3">📋</div>
                <p className="text-sm font-mono">QUEUE EMPTY</p>
                <p className="text-xs mt-2 opacity-60">
                  No tracks queued for playback
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Bottom status bar */}
      <div className="absolute bottom-0 left-0 right-0 bg-sw-dark-700/80 border-t border-sw-blue-600/30 px-4 py-2">
        <div className="flex items-center justify-between text-xs font-mono">
          <span className="text-sw-blue-400">
            PLAYLIST: CANTINA CLASSICS
          </span>
          <span className="text-sw-blue-400">
            GENRE: JIZZ & SWING
          </span>
          <span className="text-sw-blue-400">
            AUDIENCE RATING: {isPlaying ? '⭐⭐⭐⭐⭐' : '---'}
          </span>
        </div>
      </div>
    </div>
  )
}