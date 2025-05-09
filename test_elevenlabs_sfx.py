#!/usr/bin/env python3
"""
Test script for ElevenLabs Sound Effects API latency
"""

import os
import time
import requests
import subprocess
from dotenv import load_dotenv
import platform

# Load environment variables
load_dotenv()
if os.path.exists('env.visible'):
    load_dotenv('env.visible')

# Get API key
api_key = os.getenv("ELEVENLABS_API_KEY")
if not api_key:
    print("Error: No ElevenLabs API key found")
    exit(1)

# Trim any whitespace from API key
api_key = api_key.strip()

def play_audio_file(file_path):
    """Play audio file using system commands"""
    if platform.system() == "Darwin":  # macOS
        subprocess.run(["afplay", file_path])
    elif platform.system() == "Linux":
        subprocess.run(["aplay", file_path])
    elif platform.system() == "Windows":
        from playsound import playsound
        playsound(file_path)

def test_sfx_latency(prompt, duration=None):
    """Test latency of generating and playing a sound effect"""
    print(f"\nTesting SFX generation for: '{prompt}'")
    
    # Prepare the API request
    url = "https://api.elevenlabs.io/v1/sound-generation"
    
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    
    data = {
        "text": prompt
    }
    
    if duration is not None:
        data["duration_seconds"] = duration
    
    # Measure generation time
    start_time = time.perf_counter()
    
    try:
        # Make the API request
        print("Sending request to ElevenLabs...")
        response = requests.post(url, json=data, headers=headers)
        
        if response.status_code == 200:
            # Calculate generation time
            generation_time = time.perf_counter() - start_time
            print(f"Generation time: {generation_time:.2f} seconds")
            
            # Get audio size
            audio_size = len(response.content)
            print(f"Audio size: {audio_size/1024:.2f} KB")
            
            # Save the audio
            output_file = "test_sfx.mp3"
            with open(output_file, "wb") as f:
                f.write(response.content)
            
            # Measure playback start time
            print("\nStarting playback...")
            playback_start = time.perf_counter()
            
            # Play the audio
            play_audio_file(output_file)
            
            # Calculate total time including playback initialization
            playback_init_time = time.perf_counter() - playback_start
            print(f"Playback initialization time: {playback_init_time:.2f} seconds")
            print(f"Total latency (generation + playback init): {(generation_time + playback_init_time):.2f} seconds")
            
            # Clean up
            os.remove(output_file)
            
        else:
            print(f"Error: Status code {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    # Test a variety of sound effects
    test_cases = [
        ("Glass shattering on concrete", None),
        ("Laser beam firing", 0.5),  # Short duration
        ("Thunder rumbling in the distance", 2.0),  # Longer duration
        ("Footsteps on gravel, then a metallic door opens", None),  # Complex sequence
        ("90s hip-hop drum loop, 90 BPM", 1.0)  # Musical element
    ]
    
    for prompt, duration in test_cases:
        test_sfx_latency(prompt, duration)
        time.sleep(1)  # Wait between tests 