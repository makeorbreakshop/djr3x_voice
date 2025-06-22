'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useSpotifyContext } from '@/contexts/SpotifyContext'
import { useSocketContext } from '@/contexts/SocketContext'
import { SpotifyTrack } from '@/components/spotify/SpotifySearch'

export interface PlayerState {
  // Web Playback SDK state
  isReady: boolean
  deviceId: string | null
  isActive: boolean
  
  // Playback state
  isPlaying: boolean
  isPaused: boolean
  duration: number
  position: number
  volume: number
  
  // Track information
  currentTrack: SpotifyTrack | null
  previousTracks: SpotifyTrack[]
  nextTracks: SpotifyTrack[]
  
  // Loading states
  isLoading: boolean
  isBuffering: boolean
  
  // Error handling
  error: string | null
}

export interface PlayerControls {
  // Basic playback controls
  play: () => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  togglePlayPause: () => Promise<void>
  
  // Track navigation
  skipToNext: () => Promise<void>
  skipToPrevious: () => Promise<void>
  seek: (positionMs: number) => Promise<void>
  
  // Volume control
  setVolume: (volume: number) => Promise<void>
  
  // Device management
  activateDevice: () => Promise<void>
  transferPlayback: (deviceId: string) => Promise<void>
  
  // Queue management
  addToQueue: (uri: string) => Promise<void>
  
  // Playback context
  playTrack: (uri: string, contextUri?: string) => Promise<void>
  playContext: (contextUri: string, offset?: number) => Promise<void>
}

export interface UseSpotifyPlayerReturn {
  player: any | null // Using any for the Spotify.Player type
  state: PlayerState
  controls: PlayerControls
  isSDKReady: boolean
  initializePlayer: () => Promise<void>
  disconnectPlayer: () => void
}

