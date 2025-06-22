import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { SpotifyProvider, useSpotifyContext } from '../../src/contexts/SpotifyContext'
import { SpotifyApi } from '@spotify/web-api-ts-sdk'

// Mock the Spotify SDK
vi.mock('@spotify/web-api-ts-sdk', () => ({
  SpotifyApi: {
    withUserAuthorization: vi.fn(() => ({
      currentUser: {
        profile: vi.fn()
      },
      authenticate: vi.fn(),
      logOut: vi.fn(),
      getAccessToken: vi.fn()
    }))
  }
}))

// Test component that uses the context
const TestComponent = () => {
  const { sdk, authState, authenticate, logout, checkPremiumStatus } = useSpotifyContext()
  
  return (
    <div>
      <div data-testid="auth-state">
        {authState.isAuthenticated ? 'authenticated' : 'not-authenticated'}
      </div>
      <div data-testid="loading-state">
        {authState.isLoading ? 'loading' : 'not-loading'}
      </div>
      <div data-testid="premium-state">
        {authState.isPremium ? 'premium' : 'not-premium'}
      </div>
      <div data-testid="error-state">
        {authState.error || 'no-error'}
      </div>
      <div data-testid="user-name">
        {authState.user?.display_name || 'no-user'}
      </div>
      <button onClick={authenticate} data-testid="authenticate-btn">
        Authenticate
      </button>
      <button onClick={logout} data-testid="logout-btn">
        Logout
      </button>
      <button onClick={() => checkPremiumStatus()} data-testid="check-premium-btn">
        Check Premium
      </button>
    </div>
  )
}

const renderWithProvider = () => {
  return render(
    <SpotifyProvider>
      <TestComponent />
    </SpotifyProvider>
  )
}

