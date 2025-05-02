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

# Run the DJ-R3X script with the environment variables set
subprocess.run([sys.executable, "rex_talk.py"]) 