'use client'

import React, { useState, useEffect, useCallback } from 'react'
import { useSpotifyContext } from '../../contexts/SpotifyContext'
import { MagnifyingGlassIcon, ClockIcon } from '@heroicons/react/24/outline'

// Define our own interface based on the Spotify API structure
export interface SpotifyTrack {
  id: string
  name: string
  artists: Array<{ id: string; name: string; uri: string }>
  album?: {
    id: string
    name: string
    images?: Array<{ url: string }>
  }
  duration_ms: number
  uri: string
  preview_url?: string | null
  popularity?: number
  explicit?: boolean
}

export interface SpotifyAlbum {
  id: string
  name: string
  artists: Array<{ id: string; name: string }>
  images?: Array<{ url: string }>
  total_tracks?: number
  release_date?: string
}

export interface SpotifyArtist {
  id: string
  name: string
  images?: Array<{ url: string }>
  followers?: { total: number }
  genres?: string[]
}

export interface SpotifyPlaylist {
  id: string
  name: string
  owner?: { display_name: string }
  images?: Array<{ url: string }>
  tracks?: { total: number }
}

export interface SpotifySearchResult {
  tracks: SpotifyTrack[]
  albums: SpotifyAlbum[]
  artists: SpotifyArtist[]
  playlists: SpotifyPlaylist[]
}

export interface SpotifySearchProps {
  onTrackSelect?: (track: SpotifyTrack) => void
  onSearchResults?: (results: SpotifySearchResult) => void
  className?: string
}

