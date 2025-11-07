#!/usr/bin/env python3
"""
Wrapper script to run the DJ-R3X voice assistant
"""

import os
import subprocess
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Check if API key is available
elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
if not elevenlabs_key:
    print("Error: ELEVENLABS_API_KEY not found in .env file")
    sys.exit(1)

# Print confirmation with masked key
print(f"Using ElevenLabs API key: {elevenlabs_key[:4]}...{elevenlabs_key[-4:]}")

# Import configuration from app_settings
from config.app_settings import (
    TEXT_ONLY_MODE, 
    DISABLE_AUDIO_PROCESSING, 
    DEBUG_MODE, 
    PUSH_TO_TALK_MODE,
    SAMPLE_RATE,
    CHANNELS
)

# Set configuration as environment variables
os.environ["TEXT_ONLY_MODE"] = str(TEXT_ONLY_MODE).lower()
os.environ["DISABLE_AUDIO_PROCESSING"] = str(DISABLE_AUDIO_PROCESSING).lower()
os.environ["DEBUG_MODE"] = str(DEBUG_MODE).lower()
os.environ["PUSH_TO_TALK_MODE"] = str(PUSH_TO_TALK_MODE).lower()
os.environ["SAMPLE_RATE"] = str(SAMPLE_RATE)
os.environ["CHANNELS"] = str(CHANNELS)

# Set environment variable to disable eye integration
os.environ["DISABLE_EYES"] = "true"
print("Eye integration disabled for this session")

# Print configuration status
print("\nRunning with configuration:")
print(f"- Text Only Mode: {TEXT_ONLY_MODE}")
print(f"- Disable Audio Processing: {DISABLE_AUDIO_PROCESSING}")
print(f"- Debug Mode: {DEBUG_MODE}")
print(f"- Push to Talk Mode: {PUSH_TO_TALK_MODE}")
print(f"- Sample Rate: {SAMPLE_RATE} Hz")
print(f"- Channels: {CHANNELS}")
print()

# Run the DJ-R3X script with the environment variables set
subprocess.run([sys.executable, "rex_talk.py"]) 