export const useSpotifyPlayer = (): UseSpotifyPlayerReturn => {
  const { sdk, authState } = useSpotifyContext()
  const { socket } = useSocketContext()
  const [player, setPlayer] = useState<any | null>(null)
  const [isSDKReady, setIsSDKReady] = useState(false)
  const playerRef = useRef<any | null>(null)
  
  const [state, setState] = useState<PlayerState>({
    isReady: false,
    deviceId: null,
    isActive: false,
    isPlaying: false,
    isPaused: true,
    duration: 0,
    position: 0,
    volume: 0.5,
    currentTrack: null,
    previousTracks: [],
    nextTracks: [],
    isLoading: false,
    isBuffering: false,
    error: null
  })

  // Load Spotify Web Playback SDK
  useEffect(() => {
    if (typeof window !== 'undefined' && !window.Spotify) {
      const script = document.createElement('script')
      script.src = 'https://sdk.scdn.co/spotify-player.js'
      script.async = true
      
      document.body.appendChild(script)
      
      window.onSpotifyWebPlaybackSDKReady = () => {
        setIsSDKReady(true)
      }
    } else if (window.Spotify) {
      setIsSDKReady(true)
    }
  }, [])

  // Initialize player when SDK is ready and user is authenticated
  useEffect(() => {
    if (isSDKReady && authState.isAuthenticated && authState.isPremium && sdk && !player) {
      initializePlayer()
    }
  }, [isSDKReady, authState.isAuthenticated, authState.isPremium, sdk, player])

  const initializePlayer = useCallback(async () => {
    if (!isSDKReady || !authState.isAuthenticated || !authState.isPremium || !sdk) {
      setState(prev => ({ 
        ...prev, 
        error: 'SDK not ready, user not authenticated, or Premium required' 
      }))
      return
    }

    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }))

      // Get access token from the SDK
      const token = await sdk.getAccessToken()
      if (!token?.access_token) {
        throw new Error('No access token available')
      }

      // Create Spotify Player instance
      const spotifyPlayer = new window.Spotify.Player({
        name: 'DJ R3X Web Player',
        getOAuthToken: (cb: any) => {
          cb(token.access_token)
        },
        volume: 0.5
      })

      // Set up event listeners
      setupPlayerEventListeners(spotifyPlayer)

      // Connect to the player
      const success = await spotifyPlayer.connect()
      if (!success) {
        throw new Error('Failed to connect to Spotify Player')
      }

      setPlayer(spotifyPlayer)
      playerRef.current = spotifyPlayer
      
      setState(prev => ({ ...prev, isLoading: false }))
    } catch (error) {
      console.error('Failed to initialize Spotify Player:', error)
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: error instanceof Error ? error.message : 'Failed to initialize player'
      }))
    }
  }, [isSDKReady, authState.isAuthenticated, authState.isPremium, sdk])

  // Notify bridge of player ready state
  const notifyPlayerReady = useCallback((isReady: boolean, deviceId?: string) => {
    if (socket) {
      socket.emit('spotify_player_ready', {
        device_id: deviceId,
        device_name: 'DJ R3X Web Player',
        ready: isReady
      })
      console.log('Spotify player ready status sent to bridge:', isReady, deviceId)
    }
  }, [socket])

  const setupPlayerEventListeners = (spotifyPlayer: any) => {
    // Ready
    spotifyPlayer.addListener('ready', ({ device_id }: any) => {
      console.log('Ready with Device ID', device_id)
      setState(prev => ({
        ...prev,
        isReady: true,
        deviceId: device_id,
        error: null
      }))
      
      // Notify bridge that player is ready
      notifyPlayerReady(true, device_id)
    })

    // Not Ready
    spotifyPlayer.addListener('not_ready', ({ device_id }: any) => {
      console.log('Device ID has gone offline', device_id)
      setState(prev => ({
        ...prev,
        isReady: false,
        deviceId: null
      }))
      
      // Notify bridge that player is no longer ready
      notifyPlayerReady(false, device_id)
    })

    // Player state changes
    spotifyPlayer.addListener('player_state_changed', (spotifyState: any) => {
      if (!spotifyState) return

      const track = spotifyState.track_window.current_track
      
      setState(prev => ({
        ...prev,
        isPlaying: !spotifyState.paused,
        isPaused: spotifyState.paused,
        duration: spotifyState.duration,
        position: spotifyState.position,
        currentTrack: track ? {
          id: track.id!,
          name: track.name,
          artists: track.artists.map((artist: any) => ({
            id: artist.uri.split(':').pop()!,
            name: artist.name,
            uri: artist.uri
          })),
          album: {
            id: track.album.uri.split(':').pop()!,
            name: track.album.name,
            images: track.album.images
          },
          uri: track.uri,
          duration_ms: spotifyState.duration,
          preview_url: null
        } as SpotifyTrack : null,
        previousTracks: spotifyState.track_window.previous_tracks.map((t: any) => ({
          id: t.id!,
          name: t.name,
          artists: t.artists.map((artist: any) => ({
            id: artist.uri.split(':').pop()!,
            name: artist.name,
            uri: artist.uri
          })),
          uri: t.uri,
          duration_ms: 0,
          preview_url: null
        })) as SpotifyTrack[],
        nextTracks: spotifyState.track_window.next_tracks.map((t: any) => ({
          id: t.id!,
          name: t.name,
          artists: t.artists.map((artist: any) => ({
            id: artist.uri.split(':').pop()!,
            name: artist.name,
            uri: artist.uri
          })),
          uri: t.uri,
          duration_ms: 0,
          preview_url: null
        })) as SpotifyTrack[],
        isBuffering: false
      }))
    })

    // Initialization errors
    spotifyPlayer.addListener('initialization_error', ({ message }: any) => {
      console.error('Initialization Error:', message)
      setState(prev => ({ ...prev, error: `Initialization Error: ${message}` }))
    })

    // Authentication errors
    spotifyPlayer.addListener('authentication_error', ({ message }: any) => {
      console.error('Authentication Error:', message)
      setState(prev => ({ ...prev, error: `Authentication Error: ${message}` }))
    })

    // Account errors
    spotifyPlayer.addListener('account_error', ({ message }: any) => {
      console.error('Account Error:', message)
      setState(prev => ({ ...prev, error: `Account Error: ${message}` }))
    })

    // Playback errors
    spotifyPlayer.addListener('playback_error', ({ message }: any) => {
      console.error('Playback Error:', message)
      setState(prev => ({ ...prev, error: `Playback Error: ${message}` }))
    })
  }

  const disconnectPlayer = useCallback(() => {
    if (playerRef.current) {
      // Notify bridge that player is disconnecting
      notifyPlayerReady(false)
      
      playerRef.current.disconnect()
      playerRef.current = null
      setPlayer(null)
      setState(prev => ({
        ...prev,
        isReady: false,
        deviceId: null,
        isActive: false,
        isPlaying: false,
        isPaused: true,
        currentTrack: null,
        previousTracks: [],
        nextTracks: []
      }))
    }
  }, [notifyPlayerReady])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnectPlayer()
    }
  }, [disconnectPlayer])

  // Player controls
  const controls: PlayerControls = {
    play: async () => {
      if (!player) throw new Error('Player not initialized')
      await player.resume()
    },

    pause: async () => {
      if (!player) throw new Error('Player not initialized')
      await player.pause()
    },

    resume: async () => {
      if (!player) throw new Error('Player not initialized')
      await player.resume()
    },

    togglePlayPause: async () => {
      if (!player) throw new Error('Player not initialized')
      await player.togglePlay()
    },

    skipToNext: async () => {
      if (!player) throw new Error('Player not initialized')
      await player.nextTrack()
    },

    skipToPrevious: async () => {
      if (!player) throw new Error('Player not initialized')
      await player.previousTrack()
    },

    seek: async (positionMs: number) => {
      if (!player) throw new Error('Player not initialized')
      await player.seek(positionMs)
    },

    setVolume: async (volume: number) => {
      if (!player) throw new Error('Player not initialized')
      const clampedVolume = Math.max(0, Math.min(1, volume))
      await player.setVolume(clampedVolume)
      setState(prev => ({ ...prev, volume: clampedVolume }))
    },

    activateDevice: async () => {
      if (!sdk || !state.deviceId) throw new Error('SDK or device not available')
      
      try {
        await sdk.player.transferPlayback([state.deviceId], true)
        setState(prev => ({ ...prev, isActive: true }))
      } catch (error) {
        console.error('Failed to activate device:', error)
        throw error
      }
    },

    transferPlayback: async (deviceId: string) => {
      if (!sdk) throw new Error('SDK not available')
      
      try {
        await sdk.player.transferPlayback([deviceId], true)
      } catch (error) {
        console.error('Failed to transfer playback:', error)
        throw error
      }
    },

    addToQueue: async (uri: string) => {
      if (!sdk || !state.deviceId) throw new Error('SDK or device not available')
      
      try {
        await sdk.player.addItemToPlaybackQueue(uri, state.deviceId)
      } catch (error) {
        console.error('Failed to add to queue:', error)
        throw error
      }
    },

    playTrack: async (uri: string, contextUri?: string) => {
      if (!sdk || !state.deviceId) throw new Error('SDK or device not available')
      
      try {
        const playOptions: any = {
          device_id: state.deviceId,
          uris: [uri]
        }

        if (contextUri) {
          playOptions.context_uri = contextUri
          delete playOptions.uris
          playOptions.offset = { uri }
        }

        await sdk.player.startResumePlayback(playOptions)
      } catch (error) {
        console.error('Failed to play track:', error)
        throw error
      }
    },

    playContext: async (contextUri: string, offset?: number) => {
      if (!sdk || !state.deviceId) throw new Error('SDK or device not available')
      
      try {
        const playOptions: any = {
          device_id: state.deviceId,
          context_uri: contextUri
        }

        if (typeof offset === 'number') {
          playOptions.offset = { position: offset }
        }

        await sdk.player.startResumePlayback(playOptions)
      } catch (error) {
        console.error('Failed to play context:', error)
        throw error
      }
    }
  }

  return {
    player,
    state,
    controls,
    isSDKReady,
    initializePlayer,
    disconnectPlayer
  }
}

// Extend the Window interface to include Spotify SDK
declare global {
  interface Window {
    onSpotifyWebPlaybackSDKReady?: () => void
    Spotify: any
  }
}