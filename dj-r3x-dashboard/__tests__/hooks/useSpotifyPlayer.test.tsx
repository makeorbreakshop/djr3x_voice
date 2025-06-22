import React from 'react'
import { renderHook, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { useSpotifyPlayer } from '../../src/hooks/useSpotifyPlayer'

// Mock the SpotifyContext
const mockSpotifyContext = {
  sdk: null as any,
  authState: {
    isAuthenticated: true,
    isLoading: false,
    error: null,
    user: { id: 'test-user', display_name: 'Test User', product: 'premium' },
    isPremium: true
  },
  authenticate: vi.fn(),
  logout: vi.fn(),
  checkPremiumStatus: vi.fn()
}

vi.mock('../../src/contexts/SpotifyContext', async () => {
  const actual = await vi.importActual('../../src/contexts/SpotifyContext')
  return {
    ...actual,
    useSpotifyContext: () => mockSpotifyContext,
    SpotifyProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>
  }
})

// Mock Spotify Web Playback SDK
const mockPlayer = {
  connect: vi.fn(),
  disconnect: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
  resume: vi.fn(),
  pause: vi.fn(),
  togglePlay: vi.fn(),
  nextTrack: vi.fn(),
  previousTrack: vi.fn(),
  seek: vi.fn(),
  setVolume: vi.fn(),
  getVolume: vi.fn(),
  getCurrentState: vi.fn()
}

// Mock the global Spotify object
global.window = Object.create(window)
Object.defineProperty(window, 'Spotify', {
  value: {
    Player: vi.fn(() => mockPlayer)
  },
  writable: true
})

const mockSDK = {
  getAccessToken: vi.fn(),
  player: {
    transferPlayback: vi.fn(),
    addItemToPlaybackQueue: vi.fn(),
    startResumePlayback: vi.fn()
  }
}

// No wrapper needed since we're mocking the context

describe('useSpotifyPlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    
    // Reset mock context
    mockSpotifyContext.sdk = mockSDK
    mockSpotifyContext.authState = {
      isAuthenticated: true,
      isLoading: false,
      error: null,
      user: { id: 'test-user', display_name: 'Test User', product: 'premium' },
      isPremium: true
    }
    
    // Mock successful connection
    mockPlayer.connect.mockResolvedValue(true)
    mockSDK.getAccessToken.mockResolvedValue({ access_token: 'test-token' })
    
    // Mock script loading
    document.body.appendChild = vi.fn()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('Initialization', () => {
    it('should initialize with default state', () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      expect(result.current.player).toBeNull()
      expect(result.current.isSDKReady).toBe(false)
      expect(result.current.state.isReady).toBe(false)
      expect(result.current.state.isPlaying).toBe(false)
      expect(result.current.state.volume).toBe(0.5)
    })

    it('should load Spotify SDK script when not available', () => {
      // Mock window.Spotify as undefined
      const originalSpotify = (window as any).Spotify
      ;(window as any).Spotify = undefined
      
      renderHook(() => useSpotifyPlayer())
      
      expect(document.body.appendChild).toHaveBeenCalled()
      
      // Restore
      ;(window as any).Spotify = originalSpotify
    })

    it('should set SDK ready when Spotify is already available', () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      expect(result.current.isSDKReady).toBe(false)
      
      // Simulate SDK ready
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      expect(result.current.isSDKReady).toBe(true)
    })
  })

  describe('Player Initialization', () => {
    it('should initialize player when all conditions are met', async () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      // Simulate SDK ready
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      // Initialize player
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      expect(window.Spotify.Player).toHaveBeenCalledWith({
        name: 'DJ R3X Web Player',
        getOAuthToken: expect.any(Function),
        volume: 0.5
      })
      expect(mockPlayer.connect).toHaveBeenCalled()
    })

    it('should handle initialization failure', async () => {
      mockPlayer.connect.mockResolvedValue(false)
      
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      expect(result.current.state.error).toBe('Failed to connect to Spotify Player')
    })

    it('should not initialize when user is not authenticated', async () => {
      mockSpotifyContext.authState.isAuthenticated = false
      
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      expect(result.current.state.error).toContain('SDK not ready, user not authenticated, or Premium required')
    })

    it('should not initialize when user is not premium', async () => {
      mockSpotifyContext.authState.isPremium = false
      
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      expect(result.current.state.error).toContain('SDK not ready, user not authenticated, or Premium required')
    })
  })

  describe('Player Event Listeners', () => {
    let eventListeners: { [key: string]: Function } = {}

    beforeEach(() => {
      eventListeners = {}
      mockPlayer.addListener.mockImplementation((event, callback) => {
        eventListeners[event] = callback
      })
    })

    it('should handle ready event', async () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      // Simulate ready event
      act(() => {
        eventListeners['ready']({ device_id: 'test-device-id' })
      })
      
      expect(result.current.state.isReady).toBe(true)
      expect(result.current.state.deviceId).toBe('test-device-id')
    })

    it('should handle not ready event', async () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      // First set ready
      act(() => {
        eventListeners['ready']({ device_id: 'test-device-id' })
      })
      
      // Then simulate not ready
      act(() => {
        eventListeners['not_ready']({ device_id: 'test-device-id' })
      })
      
      expect(result.current.state.isReady).toBe(false)
      expect(result.current.state.deviceId).toBeNull()
    })

    it('should handle player state changed event', async () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      const mockSpotifyState = {
        paused: false,
        duration: 180000,
        position: 30000,
        track_window: {
          current_track: {
            id: 'track-id',
            name: 'Test Track',
            artists: [{ name: 'Test Artist', uri: 'spotify:artist:123' }],
            album: { 
              name: 'Test Album', 
              images: [{ url: 'test-image.jpg' }],
              uri: 'spotify:album:456'
            },
            uri: 'spotify:track:track-id'
          },
          previous_tracks: [],
          next_tracks: []
        }
      }
      
      act(() => {
        eventListeners['player_state_changed'](mockSpotifyState)
      })
      
      expect(result.current.state.isPlaying).toBe(true)
      expect(result.current.state.isPaused).toBe(false)
      expect(result.current.state.duration).toBe(180000)
      expect(result.current.state.position).toBe(30000)
      expect(result.current.state.currentTrack?.name).toBe('Test Track')
    })

    it('should handle initialization error', async () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      act(() => {
        eventListeners['initialization_error']({ message: 'Init failed' })
      })
      
      expect(result.current.state.error).toBe('Initialization Error: Init failed')
    })
  })

  describe('Player Controls', () => {
    let result: any

    beforeEach(async () => {
      const hook = renderHook(() => useSpotifyPlayer())
      result = hook.result
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
    })

    it('should play track', async () => {
      await act(async () => {
        await result.current.controls.play()
      })
      
      expect(mockPlayer.resume).toHaveBeenCalled()
    })

    it('should pause track', async () => {
      await act(async () => {
        await result.current.controls.pause()
      })
      
      expect(mockPlayer.pause).toHaveBeenCalled()
    })

    it('should toggle play/pause', async () => {
      await act(async () => {
        await result.current.controls.togglePlayPause()
      })
      
      expect(mockPlayer.togglePlay).toHaveBeenCalled()
    })

    it('should skip to next track', async () => {
      await act(async () => {
        await result.current.controls.skipToNext()
      })
      
      expect(mockPlayer.nextTrack).toHaveBeenCalled()
    })

    it('should skip to previous track', async () => {
      await act(async () => {
        await result.current.controls.skipToPrevious()
      })
      
      expect(mockPlayer.previousTrack).toHaveBeenCalled()
    })

    it('should seek to position', async () => {
      await act(async () => {
        await result.current.controls.seek(60000)
      })
      
      expect(mockPlayer.seek).toHaveBeenCalledWith(60000)
    })

    it('should set volume', async () => {
      await act(async () => {
        await result.current.controls.setVolume(0.8)
      })
      
      expect(mockPlayer.setVolume).toHaveBeenCalledWith(0.8)
      expect(result.current.state.volume).toBe(0.8)
    })

    it('should clamp volume to valid range', async () => {
      await act(async () => {
        await result.current.controls.setVolume(1.5)
      })
      
      expect(mockPlayer.setVolume).toHaveBeenCalledWith(1)
      
      await act(async () => {
        await result.current.controls.setVolume(-0.5)
      })
      
      expect(mockPlayer.setVolume).toHaveBeenCalledWith(0)
    })

    it('should throw error when player not initialized', async () => {
      // Reset to no player
      result.current.disconnectPlayer()
      
      await expect(result.current.controls.play()).rejects.toThrow('Player not initialized')
    })
  })

  describe('SDK Controls', () => {
    let result: any

    beforeEach(async () => {
      const hook = renderHook(() => useSpotifyPlayer())
      result = hook.result
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      await act(async () => {
        await result.current.initializePlayer()
      })
      
      // Set device as ready
      act(() => {
        const eventListeners: { [key: string]: Function } = {}
        mockPlayer.addListener.mockImplementation((event, callback) => {
          eventListeners[event] = callback
        })
        eventListeners['ready']?.({ device_id: 'test-device-id' })
      })
    })

    it('should activate device', async () => {
      await act(async () => {
        await result.current.controls.activateDevice()
      })
      
      expect(mockSDK.player.transferPlayback).toHaveBeenCalledWith(['test-device-id'], true)
      expect(result.current.state.isActive).toBe(true)
    })

    it('should transfer playback to another device', async () => {
      await act(async () => {
        await result.current.controls.transferPlayback('other-device-id')
      })
      
      expect(mockSDK.player.transferPlayback).toHaveBeenCalledWith(['other-device-id'], true)
    })

    it('should add track to queue', async () => {
      await act(async () => {
        await result.current.controls.addToQueue('spotify:track:123')
      })
      
      expect(mockSDK.player.addItemToPlaybackQueue).toHaveBeenCalledWith('spotify:track:123', 'test-device-id')
    })

    it('should play track', async () => {
      await act(async () => {
        await result.current.controls.playTrack('spotify:track:123')
      })
      
      expect(mockSDK.player.startResumePlayback).toHaveBeenCalledWith({
        device_id: 'test-device-id',
        uris: ['spotify:track:123']
      })
    })

    it('should play track with context', async () => {
      await act(async () => {
        await result.current.controls.playTrack('spotify:track:123', 'spotify:album:456')
      })
      
      expect(mockSDK.player.startResumePlayback).toHaveBeenCalledWith({
        device_id: 'test-device-id',
        context_uri: 'spotify:album:456',
        offset: { uri: 'spotify:track:123' }
      })
    })

    it('should play context', async () => {
      await act(async () => {
        await result.current.controls.playContext('spotify:playlist:123', 2)
      })
      
      expect(mockSDK.player.startResumePlayback).toHaveBeenCalledWith({
        device_id: 'test-device-id',
        context_uri: 'spotify:playlist:123',
        offset: { position: 2 }
      })
    })
  })

  describe('Cleanup', () => {
    it('should disconnect player on unmount', () => {
      const { result, unmount } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      // Simulate player being set
      result.current.initializePlayer()
      
      unmount()
      
      expect(mockPlayer.disconnect).toHaveBeenCalled()
    })

    it('should reset state when disconnecting', () => {
      const { result } = renderHook(() => useSpotifyPlayer())
      
      act(() => {
        window.onSpotifyWebPlaybackSDKReady?.()
      })
      
      // Initialize and then disconnect
      result.current.initializePlayer()
      result.current.disconnectPlayer()
      
      expect(result.current.player).toBeNull()
      expect(result.current.state.isReady).toBe(false)
      expect(result.current.state.deviceId).toBeNull()
    })
  })
})