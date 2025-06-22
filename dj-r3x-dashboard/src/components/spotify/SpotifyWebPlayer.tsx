'use client'

import React, { useEffect, useState } from 'react'
import { useSpotifyPlayer } from '@/hooks/useSpotifyPlayer'
import { useSpotifyContext } from '@/contexts/SpotifyContext'
import { 
  PlayIcon, 
  PauseIcon, 
  ForwardIcon, 
  BackwardIcon,
  SpeakerWaveIcon,
  SpeakerXMarkIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon
} from '@heroicons/react/24/solid'

export interface SpotifyWebPlayerProps {
  className?: string
  showAuth?: boolean
  showDeviceActivation?: boolean
}

export const SpotifyWebPlayer: React.FC<SpotifyWebPlayerProps> = ({
  className = '',
  showAuth = true,
  showDeviceActivation = true
}) => {
  const { authState, authenticate, logout } = useSpotifyContext()
  const { player, state, controls, isSDKReady, initializePlayer, disconnectPlayer } = useSpotifyPlayer()
  const [showVolumeSlider, setShowVolumeSlider] = useState(false)

  // Format time in MM:SS format
  const formatTime = (ms: number): string => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  // Calculate progress percentage
  const getProgressPercentage = (): number => {
    if (state.duration === 0) return 0
    return (state.position / state.duration) * 100
  }

  // Handle seek by clicking on progress bar
  const handleSeek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (state.duration === 0) return
    
    const rect = e.currentTarget.getBoundingClientRect()
    const clickX = e.clientX - rect.left
    const percentage = clickX / rect.width
    const newPosition = Math.floor(percentage * state.duration)
    
    controls.seek(newPosition).catch(console.error)
  }

  // Handle volume change
  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVolume = parseFloat(e.target.value)
    controls.setVolume(newVolume).catch(console.error)
  }

  // Authentication section
  if (showAuth && !authState.isAuthenticated) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <h3 className="text-lg font-semibold text-sw-blue-200 mb-4">
            Spotify Authentication Required
          </h3>
          
          {authState.isLoading ? (
            <div className="flex items-center justify-center space-x-2">
              <ArrowPathIcon className="h-5 w-5 text-sw-blue-400 animate-spin" />
              <span className="text-sw-blue-300">Connecting to Spotify...</span>
            </div>
          ) : (
            <>
              <p className="text-sw-blue-300 mb-4">
                Connect your Spotify Premium account to enable Web Playback
              </p>
              
              {authState.error && (
                <div className="bg-red-900/20 border border-red-600/30 rounded-lg p-3 mb-4">
                  <div className="flex items-center space-x-2">
                    <ExclamationTriangleIcon className="h-5 w-5 text-red-400" />
                    <span className="text-red-300 text-sm">{authState.error}</span>
                  </div>
                </div>
              )}
              
              <button
                onClick={authenticate}
                disabled={authState.isLoading}
                className="bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium py-2 px-6 rounded-lg transition-colors"
              >
                Connect Spotify
              </button>
            </>
          )}
        </div>
      </div>
    )
  }

  // Premium requirement check
  if (authState.isAuthenticated && !authState.isPremium) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <h3 className="text-lg font-semibold text-yellow-400 mb-4">
            Spotify Premium Required
          </h3>
          <p className="text-sw-blue-300 mb-4">
            Web Playback SDK requires a Spotify Premium subscription
          </p>
          <div className="flex justify-center space-x-4">
            <a
              href="https://www.spotify.com/premium/"
              target="_blank"
              rel="noopener noreferrer"
              className="bg-green-600 hover:bg-green-700 text-white font-medium py-2 px-4 rounded-lg transition-colors"
            >
              Upgrade to Premium
            </a>
            <button
              onClick={logout}
              className="bg-sw-dark-600 hover:bg-sw-dark-700 text-sw-blue-300 font-medium py-2 px-4 rounded-lg transition-colors"
            >
              Disconnect
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Device activation section
  if (showDeviceActivation && state.isReady && !state.isActive) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <h3 className="text-lg font-semibent text-sw-blue-200 mb-4">
            Activate DJ R3X Web Player
          </h3>
          <p className="text-sw-blue-300 mb-4">
            Click below to make this your active Spotify device
          </p>
          <button
            onClick={() => controls.activateDevice().catch(console.error)}
            className="bg-sw-blue-600 hover:bg-sw-blue-700 text-white font-medium py-2 px-6 rounded-lg transition-colors"
          >
            Activate Device
          </button>
        </div>
      </div>
    )
  }

  // Loading state
  if (state.isLoading || authState.isLoading || !isSDKReady) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <ArrowPathIcon className="h-8 w-8 text-sw-blue-400 animate-spin mx-auto mb-4" />
          <p className="text-sw-blue-300">
            {!isSDKReady ? 'Loading Spotify SDK...' : 'Initializing player...'}
          </p>
        </div>
      </div>
    )
  }

  // Error state
  if (state.error) {
    return (
      <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
        <div className="text-center">
          <ExclamationTriangleIcon className="h-8 w-8 text-red-400 mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-red-400 mb-2">Player Error</h3>
          <p className="text-red-300 mb-4">{state.error}</p>
          <div className="flex justify-center space-x-4">
            <button
              onClick={() => initializePlayer().catch(console.error)}
              className="bg-sw-blue-600 hover:bg-sw-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors"
            >
              Retry
            </button>
            <button
              onClick={disconnectPlayer}
              className="bg-sw-dark-600 hover:bg-sw-dark-700 text-sw-blue-300 font-medium py-2 px-4 rounded-lg transition-colors"
            >
              Reset
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Main player interface
  return (
    <div className={`bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg p-6 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-lg font-semibold text-sw-blue-200">
          DJ R3X Web Player
        </h3>
        <div className="flex items-center space-x-2">
          <div className={`w-2 h-2 rounded-full ${state.isReady ? 'bg-green-400' : 'bg-red-400'}`} />
          <span className="text-sm text-sw-blue-300">
            {state.isReady ? 'Ready' : 'Not Ready'}
          </span>
        </div>
      </div>

      {/* Current Track Info */}
      {state.currentTrack && (
        <div className="mb-6">
          <div className="flex items-center space-x-4">
            {state.currentTrack.album?.images?.[0] && (
              <img
                src={state.currentTrack.album.images[0].url}
                alt={state.currentTrack.album.name}
                className="w-16 h-16 rounded-lg shadow-lg"
              />
            )}
            <div className="flex-1 min-w-0">
              <h4 className="text-lg font-medium text-sw-blue-100 truncate">
                {state.currentTrack.name}
              </h4>
              <p className="text-sm text-sw-blue-300 truncate">
                {state.currentTrack.artists.map((artist: any) => artist.name).join(', ')}
              </p>
              {state.currentTrack.album && (
                <p className="text-xs text-sw-blue-400 truncate">
                  {state.currentTrack.album.name}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Progress Bar */}
      <div className="mb-4">
        <div className="flex items-center space-x-2 mb-2">
          <span className="text-xs text-sw-blue-400">
            {formatTime(state.position)}
          </span>
          <div 
            className="flex-1 h-2 bg-sw-dark-600 rounded-full cursor-pointer"
            onClick={handleSeek}
          >
            <div 
              className="h-full bg-sw-blue-500 rounded-full transition-all duration-300"
              style={{ width: `${getProgressPercentage()}%` }}
            />
          </div>
          <span className="text-xs text-sw-blue-400">
            {formatTime(state.duration)}
          </span>
        </div>
      </div>

      {/* Playback Controls */}
      <div className="flex items-center justify-center space-x-4 mb-4">
        <button
          onClick={() => controls.skipToPrevious().catch(console.error)}
          disabled={!state.isReady}
          className="p-2 text-sw-blue-300 hover:text-sw-blue-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          title="Previous Track"
        >
          <BackwardIcon className="h-6 w-6" />
        </button>

        <button
          onClick={() => controls.togglePlayPause().catch(console.error)}
          disabled={!state.isReady}
          className="p-3 bg-sw-blue-600 hover:bg-sw-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-full transition-colors"
          title={state.isPlaying ? 'Pause' : 'Play'}
        >
          {state.isPlaying ? (
            <PauseIcon className="h-6 w-6" />
          ) : (
            <PlayIcon className="h-6 w-6" />
          )}
        </button>

        <button
          onClick={() => controls.skipToNext().catch(console.error)}
          disabled={!state.isReady}
          className="p-2 text-sw-blue-300 hover:text-sw-blue-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          title="Next Track"
        >
          <ForwardIcon className="h-6 w-6" />
        </button>
      </div>

      {/* Volume Control */}
      <div className="flex items-center justify-center space-x-2">
        <button
          onClick={() => setShowVolumeSlider(!showVolumeSlider)}
          className="p-1 text-sw-blue-300 hover:text-sw-blue-100 transition-colors"
          title={`Volume: ${Math.round(state.volume * 100)}%`}
        >
          {state.volume === 0 ? (
            <SpeakerXMarkIcon className="h-5 w-5" />
          ) : (
            <SpeakerWaveIcon className="h-5 w-5" />
          )}
        </button>
        
        {showVolumeSlider && (
          <div className="flex items-center space-x-2">
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={state.volume}
              onChange={handleVolumeChange}
              className="w-20 h-1 bg-sw-dark-600 rounded-lg appearance-none cursor-pointer"
            />
            <span className="text-xs text-sw-blue-400 w-8 text-right">
              {Math.round(state.volume * 100)}%
            </span>
          </div>
        )}
      </div>

      {/* Status Information */}
      {state.isBuffering && (
        <div className="mt-4 flex items-center justify-center space-x-2">
          <ArrowPathIcon className="h-4 w-4 text-sw-blue-400 animate-spin" />
          <span className="text-sm text-sw-blue-300">Buffering...</span>
        </div>
      )}

      {/* Device Information */}
      <div className="mt-4 pt-4 border-t border-sw-blue-600/20">
        <div className="flex items-center justify-between text-xs text-sw-blue-400">
          <span>Device: {state.deviceId ? `DJ R3X (${state.deviceId.slice(0, 8)}...)` : 'Unknown'}</span>
          <span>Status: {state.isActive ? 'Active' : 'Inactive'}</span>
        </div>
      </div>
    </div>
  )
}