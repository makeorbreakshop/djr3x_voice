#!/usr/bin/env python
"""
Debug script to test Spotify configuration in MusicSourceManagerService
"""

import os
import sys
import asyncio
from dotenv import load_dotenv
from pyee.asyncio import AsyncIOEventEmitter

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Load environment variables from project root directory
env_path = os.path.join(os.path.dirname(__file__), '.env')
print(f"Loading .env from: {env_path}")
load_result = load_dotenv(env_path, verbose=True)
print(f"load_dotenv result: {load_result}")

# Import the service
from cantina_os.cantina_os.services.music_source_manager_service.music_source_manager_service import MusicSourceManagerService

async def test_spotify_config():
    """Test Spotify configuration"""
    
    # Check environment variables
    print("\n=== Environment Variables ===")
    print(f"ENABLE_SPOTIFY: {os.getenv('ENABLE_SPOTIFY')}")
    print(f"SPOTIFY_CLIENT_ID: {bool(os.getenv('SPOTIFY_CLIENT_ID'))}")
    print(f"SPOTIFY_CLIENT_SECRET: {bool(os.getenv('SPOTIFY_CLIENT_SECRET'))}")
    
    # Test config building (simulating main.py logic)
    print("\n=== Configuration Building ===")
    config = {
        "ENABLE_SPOTIFY": os.getenv("ENABLE_SPOTIFY", "false").lower() == "true",
        "SPOTIFY_CLIENT_ID": os.getenv("SPOTIFY_CLIENT_ID", ""),
        "SPOTIFY_CLIENT_SECRET": os.getenv("SPOTIFY_CLIENT_SECRET", ""),
        "SPOTIFY_REDIRECT_URI": os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback"),
        "MUSIC_MAX_SEARCH_RESULTS": int(os.getenv("MUSIC_MAX_SEARCH_RESULTS", "50")),
        "MUSIC_DEFAULT_PROVIDER": os.getenv("MUSIC_DEFAULT_PROVIDER", "local"),
        "MUSIC_PROVIDER_TIMEOUT": int(os.getenv("MUSIC_PROVIDER_TIMEOUT", "30")),
        "MUSIC_MAX_RETRIES": int(os.getenv("MUSIC_MAX_RETRIES", "3")),
        "MUSIC_HEALTH_CHECK_INTERVAL": int(os.getenv("MUSIC_HEALTH_CHECK_INTERVAL", "300")),
        "MUSIC_SEARCH_ALL_PROVIDERS": os.getenv("MUSIC_SEARCH_ALL_PROVIDERS", "true").lower() == "true",
    }
    
    print(f"Config ENABLE_SPOTIFY: {config['ENABLE_SPOTIFY']}")
    print(f"Config SPOTIFY_CLIENT_ID present: {bool(config['SPOTIFY_CLIENT_ID'])}")
    print(f"Config SPOTIFY_CLIENT_SECRET present: {bool(config['SPOTIFY_CLIENT_SECRET'])}")
    
    # Build service config like main.py does
    music_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio", "music")
    os.makedirs(music_dir, exist_ok=True)
    
    spotify_config = None
    if config.get("ENABLE_SPOTIFY") and config.get("SPOTIFY_CLIENT_ID") and config.get("SPOTIFY_CLIENT_SECRET"):
        spotify_config = {
            "client_id": config.get("SPOTIFY_CLIENT_ID"),
            "client_secret": config.get("SPOTIFY_CLIENT_SECRET"),
            "redirect_uri": config.get("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback"),
            "cache_directory": ".spotify_cache",
            "search_limit": config.get("MUSIC_MAX_SEARCH_RESULTS", 50),
            "library_cache_minutes": 30
        }
        print(f"\n=== Spotify Config Built ===")
        print(f"spotify_config: {spotify_config}")
    else:
        print(f"\n=== Spotify Config NOT Built ===")
        print(f"ENABLE_SPOTIFY: {config.get('ENABLE_SPOTIFY')}")
        print(f"SPOTIFY_CLIENT_ID: {bool(config.get('SPOTIFY_CLIENT_ID'))}")
        print(f"SPOTIFY_CLIENT_SECRET: {bool(config.get('SPOTIFY_CLIENT_SECRET'))}")
    
    service_config = {
        "default_provider": config.get("MUSIC_DEFAULT_PROVIDER", "local"),
        "enable_spotify": config.get("ENABLE_SPOTIFY", False),
        "spotify_config": spotify_config,
        "fallback_enabled": True,
        "local_music_directory": music_dir,
        "provider_timeout": int(config.get("MUSIC_PROVIDER_TIMEOUT", "30")),
        "max_retries": int(config.get("MUSIC_MAX_RETRIES", "3")),
        "health_check_interval": int(config.get("MUSIC_HEALTH_CHECK_INTERVAL", "300")),
        "search_all_providers": config.get("MUSIC_SEARCH_ALL_PROVIDERS", "true").lower() == "true",
        "max_search_results": int(config.get("MUSIC_MAX_SEARCH_RESULTS", "50"))
    }
    
    print(f"\n=== Service Config ===")
    print(f"enable_spotify: {service_config['enable_spotify']}")
    print(f"spotify_config: {service_config['spotify_config']}")
    
    # Test spotipy availability
    print(f"\n=== Spotipy Test ===")
    try:
        import spotipy
        print("spotipy is available")
    except ImportError as e:
        print(f"spotipy not available: {e}")
        return
    
    # Test service creation
    print(f"\n=== Service Creation Test ===")
    event_bus = AsyncIOEventEmitter()
    
    try:
        service = MusicSourceManagerService(
            event_bus=event_bus,
            config=service_config,
            name="music_source_manager_service"
        )
        print("Service created successfully")
        
        # Test provider registration
        print(f"\n=== Provider Registration Test ===")
        await service._register_providers()
        print(f"Registered providers: {list(service._provider_configs.keys())}")
        
        # Test provider initialization
        print(f"\n=== Provider Initialization Test ===")
        await service._initialize_providers()
        print(f"Initialized providers: {list(service._providers.keys())}")
        
        for name, provider in service._providers.items():
            print(f"Provider {name}: available={provider.is_available}")
        
    except Exception as e:
        print(f"Service creation/initialization failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_spotify_config())