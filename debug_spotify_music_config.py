#!/usr/bin/env python3
"""
Debug script to check Spotify configuration for MusicSourceManagerService
"""

import os
from dotenv import load_dotenv

# Load environment variables from the correct path, override existing
load_dotenv("/Users/brandoncullum/djr3x_voice/.env", override=True)

print("=== Spotify Configuration Debug ===")
print()

# Check environment variables
spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
spotify_redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "")
enable_spotify = os.getenv("ENABLE_SPOTIFY", "false").lower() == "true"

print("Environment Variables:")
print(f"  SPOTIFY_CLIENT_ID: {'✅ SET' if spotify_client_id else '❌ MISSING'} ({len(spotify_client_id)} chars)")
print(f"  SPOTIFY_CLIENT_SECRET: {'✅ SET' if spotify_client_secret else '❌ MISSING'} ({len(spotify_client_secret)} chars)")
print(f"  SPOTIFY_REDIRECT_URI: {'✅ SET' if spotify_redirect_uri else '❌ MISSING'} ({spotify_redirect_uri})")
print(f"  ENABLE_SPOTIFY: {'✅ TRUE' if enable_spotify else '❌ FALSE'} ({os.getenv('ENABLE_SPOTIFY', 'not set')})")
print()

# Check if Spotify provider import works
print("Spotify Provider Import Check:")
try:
    from cantina_os.services.music_source_manager_service.providers.spotify_music_provider import SpotifyMusicProvider
    print("  ✅ SpotifyMusicProvider import: SUCCESS")
    SPOTIFY_AVAILABLE = True
except ImportError as e:
    print(f"  ❌ SpotifyMusicProvider import: FAILED - {e}")
    SPOTIFY_AVAILABLE = False

print()

# Simulate the configuration logic from main.py
print("Configuration Logic Simulation:")
if enable_spotify and spotify_client_id and spotify_client_secret:
    spotify_config = {
        "client_id": spotify_client_id,
        "client_secret": spotify_client_secret,
        "redirect_uri": spotify_redirect_uri,
        "cache_directory": ".spotify_cache",
        "search_limit": 50,
        "library_cache_minutes": 30
    }
    print("  ✅ Spotify config would be created:")
    for key, value in spotify_config.items():
        if 'secret' in key.lower():
            print(f"    {key}: {value[:5]}...{value[-5:] if len(value) > 10 else ''}")
        else:
            print(f"    {key}: {value}")
else:
    spotify_config = None
    print("  ❌ Spotify config would be None")
    print("  Reasons:")
    if not enable_spotify:
        print("    - ENABLE_SPOTIFY is False")
    if not spotify_client_id:
        print("    - SPOTIFY_CLIENT_ID is missing")
    if not spotify_client_secret:
        print("    - SPOTIFY_CLIENT_SECRET is missing")

print()

# Final assessment
print("Final Assessment:")
if SPOTIFY_AVAILABLE and spotify_config and enable_spotify:
    print("  ✅ Spotify provider SHOULD be registered and initialized")
else:
    print("  ❌ Spotify provider will NOT be registered")
    if not SPOTIFY_AVAILABLE:
        print("    - Spotify provider import failed")
    if not spotify_config:
        print("    - Spotify configuration incomplete")
    if not enable_spotify:
        print("    - Spotify integration disabled")

print()
print("=== End Debug ===")