describe('SpotifyContext', () => {
  let mockSDK: any
  let mockCurrentUser: any

  beforeEach(() => {
    // Reset mocks
    vi.clearAllMocks()
    
    // Mock environment variables
    process.env.NEXT_PUBLIC_SPOTIFY_CLIENT_ID = 'test-client-id'
    
    // Mock window.location.origin
    delete (window as any).location
    window.location = { origin: 'http://localhost:3000' } as any
    
    // Setup mock SDK
    mockCurrentUser = {
      profile: vi.fn()
    }
    
    mockSDK = {
      currentUser: mockCurrentUser,
      authenticate: vi.fn(),
      logOut: vi.fn(),
      getAccessToken: vi.fn()
    }
    
    vi.mocked(SpotifyApi.withUserAuthorization).mockReturnValue(mockSDK)
  })

  afterEach(() => {
    vi.clearAllMocks()
    delete process.env.NEXT_PUBLIC_SPOTIFY_CLIENT_ID
  })

  describe('Initialization', () => {
    it('should initialize with loading state', () => {
      renderWithProvider()
      
      expect(screen.getByTestId('auth-state')).toHaveTextContent('not-authenticated')
      expect(screen.getByTestId('loading-state')).toHaveTextContent('loading')
      expect(screen.getByTestId('premium-state')).toHaveTextContent('not-premium')
      expect(screen.getByTestId('error-state')).toHaveTextContent('no-error')
    })

    it('should initialize SDK with correct parameters', async () => {
      renderWithProvider()
      
      await waitFor(() => {
        expect(SpotifyApi.withUserAuthorization).toHaveBeenCalledWith(
          'test-client-id',
          'http://localhost:3000/api/auth/callback/spotify',
          [
            'streaming',
            'user-read-playback-state',
            'user-modify-playback-state',
            'user-read-currently-playing',
            'user-read-private'
          ]
        )
      })
    })

    it('should handle missing client ID', async () => {
      delete process.env.NEXT_PUBLIC_SPOTIFY_CLIENT_ID
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('error-state')).toHaveTextContent('Spotify Client ID not configured')
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
    })
  })

  describe('Authentication Flow', () => {
    it('should handle successful authentication', async () => {
      const mockUser = {
        id: 'test-user',
        display_name: 'Test User',
        product: 'premium'
      }
      
      mockCurrentUser.profile.mockResolvedValue(mockUser)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
      
      const authenticateBtn = screen.getByTestId('authenticate-btn')
      
      await act(async () => {
        fireEvent.click(authenticateBtn)
      })
      
      await waitFor(() => {
        expect(mockSDK.authenticate).toHaveBeenCalled()
        expect(screen.getByTestId('auth-state')).toHaveTextContent('authenticated')
        expect(screen.getByTestId('premium-state')).toHaveTextContent('premium')
        expect(screen.getByTestId('user-name')).toHaveTextContent('Test User')
      })
    })

    it('should handle authentication with non-premium user', async () => {
      const mockUser = {
        id: 'test-user',
        display_name: 'Test User',
        product: 'free'
      }
      
      mockCurrentUser.profile.mockResolvedValue(mockUser)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
      
      const authenticateBtn = screen.getByTestId('authenticate-btn')
      
      await act(async () => {
        fireEvent.click(authenticateBtn)
      })
      
      await waitFor(() => {
        expect(screen.getByTestId('auth-state')).toHaveTextContent('authenticated')
        expect(screen.getByTestId('premium-state')).toHaveTextContent('not-premium')
        expect(screen.getByTestId('error-state')).toHaveTextContent('Spotify Premium is required for playback features')
      })
    })

    it('should handle authentication failure', async () => {
      mockSDK.authenticate.mockRejectedValue(new Error('Authentication failed'))
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
      
      const authenticateBtn = screen.getByTestId('authenticate-btn')
      
      await act(async () => {
        fireEvent.click(authenticateBtn)
      })
      
      await waitFor(() => {
        expect(screen.getByTestId('auth-state')).toHaveTextContent('not-authenticated')
        expect(screen.getByTestId('error-state')).toHaveTextContent('Authentication failed')
      })
    })
  })

  describe('Logout', () => {
    it('should handle logout successfully', async () => {
      const mockUser = {
        id: 'test-user',
        display_name: 'Test User',
        product: 'premium'
      }
      
      mockCurrentUser.profile.mockResolvedValue(mockUser)
      
      // Mock localStorage
      const mockLocalStorage = {
        removeItem: vi.fn()
      }
      Object.defineProperty(window, 'localStorage', {
        value: mockLocalStorage,
        writable: true
      })
      
      renderWithProvider()
      
      // First authenticate
      await waitFor(() => {
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
      
      const authenticateBtn = screen.getByTestId('authenticate-btn')
      await act(async () => {
        fireEvent.click(authenticateBtn)
      })
      
      await waitFor(() => {
        expect(screen.getByTestId('auth-state')).toHaveTextContent('authenticated')
      })
      
      // Then logout
      const logoutBtn = screen.getByTestId('logout-btn')
      await act(async () => {
        fireEvent.click(logoutBtn)
      })
      
      await waitFor(() => {
        expect(mockSDK.logOut).toHaveBeenCalled()
        expect(mockLocalStorage.removeItem).toHaveBeenCalledWith('spotify-sdk')
        expect(screen.getByTestId('auth-state')).toHaveTextContent('not-authenticated')
        expect(screen.getByTestId('premium-state')).toHaveTextContent('not-premium')
        expect(screen.getByTestId('user-name')).toHaveTextContent('no-user')
      })
    })
  })

  describe('Premium Status Check', () => {
    it('should check premium status correctly', async () => {
      const mockUser = {
        id: 'test-user',
        display_name: 'Test User',
        product: 'premium'
      }
      
      mockCurrentUser.profile.mockResolvedValue(mockUser)
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
      
      // First authenticate
      const authenticateBtn = screen.getByTestId('authenticate-btn')
      await act(async () => {
        fireEvent.click(authenticateBtn)
      })
      
      await waitFor(() => {
        expect(screen.getByTestId('auth-state')).toHaveTextContent('authenticated')
      })
      
      // Check premium status
      const checkPremiumBtn = screen.getByTestId('check-premium-btn')
      await act(async () => {
        fireEvent.click(checkPremiumBtn)
      })
      
      await waitFor(() => {
        expect(mockCurrentUser.profile).toHaveBeenCalled()
      })
    })

    it('should handle premium status check failure', async () => {
      mockCurrentUser.profile.mockRejectedValue(new Error('Failed to get user profile'))
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
      
      const checkPremiumBtn = screen.getByTestId('check-premium-btn')
      await act(async () => {
        fireEvent.click(checkPremiumBtn)
      })
      
      // Should not throw error, but return false
      await waitFor(() => {
        expect(mockCurrentUser.profile).toHaveBeenCalled()
      })
    })
  })

  describe('Error Handling', () => {
    it('should handle SDK initialization failure', async () => {
      vi.mocked(SpotifyApi.withUserAuthorization).mockImplementation(() => {
        throw new Error('SDK initialization failed')
      })
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('error-state')).toHaveTextContent('SDK initialization failed')
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
      })
    })

    it('should handle user profile fetch failure during initialization', async () => {
      mockCurrentUser.profile.mockRejectedValue(new Error('Profile fetch failed'))
      
      renderWithProvider()
      
      await waitFor(() => {
        expect(screen.getByTestId('auth-state')).toHaveTextContent('not-authenticated')
        expect(screen.getByTestId('loading-state')).toHaveTextContent('not-loading')
        expect(screen.getByTestId('error-state')).toHaveTextContent('no-error')
      })
    })
  })

  describe('Context Provider Error', () => {
    it('should throw error when used outside provider', () => {
      const TestComponentOutsideProvider = () => {
        const context = useSpotifyContext()
        return <div>{context.authState.isAuthenticated ? 'authenticated' : 'not-authenticated'}</div>
      }
      
      // Suppress console.error for this test
      const originalError = console.error
      console.error = vi.fn()
      
      expect(() => {
        render(<TestComponentOutsideProvider />)
      }).toThrow('useSpotifyContext must be used within a SpotifyProvider')
      
      // Restore console.error
      console.error = originalError
    })
  })
})