'use client'

import React, { useState } from 'react'
import { useSpotifyContext } from '../../contexts/SpotifyContext'
import { useSpotifyPlayer } from '../../hooks/useSpotifyPlayer'
import { useSocketContext } from '../../contexts/SocketContext'
import { PlusIcon, PlayIcon } from '@heroicons/react/24/outline'
import { SpotifyTrack } from './SpotifySearch'

export interface SpotifyTrackResultsProps {
  tracks: SpotifyTrack[]
  onTrackSelect?: (track: SpotifyTrack) => void
  onAddToQueue?: (track: SpotifyTrack) => void
  className?: string
}

export const SpotifyTrackResults: React.FC<SpotifyTrackResultsProps> = ({
  tracks,
  onTrackSelect,
  onAddToQueue,
  className = ''
}) => {
  const { authState } = useSpotifyContext()
  const { state, controls } = useSpotifyPlayer()
  const { socket } = useSocketContext()
  const [playingTrack, setPlayingTrack] = useState<string | null>(null)

  const formatDuration = (ms: number): string => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  const getTrackQualityIndicator = (track: SpotifyTrack): string => {
    if (authState.isPremium) {
      return '🎵 Full Song'
    }
    return track.preview_url ? '🔍 Preview' : '🚫 No Preview'
  }

  const handleTrackPlay = async (track: SpotifyTrack) => {
    try {
      setPlayingTrack(track.id)
      
      if (authState.isPremium && state.isReady) {
        // Play full song via Web Playback SDK
        await controls.playTrack(track.uri)
        
        // Also notify bridge/CantinaOS about the track playback
        if (socket) {
          socket.emit('spotify_track_request', {
            track_id: track.id,
            track_uri: track.uri,
            track_name: track.name,
            artist: track.artists.map(a => a.name).join(', '),
            device_id: state.deviceId,
            reason: 'user_selection',
            fallback_enabled: true
          })
        }
      } else if (track.preview_url) {
        // Play 30-second preview for free users
        const audio = new Audio(track.preview_url)
        audio.play()
        
        // Auto-stop after 30 seconds
        setTimeout(() => {
          audio.pause()
          setPlayingTrack(null)
        }, 30000)
        
        // Notify bridge about preview playback
        if (socket) {
          socket.emit('spotify_track_request', {
            track_id: track.id,
            track_uri: track.uri,
            track_name: track.name,
            artist: track.artists.map(a => a.name).join(', '),
            device_id: null,
            reason: 'preview_fallback',
            fallback_enabled: true
          })
        }
      }
      
      onTrackSelect?.(track)
    } catch (error) {
      console.error('Failed to play track:', error)
      setPlayingTrack(null)
    }
  }

  const handleAddToQueue = async (track: SpotifyTrack) => {
    if (authState.isPremium && state.isReady) {
      try {
        await controls.addToQueue(track.uri)
        onAddToQueue?.(track)
      } catch (error) {
        console.error('Failed to add to queue:', error)
      }
    }
  }

  const isCurrentTrack = (track: SpotifyTrack): boolean => {
    return state.currentTrack?.id === track.id
  }

  const isTrackPlaying = (track: SpotifyTrack): boolean => {
    return playingTrack === track.id || (isCurrentTrack(track) && state.isPlaying)
  }

  if (tracks.length === 0) {
    return (
      <div className={`text-center py-8 text-sw-blue-300/50 ${className}`}>
        No tracks found. Try searching for music above.
      </div>
    )
  }

  return (
    <div className={`space-y-2 ${className}`}>
      {tracks.map((track) => (
        <div
          key={track.id}
          className={`
            p-3 rounded-lg border border-sw-blue-600/20
            transition-all duration-200 hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50
            ${isCurrentTrack(track) ? 'bg-sw-blue-600/20 border-sw-blue-500' : 'bg-sw-dark-700/30'}
          `}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3 flex-1 min-w-0">
              {/* Album Art */}
              {track.album?.images?.[2] && (
                <img
                  src={track.album.images[2].url}
                  alt={track.album.name}
                  className="w-12 h-12 rounded shadow-lg flex-shrink-0"
                />
              )}
              
              {/* Track Info */}
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-sw-blue-100 truncate">{track.name}</h4>
                <p className="text-sm text-sw-blue-300 truncate">
                  {track.artists.map(artist => artist.name).join(', ')}
                </p>
                <p className="text-xs text-sw-blue-400 truncate">{track.album?.name}</p>
              </div>
            </div>

            {/* Track Controls and Info */}
            <div className="flex items-center space-x-3 flex-shrink-0">
              <div className="text-right">
                <p className="text-sm text-sw-blue-300">{formatDuration(track.duration_ms)}</p>
                <p className="text-xs text-sw-blue-400">{getTrackQualityIndicator(track)}</p>
                {isTrackPlaying(track) && (
                  <p className="text-xs text-green-400 font-medium">
                    {authState.isPremium ? 'PLAYING' : 'PREVIEW'}
                  </p>
                )}
              </div>

              {/* Play Button */}
              <button
                onClick={() => handleTrackPlay(track)}
                disabled={!authState.isPremium && !track.preview_url}
                className={`
                  p-2 rounded-full transition-colors
                  ${isTrackPlaying(track) 
                    ? 'bg-green-600 text-white' 
                    : 'bg-sw-blue-600 hover:bg-sw-blue-700 text-white disabled:opacity-50 disabled:cursor-not-allowed'
                  }
                `}
                title={
                  !authState.isPremium && !track.preview_url 
                    ? 'No preview available' 
                    : authState.isPremium 
                      ? 'Play full song' 
                      : 'Play 30-second preview'
                }
              >
                <PlayIcon className="h-4 w-4" />
              </button>

              {/* Add to Queue Button (Premium only) */}
              {authState.isPremium && (
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    handleAddToQueue(track)
                  }}
                  disabled={!state.isReady}
                  className="p-2 text-sw-blue-400 hover:text-sw-blue-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  title="Add to Spotify queue"
                >
                  <PlusIcon className="h-4 w-4" />
                </button>
              )}
            </div>
          </div>

          {/* Track Popularity Bar (if available) */}
          {track.popularity && track.popularity > 0 && (
            <div className="mt-2 flex items-center space-x-2">
              <span className="text-xs text-sw-blue-400">Popularity:</span>
              <div className="flex-1 bg-sw-dark-600 rounded-full h-1">
                <div 
                  className="bg-sw-blue-500 h-1 rounded-full"
                  style={{ width: `${track.popularity}%` }}
                />
              </div>
              <span className="text-xs text-sw-blue-400 w-8 text-right">
                {track.popularity}%
              </span>
            </div>
          )}

          {/* Explicit Content Warning */}
          {track.explicit && (
            <div className="mt-1">
              <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-red-900/20 text-red-300 border border-red-600/30">
                EXPLICIT
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}