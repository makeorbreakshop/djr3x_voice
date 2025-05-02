#!/usr/bin/env python3
"""
Test script for ElevenLabs API
"""

import os
from dotenv import load_dotenv
from elevenlabs import generate, play, set_api_key
import sys

# Load environment variables
load_dotenv()
if os.path.exists('env.visible'):
    load_dotenv('env.visible')

# Get API key
api_key = os.getenv("ELEVENLABS_API_KEY")
if not api_key:
    print("Error: No ElevenLabs API key found")
    sys.exit(1)

print(f"API key: {api_key[:4]}...{api_key[-4:]}")
print(f"API key length: {len(api_key)}")

# Trim any whitespace
if api_key != api_key.strip():
    print("Warning: API key contains whitespace, trimming...")
    api_key = api_key.strip()
    print(f"New API key length: {len(api_key)}")

# Set API key
set_api_key(api_key)

try:
    # Generate a simple test
    print("Generating test audio...")
    audio = generate(
        text="BZZZT! Test successful! I'm DJ R3X!",
        voice=os.getenv("ELEVENLABS_VOICE_ID"),
        model="eleven_monolingual_v1"
    )
    
    # Play the audio
    print("Playing audio...")
    play(audio)
    print("Test completed successfully!")
    
except Exception as e:
    print(f"Error: {str(e)}")
    # Print the raw API key for debugging
    print(f"Raw API key: '{api_key}'") 