#!/usr/bin/env python3
"""
Test script for ElevenLabs API using direct REST calls
"""

import os
import requests
import json
from dotenv import load_dotenv
import sys

# Force reload environment variables
os.environ.clear()
load_dotenv('.env', override=True)
if os.path.exists('env.visible'):
    load_dotenv('env.visible', override=True)

# Get API key
api_key = os.getenv("ELEVENLABS_API_KEY")
if not api_key:
    print("Error: No ElevenLabs API key found")
    sys.exit(1)

# Trim any whitespace
api_key = api_key.strip()

print(f"API key: {api_key[:4]}...{api_key[-4:]}")
print(f"API key length: {len(api_key)}")

# First, test the user API to check if the API key is valid
url = "https://api.elevenlabs.io/v1/user"

headers = {
    "Accept": "application/json",
    "xi-api-key": api_key
}

try:
    print("Testing API key with user info endpoint...")
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print("API key is valid! User info retrieved successfully.")
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error: Status code {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Error: {str(e)}")

# Test if we can get available voices
try:
    print("\nTesting voices endpoint...")
    voices_url = "https://api.elevenlabs.io/v1/voices"
    voices_response = requests.get(voices_url, headers=headers)
    
    if voices_response.status_code == 200:
        print("Voices retrieved successfully!")
        voices = voices_response.json()
        print(f"Available voices: {len(voices.get('voices', []))}")
        # Print the first voice if available
        if voices.get('voices'):
            first_voice = voices.get('voices')[0]
            print(f"Sample voice: {first_voice.get('name')} (ID: {first_voice.get('voice_id')})")
    else:
        print(f"Error getting voices: Status code {voices_response.status_code}")
        print(voices_response.text)
except Exception as e:
    print(f"Error with voices request: {str(e)}")

# Test TTS with a short sample
try:
    print("\nTesting TTS endpoint...")
    tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{os.getenv('ELEVENLABS_VOICE_ID')}"
    
    tts_headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    
    tts_data = {
        "text": "BZZZT! Test successful! I'm DJ R3X!",
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.5
        }
    }
    
    print(f"Using voice ID: {os.getenv('ELEVENLABS_VOICE_ID')}")
    tts_response = requests.post(tts_url, json=tts_data, headers=tts_headers)
    
    if tts_response.status_code == 200:
        print("TTS generated successfully!")
        # Save to a file for testing
        with open("test_audio.mp3", "wb") as f:
            f.write(tts_response.content)
        print("Audio saved to test_audio.mp3")
    else:
        print(f"Error with TTS: Status code {tts_response.status_code}")
        print(tts_response.text)
except Exception as e:
    print(f"Error with TTS request: {str(e)}") 