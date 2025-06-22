#\!/usr/bin/env python3
"""Debug script to check Spotify configuration values."""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

print("=== Environment Variables ===")
print(f"ENABLE_SPOTIFY: {repr(os.getenv('ENABLE_SPOTIFY'))}")
print(f"SPOTIFY_CLIENT_ID: {repr(os.getenv('SPOTIFY_CLIENT_ID'))}")
print(f"SPOTIFY_CLIENT_SECRET: {repr(os.getenv('SPOTIFY_CLIENT_SECRET'))}")
print(f"SPOTIFY_REDIRECT_URI: {repr(os.getenv('SPOTIFY_REDIRECT_URI'))}")

print("\n=== Processed Values ===")
enable_spotify = os.getenv("ENABLE_SPOTIFY", "false").lower() == "true"
client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")

print(f"enable_spotify (boolean): {enable_spotify}")
print(f"client_id: {repr(client_id)}")
print(f"client_secret: {repr(client_secret)}")
print(f"redirect_uri: {repr(redirect_uri)}")

print("\n=== Spotify Config Creation ===")
spotify_config = None
if enable_spotify and client_id and client_secret:
    spotify_config = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "cache_directory": ".spotify_cache",
        "search_limit": 50,
        "library_cache_minutes": 30
    }
    print("✅ Spotify config would be created:")
    print(f"  spotify_config: {spotify_config}")
else:
    print("❌ Spotify config would be None")
    print(f"  enable_spotify: {enable_spotify}")
    print(f"  client_id: {bool(client_id)}")
    print(f"  client_secret: {bool(client_secret)}")

print("\n=== Service Config ===")
service_config = {
    "enable_spotify": enable_spotify,
    "spotify_config": spotify_config,
}
print(f"service_config['enable_spotify']: {service_config['enable_spotify']}")
print(f"service_config['spotify_config']: {service_config['spotify_config']}")

print("\n=== Provider Registration Logic ===")
if service_config['enable_spotify'] and service_config['spotify_config']:
    print("✅ Spotify provider WOULD be registered")
else:
    print("❌ Spotify provider would NOT be registered")
EOF < /dev/null