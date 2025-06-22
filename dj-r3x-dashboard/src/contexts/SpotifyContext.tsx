'use client'

import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { SpotifyApi } from '@spotify/web-api-ts-sdk'
import { useSocketContext } from './SocketContext'

export interface UserProfile {
  id: string
  display_name?: string
  email?: string
  product?: string
}

export interface SpotifyAuthState {
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  user: UserProfile | null
  isPremium: boolean
}

export interface SpotifyContextType {
  sdk: SpotifyApi | null
  authState: SpotifyAuthState
  authenticate: () => Promise<void>
  logout: () => void
  checkPremiumStatus: () => Promise<boolean>
}

const SpotifyContext = createContext<SpotifyContextType | null>(null)

export const useSpotifyContext = () => {
  const context = useContext(SpotifyContext)
  if (!context) {
    throw new Error('useSpotifyContext must be used within a SpotifyProvider')
  }
  return context
}

interface SpotifyProviderProps {
  children: ReactNode
}

export const SpotifyProvider: React.FC<SpotifyProviderProps> = ({ children }) => {
  const { socket } = useSocketContext()
  const [sdk, setSdk] = useState<SpotifyApi | null>(null)
  const [authState, setAuthState] = useState<SpotifyAuthState>({
    isAuthenticated: false,
    isLoading: true,
    error: null,
    user: null,
    isPremium: false
  })

  // Required OAuth scopes for Web Playback SDK
  const REQUIRED_SCOPES = [
    'streaming',
    'user-read-playback-state',
    'user-modify-playback-state',
    'user-read-currently-playing',
    'user-read-private'
  ]

  useEffect(() => {
    initializeSpotifySDK()
  }, [])

  // Check for authorization code in URL and process it
  useEffect(() => {
    if (!sdk) return
    
    const checkAuthStatus = async () => {
      try {
        // STEP 1: Check current URL for authorization code
        const urlParams = new URLSearchParams(window.location.search)
        const authCode = urlParams.get('code')
        const authError = urlParams.get('error')
        
        console.log('=== SPOTIFY AUTH DEBUG ===')
        console.log('Current URL:', window.location.href)
        console.log('Authorization code found:', authCode ? 'YES' : 'NO')
        console.log('Auth error found:', authError || 'NONE')
        
        if (authError) {
          console.error('Spotify OAuth error:', authError)
          setAuthState({
            isAuthenticated: false,
            isLoading: false,
            error: `Spotify OAuth error: ${authError}`,
            user: null,
            isPremium: false
          })
          return
        }
        
        if (authCode) {
          console.log('Processing authorization code...')
          setAuthState(prev => ({ ...prev, isLoading: true, error: null }))
          
          // Let the SDK handle the code automatically
          console.log('Waiting for SDK to process authorization code...')
          await new Promise(resolve => setTimeout(resolve, 1000)) // Give SDK time to process
        }
        
        // STEP 2: Check if we have an access token
        console.log('Checking for access token...')
        const token = await sdk.getAccessToken()
        console.log('Access token found:', token ? 'YES' : 'NO')
        
        if (token && !authState.isAuthenticated) {
          console.log('Found access token, fetching user profile...')
          
          // STEP 3: Get user profile
          const user = await sdk.currentUser.profile()
          console.log('User profile loaded:', user.display_name)
          
          // STEP 4: Check premium status
          const isPremium = await checkPremiumStatus(sdk)
          console.log('Premium status:', isPremium)
          
          setAuthState({
            isAuthenticated: true,
            isLoading: false,
            error: null,
            user,
            isPremium
          })
          
          console.log('✅ Authentication successful!')
          
          // Clean up URL if we processed an auth code
          if (authCode) {
            window.history.replaceState({}, document.title, window.location.pathname)
            console.log('Cleaned up authorization code from URL')
          }
          
        } else if (!token) {
          console.log('No access token available')
          setAuthState(prev => ({ 
            ...prev, 
            isAuthenticated: false, 
            isLoading: false 
          }))
        }
        
      } catch (error) {
        console.error('❌ Authentication check failed:', error)
        setAuthState({
          isAuthenticated: false,
          isLoading: false,
          error: error instanceof Error ? error.message : 'Authentication failed',
          user: null,
          isPremium: false
        })
      }
    }
    
    // Check auth status after a short delay to let SDK process any OAuth callbacks
    const timer = setTimeout(checkAuthStatus, 500)
    return () => clearTimeout(timer)
  }, [sdk])

  // Notify bridge when auth state changes
  useEffect(() => {
    if (!authState.isLoading) {
      notifyAuthStatus(authState)
    }
  }, [authState.isAuthenticated, authState.isPremium, authState.user, authState.error, socket])

  const initializeSpotifySDK = async () => {
    try {
      setAuthState(prev => ({ ...prev, isLoading: true, error: null }))

      const clientId = process.env.NEXT_PUBLIC_SPOTIFY_CLIENT_ID
      if (!clientId) {
        console.warn('Spotify Client ID not configured in environment variables')
        setAuthState({
          isAuthenticated: false,
          isLoading: false,
          error: 'Spotify Client ID not configured. Please add NEXT_PUBLIC_SPOTIFY_CLIENT_ID to your environment variables.',
          user: null,
          isPremium: false
        })
        return
      }

      // Use simple redirect URI as per Spotify SDK documentation
      const redirectUri = process.env.NEXT_PUBLIC_SPOTIFY_REDIRECT_URI || 'http://127.0.0.1:3000'
      
      console.log('=== SPOTIFY SDK INITIALIZATION DEBUG ===')
      console.log('Client ID from env:', clientId)
      console.log('Client ID length:', clientId?.length)
      console.log('Redirect URI from env:', redirectUri)
      console.log('Required scopes:', REQUIRED_SCOPES)
      console.log('Current page URL:', window.location.href)
      
      // Initialize Spotify SDK with Authorization Code Flow + PKCE
      // The SDK handles PKCE automatically when using withUserAuthorization
      const spotifySDK = SpotifyApi.withUserAuthorization(
        clientId,
        redirectUri,
        REQUIRED_SCOPES
      )

      setSdk(spotifySDK)
      console.log('Spotify SDK initialized successfully')

      // Check if user is already authenticated
      try {
        // Try to get current user without triggering auth flow
        const accessToken = await spotifySDK.getAccessToken()
        if (accessToken) {
          console.log('Found existing access token, getting user profile...')
          const user = await spotifySDK.currentUser.profile()
          const isPremium = await checkPremiumStatus(spotifySDK)
          
          setAuthState({
            isAuthenticated: true,
            isLoading: false,
            error: null,
            user,
            isPremium
          })
          console.log('User already authenticated:', user.display_name)
        } else {
          throw new Error('No access token available')
        }
      } catch (error) {
        // User is not authenticated yet or token expired
        console.log('No existing authentication found')
        setAuthState({
          isAuthenticated: false,
          isLoading: false,
          error: null,
          user: null,
          isPremium: false
        })
      }
    } catch (error) {
      console.error('Failed to initialize Spotify SDK:', error)
      setAuthState({
        isAuthenticated: false,
        isLoading: false,
        error: error instanceof Error ? error.message : 'Failed to initialize Spotify SDK',
        user: null,
        isPremium: false
      })
    }
  }

  const authenticate = async () => {
    if (!sdk) {
      throw new Error('Spotify SDK not initialized')
    }

    try {
      setAuthState(prev => ({ ...prev, isLoading: true, error: null }))
      
      console.log('=== SPOTIFY AUTHENTICATION DEBUG ===')
      console.log('SDK initialized:', !!sdk)
      console.log('Client ID:', process.env.NEXT_PUBLIC_SPOTIFY_CLIENT_ID)
      console.log('Redirect URI:', process.env.NEXT_PUBLIC_SPOTIFY_REDIRECT_URI || 'http://127.0.0.1:3000')
      console.log('Bridge URL:', process.env.NEXT_PUBLIC_BRIDGE_URL || 'http://localhost:8000')
      
      // Use the official SDK mixed authentication pattern with bridge postback
      const bridgeUrl = process.env.NEXT_PUBLIC_BRIDGE_URL || 'http://localhost:8000'
      const clientId = process.env.NEXT_PUBLIC_SPOTIFY_CLIENT_ID!
      const redirectUri = process.env.NEXT_PUBLIC_SPOTIFY_REDIRECT_URI || 'http://127.0.0.1:3000'
      
      console.log('Using mixed authentication pattern with bridge postback...')
      console.log('Bridge endpoint:', `${bridgeUrl}/accept-user-token`)
      
      await SpotifyApi.performUserAuthorization(
        clientId,
        redirectUri,
        REQUIRED_SCOPES,
        `${bridgeUrl}/accept-user-token`
      )
      
      console.log('Mixed authentication initiated - token will be posted to bridge...')
      
    } catch (error) {
      console.error('❌ Spotify authentication failed:', error)
      console.error('Error details:', {
        name: error instanceof Error ? error.name : 'Unknown',
        message: error instanceof Error ? error.message : String(error),
        stack: error instanceof Error ? error.stack : undefined
      })
      
      setAuthState({
        isAuthenticated: false,
        isLoading: false,
        error: error instanceof Error ? error.message : 'Authentication failed',
        user: null,
        isPremium: false
      })
    }
  }

  const logout = () => {
    try {
      if (sdk) {
        sdk.logOut()
      }
      
      setAuthState({
        isAuthenticated: false,
        isLoading: false,
        error: null,
        user: null,
        isPremium: false
      })

      // Clear any stored tokens
      localStorage.removeItem('spotify-sdk')
    } catch (error) {
      console.error('Logout failed:', error)
    }
  }

  const checkPremiumStatus = async (sdkInstance?: SpotifyApi): Promise<boolean> => {
    try {
      const spotifySDK = sdkInstance || sdk
      if (!spotifySDK) return false

      const user = await spotifySDK.currentUser.profile()
      return user.product === 'premium'
    } catch (error) {
      console.error('Failed to check premium status:', error)
      return false
    }
  }

  // Communicate auth status to bridge/CantinaOS
  const notifyAuthStatus = (authState: SpotifyAuthState) => {
    if (socket) {
      socket.emit('spotify_auth_status_update', {
        authenticated: authState.isAuthenticated,
        user_id: authState.user?.id,
        premium: authState.isPremium,
        error: authState.error
      })
      console.log('Spotify auth status sent to bridge:', authState.isAuthenticated, authState.isPremium)
    }
  }

  const contextValue: SpotifyContextType = {
    sdk,
    authState,
    authenticate,
    logout,
    checkPremiumStatus: () => checkPremiumStatus()
  }

  return (
    <SpotifyContext.Provider value={contextValue}>
      {children}
    </SpotifyContext.Provider>
  )
}