import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { SpotifyWebPlayer } from '../../../src/components/spotify/SpotifyWebPlayer'

// Mock the hooks
const mockSpotifyContext = {
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

const mockPlayerState = {
  isReady: true,
  deviceId: 'test-device-id',
  isActive: true,
  isPlaying: false,
  isPaused: true,
  duration: 180000,
  position: 30000,
  volume: 0.5,
  currentTrack: {
    id: 'track-id',
    name: 'Test Track',
    artists: [{ id: 'artist-id', name: 'Test Artist', uri: 'spotify:artist:123' }],
    album: {
      id: 'album-id',
      name: 'Test Album',
      images: [{ url: 'test-image.jpg', height: 300, width: 300 }]
    },
    uri: 'spotify:track:track-id',
    duration_ms: 180000
  },
  previousTracks: [],
  nextTracks: [],
  isLoading: false,
  isBuffering: false,
  error: null
}

const mockPlayerControls = {
  play: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  togglePlayPause: vi.fn(),
  skipToNext: vi.fn(),
  skipToPrevious: vi.fn(),
  seek: vi.fn(),
  setVolume: vi.fn(),
  activateDevice: vi.fn(),
  transferPlayback: vi.fn(),
  addToQueue: vi.fn(),
  playTrack: vi.fn(),
  playContext: vi.fn()
}

const mockUseSpotifyPlayer = {
  player: {} as any,
  state: mockPlayerState,
  controls: mockPlayerControls,
  isSDKReady: true,
  initializePlayer: vi.fn(),
  disconnectPlayer: vi.fn()
}

vi.mock('../../../src/contexts/SpotifyContext', () => ({
  useSpotifyContext: () => mockSpotifyContext
}))

vi.mock('../../../src/hooks/useSpotifyPlayer', () => ({
  useSpotifyPlayer: () => mockUseSpotifyPlayer
}))

describe('SpotifyWebPlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    
    // Reset mock states
    mockSpotifyContext.authState = {
      isAuthenticated: true,
      isLoading: false,
      error: null,
      user: { id: 'test-user', display_name: 'Test User', product: 'premium' },
      isPremium: true
    }
    
    mockUseSpotifyPlayer.state = {
      ...mockPlayerState,
      isReady: true,
      isActive: true,
      isLoading: false,
      error: null
    }
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('Authentication States', () => {
    it('should show authentication required when not authenticated', () => {
      mockSpotifyContext.authState.isAuthenticated = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Spotify Authentication Required')).toBeInTheDocument()
      expect(screen.getByText('Connect your Spotify Premium account to enable Web Playback')).toBeInTheDocument()
      expect(screen.getByText('Connect Spotify')).toBeInTheDocument()
    })

    it('should handle authentication click', async () => {
      mockSpotifyContext.authState.isAuthenticated = false
      
      render(<SpotifyWebPlayer />)
      
      const connectButton = screen.getByText('Connect Spotify')
      fireEvent.click(connectButton)
      
      expect(mockSpotifyContext.authenticate).toHaveBeenCalled()
    })

    it('should show loading state during authentication', () => {
      mockSpotifyContext.authState.isAuthenticated = false
      mockSpotifyContext.authState.isLoading = true
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Connecting to Spotify...')).toBeInTheDocument()
    })

    it('should show authentication error', () => {
      mockSpotifyContext.authState.isAuthenticated = false
      mockSpotifyContext.authState.error = 'Authentication failed'
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Authentication failed')).toBeInTheDocument()
    })

    it('should hide auth section when showAuth is false', () => {
      mockSpotifyContext.authState.isAuthenticated = false
      
      render(<SpotifyWebPlayer showAuth={false} />)
      
      expect(screen.queryByText('Spotify Authentication Required')).not.toBeInTheDocument()
    })
  })

  describe('Premium Requirements', () => {
    it('should show premium required when user is not premium', () => {
      mockSpotifyContext.authState.isPremium = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Spotify Premium Required')).toBeInTheDocument()
      expect(screen.getByText('Web Playback SDK requires a Spotify Premium subscription')).toBeInTheDocument()
      expect(screen.getByText('Upgrade to Premium')).toBeInTheDocument()
      expect(screen.getByText('Disconnect')).toBeInTheDocument()
    })

    it('should handle logout from premium screen', () => {
      mockSpotifyContext.authState.isPremium = false
      
      render(<SpotifyWebPlayer />)
      
      const disconnectButton = screen.getByText('Disconnect')
      fireEvent.click(disconnectButton)
      
      expect(mockSpotifyContext.logout).toHaveBeenCalled()
    })

    it('should open Spotify Premium link', () => {
      mockSpotifyContext.authState.isPremium = false
      
      render(<SpotifyWebPlayer />)
      
      const upgradeLink = screen.getByText('Upgrade to Premium')
      expect(upgradeLink).toHaveAttribute('href', 'https://www.spotify.com/premium/')
      expect(upgradeLink).toHaveAttribute('target', '_blank')
    })
  })

  describe('Device Activation', () => {
    it('should show device activation when device is ready but not active', () => {
      mockUseSpotifyPlayer.state.isActive = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Activate DJ R3X Web Player')).toBeInTheDocument()
      expect(screen.getByText('Click below to make this your active Spotify device')).toBeInTheDocument()
      expect(screen.getByText('Activate Device')).toBeInTheDocument()
    })

    it('should handle device activation', () => {
      mockUseSpotifyPlayer.state.isActive = false
      
      render(<SpotifyWebPlayer />)
      
      const activateButton = screen.getByText('Activate Device')
      fireEvent.click(activateButton)
      
      expect(mockPlayerControls.activateDevice).toHaveBeenCalled()
    })

    it('should hide device activation when showDeviceActivation is false', () => {
      mockUseSpotifyPlayer.state.isActive = false
      
      render(<SpotifyWebPlayer showDeviceActivation={false} />)
      
      expect(screen.queryByText('Activate DJ R3X Web Player')).not.toBeInTheDocument()
    })
  })

  describe('Loading States', () => {
    it('should show loading when player is loading', () => {
      mockUseSpotifyPlayer.state.isLoading = true
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Initializing player...')).toBeInTheDocument()
    })

    it('should show loading when auth is loading', () => {
      mockSpotifyContext.authState.isLoading = true
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Initializing player...')).toBeInTheDocument()
    })

    it('should show loading when SDK is not ready', () => {
      mockUseSpotifyPlayer.isSDKReady = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Loading Spotify SDK...')).toBeInTheDocument()
    })
  })

  describe('Error States', () => {
    it('should show error state', () => {
      mockUseSpotifyPlayer.state.error = 'Player initialization failed'
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Player Error')).toBeInTheDocument()
      expect(screen.getByText('Player initialization failed')).toBeInTheDocument()
      expect(screen.getByText('Retry')).toBeInTheDocument()
      expect(screen.getByText('Reset')).toBeInTheDocument()
    })

    it('should handle retry on error', () => {
      mockUseSpotifyPlayer.state.error = 'Player initialization failed'
      
      render(<SpotifyWebPlayer />)
      
      const retryButton = screen.getByText('Retry')
      fireEvent.click(retryButton)
      
      expect(mockUseSpotifyPlayer.initializePlayer).toHaveBeenCalled()
    })

    it('should handle reset on error', () => {
      mockUseSpotifyPlayer.state.error = 'Player initialization failed'
      
      render(<SpotifyWebPlayer />)
      
      const resetButton = screen.getByText('Reset')
      fireEvent.click(resetButton)
      
      expect(mockUseSpotifyPlayer.disconnectPlayer).toHaveBeenCalled()
    })
  })

  describe('Player Interface', () => {
    it('should render main player interface when ready', () => {
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('DJ R3X Web Player')).toBeInTheDocument()
      expect(screen.getByText('Ready')).toBeInTheDocument()
      expect(screen.getByText('Test Track')).toBeInTheDocument()
      expect(screen.getByText('Test Artist')).toBeInTheDocument()
      expect(screen.getByText('Test Album')).toBeInTheDocument()
    })

    it('should show current track information', () => {
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Test Track')).toBeInTheDocument()
      expect(screen.getByText('Test Artist')).toBeInTheDocument()
      expect(screen.getByText('Test Album')).toBeInTheDocument()
      
      const albumImage = screen.getByAltText('Test Album')
      expect(albumImage).toHaveAttribute('src', 'test-image.jpg')
    })

    it('should format time correctly', () => {
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('0:30')).toBeInTheDocument() // 30000ms = 30s
      expect(screen.getByText('3:00')).toBeInTheDocument() // 180000ms = 3min
    })

    it('should show progress bar', () => {
      render(<SpotifyWebPlayer />)
      
      const progressBars = screen.getAllByRole('generic').filter(el => 
        el.className.includes('bg-sw-blue-500')
      )
      expect(progressBars.length).toBeGreaterThan(0)
    })
  })

  describe('Playback Controls', () => {
    it('should render playback controls', () => {
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByTitle('Previous Track')).toBeInTheDocument()
      expect(screen.getByTitle('Play')).toBeInTheDocument()
      expect(screen.getByTitle('Next Track')).toBeInTheDocument()
    })

    it('should show pause button when playing', () => {
      mockUseSpotifyPlayer.state.isPlaying = true
      mockUseSpotifyPlayer.state.isPaused = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByTitle('Pause')).toBeInTheDocument()
    })

    it('should handle play/pause toggle', () => {
      render(<SpotifyWebPlayer />)
      
      const playButton = screen.getByTitle('Play')
      fireEvent.click(playButton)
      
      expect(mockPlayerControls.togglePlayPause).toHaveBeenCalled()
    })

    it('should handle previous track', () => {
      render(<SpotifyWebPlayer />)
      
      const prevButton = screen.getByTitle('Previous Track')
      fireEvent.click(prevButton)
      
      expect(mockPlayerControls.skipToPrevious).toHaveBeenCalled()
    })

    it('should handle next track', () => {
      render(<SpotifyWebPlayer />)
      
      const nextButton = screen.getByTitle('Next Track')
      fireEvent.click(nextButton)
      
      expect(mockPlayerControls.skipToNext).toHaveBeenCalled()
    })

    it('should disable controls when not ready', () => {
      mockUseSpotifyPlayer.state.isReady = false
      
      render(<SpotifyWebPlayer />)
      
      const playButton = screen.getByTitle('Play')
      const prevButton = screen.getByTitle('Previous Track')
      const nextButton = screen.getByTitle('Next Track')
      
      expect(playButton).toBeDisabled()
      expect(prevButton).toBeDisabled()
      expect(nextButton).toBeDisabled()
    })
  })

  describe('Progress Bar Interaction', () => {
    it('should handle seek by clicking on progress bar', () => {
      render(<SpotifyWebPlayer />)
      
      const progressBars = screen.getAllByRole('generic').filter(el => 
        el.className.includes('bg-sw-dark-600')
      )
      const progressBar = progressBars[0]
      
      // Mock getBoundingClientRect
      vi.spyOn(progressBar, 'getBoundingClientRect').mockReturnValue({
        left: 0,
        width: 100,
        top: 0,
        right: 100,
        bottom: 10,
        height: 10,
        x: 0,
        y: 0,
        toJSON: () => ({})
      })
      
      fireEvent.click(progressBar, { clientX: 50 })
      
      expect(mockPlayerControls.seek).toHaveBeenCalledWith(90000) // 50% of 180000ms
    })

    it('should not seek when duration is zero', () => {
      mockUseSpotifyPlayer.state.duration = 0
      
      render(<SpotifyWebPlayer />)
      
      const progressBars = screen.getAllByRole('generic').filter(el => 
        el.className.includes('bg-sw-dark-600')
      )
      const progressBar = progressBars[0]
      
      fireEvent.click(progressBar, { clientX: 50 })
      
      expect(mockPlayerControls.seek).not.toHaveBeenCalled()
    })
  })

  describe('Volume Control', () => {
    it('should show volume control when speaker icon is clicked', () => {
      render(<SpotifyWebPlayer />)
      
      const volumeButton = screen.getByTitle('Volume: 50%')
      fireEvent.click(volumeButton)
      
      expect(screen.getByRole('slider')).toBeInTheDocument()
      expect(screen.getByText('50%')).toBeInTheDocument()
    })

    it('should handle volume change', () => {
      render(<SpotifyWebPlayer />)
      
      // Open volume slider
      const volumeButton = screen.getByTitle('Volume: 50%')
      fireEvent.click(volumeButton)
      
      const volumeSlider = screen.getByRole('slider')
      fireEvent.change(volumeSlider, { target: { value: '0.8' } })
      
      expect(mockPlayerControls.setVolume).toHaveBeenCalledWith(0.8)
    })

    it('should show muted icon when volume is zero', () => {
      mockUseSpotifyPlayer.state.volume = 0
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByTitle('Volume: 0%')).toBeInTheDocument()
    })
  })

  describe('Status Indicators', () => {
    it('should show buffering indicator', () => {
      mockUseSpotifyPlayer.state.isBuffering = true
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Buffering...')).toBeInTheDocument()
    })

    it('should show device information', () => {
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText(/Device: DJ R3X \(test-dev...\)/)).toBeInTheDocument()
      expect(screen.getByText('Status: Active')).toBeInTheDocument()
    })

    it('should show inactive status when device is not active', () => {
      mockUseSpotifyPlayer.state.isActive = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Status: Inactive')).toBeInTheDocument()
    })

    it('should show not ready status', () => {
      mockUseSpotifyPlayer.state.isReady = false
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('Not Ready')).toBeInTheDocument()
    })
  })

  describe('Custom Props', () => {
    it('should apply custom className', () => {
      const { container } = render(<SpotifyWebPlayer className="custom-class" />)
      
      expect(container.firstChild).toHaveClass('custom-class')
    })
  })

  describe('No Current Track', () => {
    it('should handle no current track gracefully', () => {
      mockUseSpotifyPlayer.state.currentTrack = null
      
      render(<SpotifyWebPlayer />)
      
      expect(screen.getByText('DJ R3X Web Player')).toBeInTheDocument()
      expect(screen.queryByText('Test Track')).not.toBeInTheDocument()
    })
  })
})