export const SpotifySearch: React.FC<SpotifySearchProps> = ({
  onTrackSelect,
  onSearchResults,
  className = ''
}) => {
  const { sdk, authState } = useSpotifyContext()
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState<SpotifySearchResult>({
    tracks: [],
    albums: [],
    artists: [],
    playlists: []
  })
  const [isSearching, setIsSearching] = useState(false)
  const [searchHistory, setSearchHistory] = useState<string[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [selectedCategory, setSelectedCategory] = useState<'tracks' | 'albums' | 'artists' | 'playlists'>('tracks')

  // Load search history from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem('spotify-search-history')
      if (saved) {
        setSearchHistory(JSON.parse(saved))
      }
    } catch (error) {
      console.error('Failed to load search history:', error)
    }
  }, [])

  // Save search history to localStorage
  const saveSearchHistory = useCallback((history: string[]) => {
    try {
      localStorage.setItem('spotify-search-history', JSON.stringify(history))
    } catch (error) {
      console.error('Failed to save search history:', error)
    }
  }, [])

  const performSearch = useCallback(async (query: string) => {
    if (!sdk || !authState.isAuthenticated || !query.trim()) return

    try {
      setIsSearching(true)

      const results = await sdk.search(
        query,
        ['track', 'album', 'artist', 'playlist'],
        'US',
        20 // Limit results to 20 per category
      )

      const searchResult: SpotifySearchResult = {
        tracks: results.tracks.items,
        albums: results.albums.items,
        artists: results.artists.items,
        playlists: results.playlists.items
      }

      setSearchResults(searchResult)
      onSearchResults?.(searchResult)

      // Add to search history if not already present
      if (query.trim() && !searchHistory.includes(query.trim())) {
        const newHistory = [query.trim(), ...searchHistory.slice(0, 9)] // Keep last 10 searches
        setSearchHistory(newHistory)
        saveSearchHistory(newHistory)
      }
    } catch (error) {
      console.error('Search failed:', error)
      setSearchResults({ tracks: [], albums: [], artists: [], playlists: [] })
    } finally {
      setIsSearching(false)
    }
  }, [sdk, authState.isAuthenticated, searchHistory, saveSearchHistory, onSearchResults])

  // Debounced search function
  useEffect(() => {
    if (!searchTerm.trim() || !sdk || !authState.isAuthenticated) {
      setSearchResults({ tracks: [], albums: [], artists: [], playlists: [] })
      return
    }

    const timeoutId = setTimeout(() => {
      performSearch(searchTerm)
    }, 300) // 300ms debounce

    return () => clearTimeout(timeoutId)
  }, [searchTerm, sdk, authState.isAuthenticated, performSearch])

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (searchTerm.trim()) {
      performSearch(searchTerm)
      setShowHistory(false)
    }
  }

  const handleHistorySelect = (term: string) => {
    setSearchTerm(term)
    setShowHistory(false)
    performSearch(term)
  }

  const clearSearchHistory = () => {
    setSearchHistory([])
    saveSearchHistory([])
  }

  const formatDuration = (ms: number): string => {
    const seconds = Math.floor(ms / 1000)
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
  }

  const getTrackQualityIndicator = (track: SpotifyTrack): string => {
    // For Premium users, most tracks are full songs
    if (authState.isPremium) {
      return '🎵 Full Song'
    }
    // For free users, they get 30-second previews
    return track.preview_url ? '🔍 Preview' : '🚫 No Preview'
  }

  if (!authState.isAuthenticated) {
    return (
      <div className={`text-center py-8 ${className}`}>
        <p className="text-sw-blue-300">Please authenticate with Spotify to search</p>
      </div>
    )
  }

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Search Input */}
      <form onSubmit={handleSearchSubmit} className="relative">
        <div className="relative">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-sw-blue-400" />
          <input
            type="text"
            placeholder="Search tracks, artists, albums, playlists..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            onFocus={() => setShowHistory(searchHistory.length > 0)}
            className="w-full pl-10 pr-4 py-2 bg-sw-dark-700 border border-sw-blue-600/30 rounded-lg text-sw-blue-100 placeholder-sw-blue-400 focus:outline-none focus:border-sw-blue-500 focus:ring-1 focus:ring-sw-blue-500"
          />
          {isSearching && (
            <div className="absolute right-3 top-1/2 transform -translate-y-1/2">
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-sw-blue-500"></div>
            </div>
          )}
        </div>

        {/* Search History Dropdown */}
        {showHistory && searchHistory.length > 0 && (
          <div className="absolute z-10 w-full mt-1 bg-sw-dark-800 border border-sw-blue-600/30 rounded-lg shadow-lg">
            <div className="p-2 border-b border-sw-blue-600/20">
              <div className="flex items-center justify-between">
                <span className="text-xs text-sw-blue-400 font-medium">Recent Searches</span>
                <button
                  type="button"
                  onClick={clearSearchHistory}
                  className="text-xs text-sw-blue-400 hover:text-sw-blue-300"
                >
                  Clear
                </button>
              </div>
            </div>
            <div className="max-h-48 overflow-y-auto">
              {searchHistory.map((term, index) => (
                <button
                  key={index}
                  type="button"
                  onClick={() => handleHistorySelect(term)}
                  className="w-full px-3 py-2 text-left text-sw-blue-200 hover:bg-sw-dark-700 flex items-center space-x-2"
                >
                  <ClockIcon className="h-3 w-3 text-sw-blue-400" />
                  <span className="truncate">{term}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </form>

      {/* Category Tabs */}
      {(searchResults.tracks.length > 0 || searchResults.albums.length > 0 || 
        searchResults.artists.length > 0 || searchResults.playlists.length > 0) && (
        <div className="flex space-x-1 bg-sw-dark-700/50 rounded-lg p-1">
          {[
            { key: 'tracks' as const, label: 'Tracks', count: searchResults.tracks.length },
            { key: 'albums' as const, label: 'Albums', count: searchResults.albums.length },
            { key: 'artists' as const, label: 'Artists', count: searchResults.artists.length },
            { key: 'playlists' as const, label: 'Playlists', count: searchResults.playlists.length }
          ].map(({ key, label, count }) => (
            <button
              key={key}
              onClick={() => setSelectedCategory(key)}
              className={`px-3 py-2 text-sm font-medium rounded-md transition-colors ${
                selectedCategory === key
                  ? 'bg-sw-blue-600 text-white'
                  : 'text-sw-blue-300 hover:text-sw-blue-100 hover:bg-sw-dark-600'
              }`}
            >
              {label} ({count})
            </button>
          ))}
        </div>
      )}

      {/* Search Results */}
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {selectedCategory === 'tracks' && searchResults.tracks.map((track) => (
          <div
            key={track.id}
            className="p-3 bg-sw-dark-700/30 border border-sw-blue-600/20 rounded-lg hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50 transition-all cursor-pointer"
            onClick={() => onTrackSelect?.(track)}
          >
            <div className="flex items-center space-x-3">
              {track.album?.images?.[2] && (
                <img
                  src={track.album.images[2].url}
                  alt={track.album.name}
                  className="w-12 h-12 rounded shadow-lg"
                />
              )}
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-sw-blue-100 truncate">{track.name}</h4>
                <p className="text-sm text-sw-blue-300 truncate">
                  {track.artists.map(artist => artist.name).join(', ')}
                </p>
                <p className="text-xs text-sw-blue-400 truncate">{track.album?.name}</p>
              </div>
              <div className="text-right flex-shrink-0">
                <p className="text-sm text-sw-blue-300">{formatDuration(track.duration_ms)}</p>
                <p className="text-xs text-sw-blue-400">{getTrackQualityIndicator(track)}</p>
              </div>
            </div>
          </div>
        ))}

        {selectedCategory === 'albums' && searchResults.albums.map((album) => (
          <div
            key={album.id}
            className="p-3 bg-sw-dark-700/30 border border-sw-blue-600/20 rounded-lg hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50 transition-all"
          >
            <div className="flex items-center space-x-3">
              {album.images?.[2] && (
                <img
                  src={album.images[2].url}
                  alt={album.name}
                  className="w-12 h-12 rounded shadow-lg"
                />
              )}
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-sw-blue-100 truncate">{album.name}</h4>
                <p className="text-sm text-sw-blue-300 truncate">
                  {album.artists.map(artist => artist.name).join(', ')}
                </p>
                <p className="text-xs text-sw-blue-400">
                  {album.total_tracks} tracks • {album.release_date?.split('-')[0]}
                </p>
              </div>
            </div>
          </div>
        ))}

        {selectedCategory === 'artists' && searchResults.artists.map((artist) => (
          <div
            key={artist.id}
            className="p-3 bg-sw-dark-700/30 border border-sw-blue-600/20 rounded-lg hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50 transition-all"
          >
            <div className="flex items-center space-x-3">
              {artist.images?.[2] && (
                <img
                  src={artist.images[2].url}
                  alt={artist.name}
                  className="w-12 h-12 rounded-full shadow-lg"
                />
              )}
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-sw-blue-100 truncate">{artist.name}</h4>
                <p className="text-sm text-sw-blue-300">
                  {artist.followers?.total.toLocaleString()} followers
                </p>
                <p className="text-xs text-sw-blue-400">
                  {artist.genres?.slice(0, 2).join(', ')}
                </p>
              </div>
            </div>
          </div>
        ))}

        {selectedCategory === 'playlists' && searchResults.playlists.map((playlist) => (
          <div
            key={playlist.id}
            className="p-3 bg-sw-dark-700/30 border border-sw-blue-600/20 rounded-lg hover:bg-sw-dark-700/50 hover:border-sw-blue-500/50 transition-all"
          >
            <div className="flex items-center space-x-3">
              {playlist.images?.[0] && (
                <img
                  src={playlist.images[0].url}
                  alt={playlist.name}
                  className="w-12 h-12 rounded shadow-lg"
                />
              )}
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-sw-blue-100 truncate">{playlist.name}</h4>
                <p className="text-sm text-sw-blue-300 truncate">
                  by {playlist.owner?.display_name}
                </p>
                <p className="text-xs text-sw-blue-400">
                  {playlist.tracks?.total} tracks
                </p>
              </div>
            </div>
          </div>
        ))}

        {/* No Results */}
        {searchTerm && !isSearching && 
         searchResults.tracks.length === 0 && 
         searchResults.albums.length === 0 && 
         searchResults.artists.length === 0 && 
         searchResults.playlists.length === 0 && (
          <div className="text-center py-8 text-sw-blue-300/50">
            No results found for &quot;{searchTerm}&quot;
          </div>
        )}

        {/* Empty State */}
        {!searchTerm && (
          <div className="text-center py-8 text-sw-blue-300/50">
            Start typing to search Spotify&apos;s library...
          </div>
        )}
      </div>
    </div>
  )
}