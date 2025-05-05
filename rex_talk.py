#!/usr/bin/env python3
"""
DJ R3X Voice Assistant
A voice-first "mini-assistant" that listens, thinks, and speaks back in 
a DJ R3X-inspired voice from Star Wars.
"""

import os
import sys
import time
import json
import wave
import threading
import requests
import speech_recognition as sr
from pathlib import Path
import io
import pygame
from dotenv import load_dotenv, find_dotenv
from colorama import init, Fore, Style
from config.voice_settings import active_config as voice_config  # Voice settings
from config.voice_config import DISABLE_AUDIO_PROCESSING  # Only import the processing flag
from config.openai_settings import active_config as openai_config  # OpenAI settings
from config.app_settings import DEBUG_MODE, TEXT_ONLY_MODE, PUSH_TO_TALK_MODE  # Import app settings
from audio_processor import process_and_play_audio
import asyncio
from pydub import AudioSegment
import numpy as np
import sounddevice as sd
from openai import OpenAI
from whisper_manager import WhisperManager  # Add this import
from pynput import keyboard

# Initialize colorama
init()

# Initialize pygame for audio playback
pygame.mixer.init()

# Global variables for push-to-talk
space_pressed = False
recording_event = threading.Event()
is_recording = False  # New flag to track recording state

def on_press(key):
    """Handle key press events."""
    global space_pressed, is_recording
    try:
        if key == keyboard.Key.space:
            if not space_pressed:  # Only toggle if space wasn't already pressed
                space_pressed = True
                is_recording = not is_recording  # Toggle recording state
                if is_recording:
                    recording_event.set()
                else:
                    recording_event.clear()
    except AttributeError:
        pass

def on_release(key):
    """Handle key release events."""
    global space_pressed
    try:
        if key == keyboard.Key.space:
            space_pressed = False
    except AttributeError:
        pass

# Start keyboard listener
keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
keyboard_listener.start()

# Load environment variables from .env file
env_path = find_dotenv(filename='.env', raise_error_if_not_found=True)
load_dotenv(env_path)

# Print debug status
print(f"{Fore.YELLOW}Debug Mode is: {'enabled' if DEBUG_MODE else 'disabled'}{Style.RESET_ALL}")

# Only import debug utils after environment is loaded
from debug_utils import debug_timer, DebugTimer

# Legacy support for env.visible (deprecated)
if os.path.exists('env.visible') and not os.getenv("OPENAI_API_KEY"):
    print(f"{Fore.YELLOW}Warning: Using env.visible is deprecated. Please migrate to .env file.{Style.RESET_ALL}")
    load_dotenv('env.visible')

# API Keys and Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# System prompt loading logic
DJ_R3X_PERSONA_FILE = os.getenv("DJ_R3X_PERSONA_FILE")
if DJ_R3X_PERSONA_FILE and os.path.exists(DJ_R3X_PERSONA_FILE):
    with open(DJ_R3X_PERSONA_FILE, "r") as f:
        DJ_R3X_PERSONA = f.read()
else:
    DJ_R3X_PERSONA = os.getenv("DJ_R3X_PERSONA", 
        "You are DJ R3X, a droid DJ from Star Wars. You have an upbeat, quirky personality. "
        "keep responses brief and entertaining. You love music and Star Wars.")

# Validate configuration
@debug_timer
def validate_config():
    """Validate that all required configuration variables are set."""
    global ELEVENLABS_API_KEY
    
    missing_vars = []
    
    if not OPENAI_API_KEY:
        missing_vars.append("OPENAI_API_KEY")
    
    if not ELEVENLABS_API_KEY:
        missing_vars.append("ELEVENLABS_API_KEY")
    
    if not ELEVENLABS_VOICE_ID:
        missing_vars.append("ELEVENLABS_VOICE_ID")
    
    if missing_vars:
        print(f"{Fore.RED}Error: Missing required environment variables:{Style.RESET_ALL}")
        for var in missing_vars:
            print(f"  - {var}")
        print(f"\nPlease set these variables in your .env file or export them directly.")
        sys.exit(1)

    # Print the API key information for debugging
    print(f"{Fore.CYAN}ElevenLabs API key loaded (length: {len(ELEVENLABS_API_KEY)}){Style.RESET_ALL}")
    print(f"{Fore.CYAN}API key format: {ELEVENLABS_API_KEY[:3]}...{Style.RESET_ALL}")
    
    # Trim any whitespace from the API key
    clean_api_key = ELEVENLABS_API_KEY.strip()
    if clean_api_key != ELEVENLABS_API_KEY:
        print(f"{Fore.YELLOW}Warning: Whitespace found in API key, trimming...{Style.RESET_ALL}")
        ELEVENLABS_API_KEY = clean_api_key
        os.environ["ELEVENLABS_API_KEY"] = clean_api_key

# Initialize clients
@debug_timer
def initialize_clients():
    """Initialize API clients."""
    try:
        # Initialize OpenAI client with just the API key
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )
        recognizer = sr.Recognizer()
        
        # Initialize Whisper
        whisper_manager = WhisperManager(model_size="base")
        whisper_manager.load_model()  # Pre-load the model
        
        return openai_client, recognizer, whisper_manager
    except Exception as e:
        print(f"{Fore.RED}Error initializing clients: {str(e)}{Style.RESET_ALL}")
        sys.exit(1)

# Listen for speech
@debug_timer
def listen_for_speech(recognizer, whisper_manager):
    """Capture audio from microphone and convert to text using Whisper."""
    global is_recording
    try:
        with sr.Microphone() as source:
            if PUSH_TO_TALK_MODE:
                print(f"{Fore.CYAN}🎧 Press Space bar once to start recording, again to stop...{Style.RESET_ALL}")
                
                # Wait for space bar press to start
                recording_event.wait()
                
                print(f"{Fore.CYAN}🎤 Recording... (press Space bar to stop){Style.RESET_ALL}")
                
                # Start recording with a timeout
                try:
                    audio = recognizer.listen(source, timeout=30.0, phrase_time_limit=30.0)
                    is_recording = False  # Reset recording state after successful capture
                    recording_event.clear()  # Clear the event
                except sr.WaitTimeoutError:
                    print(f"{Fore.YELLOW}Recording timed out. Press Space bar to try again.{Style.RESET_ALL}")
                    is_recording = False
                    recording_event.clear()
                    return None
            
            print(f"{Fore.YELLOW}🔍 Processing speech...{Style.RESET_ALL}")
            
            # Convert audio data to numpy array for Whisper
            print(f"{Fore.YELLOW}Converting audio to format for Whisper...{Style.RESET_ALL}")
            audio_data = np.frombuffer(audio.get_raw_data(), dtype=np.int16)
            audio_float = audio_data.astype(np.float32) / 32768.0
            
            # Use Whisper for transcription
            print(f"{Fore.YELLOW}Starting Whisper transcription...{Style.RESET_ALL}")
            text = whisper_manager.transcribe(audio_float, sample_rate=audio.sample_rate)
            
            if not text:
                print(f"{Fore.RED}Warning: Whisper returned empty transcription{Style.RESET_ALL}")
                return None
                
            return text
            
    except sr.WaitTimeoutError:
        print(f"{Fore.YELLOW}No speech detected within timeout period.{Style.RESET_ALL}")
        return None
    except sr.UnknownValueError:
        print(f"{Fore.YELLOW}Sorry, I couldn't understand that.{Style.RESET_ALL}")
        return None
    except Exception as e:
        print(f"{Fore.RED}Unexpected error during speech recognition: {str(e)}{Style.RESET_ALL}")
        return None

# Generate AI response
@debug_timer
def generate_response(openai_client, user_input):
    """Generate a response using OpenAI's API."""
    try:
        print(f"{Fore.YELLOW}🤖 Thinking...{Style.RESET_ALL}")
        
        # Get OpenAI settings
        settings = openai_config
        
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": DJ_R3X_PERSONA},
                {"role": "user", "content": user_input}
            ],
            max_tokens=settings["max_tokens"],
            temperature=settings["temperature"],
            top_p=settings["top_p"],
            presence_penalty=settings["presence_penalty"],
            frequency_penalty=settings["frequency_penalty"]
        )
        
        return response.choices[0].message.content
    except Exception as e:
        print(f"{Fore.RED}Error generating response: {str(e)}{Style.RESET_ALL}")
        return "BZZZT! My circuits are a bit overloaded right now. Can you try again?"

# Text to speech using REST API
@debug_timer
def text_to_speech(text):
    """Convert text to speech using ElevenLabs TTS via REST API."""
    try:
        print(f"{Fore.YELLOW}🔊 Generating audio...{Style.RESET_ALL}")
        
        # Create a thread for audio generation to allow showing a spinner
        audio_thread = threading.Thread(target=generate_and_play_audio_rest, args=(text,))
        audio_thread.start()
        
        # Show a simple spinner while generating
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        i = 0
        while audio_thread.is_alive():
            sys.stdout.write(f"\r{Fore.CYAN}Generating audio {spinner[i % len(spinner)]}{Style.RESET_ALL}")
            sys.stdout.flush()
            time.sleep(0.1)
            i += 1
        
        sys.stdout.write("\r" + " " * 30 + "\r")
        sys.stdout.flush()
        
        return True
    except Exception as e:
        print(f"{Fore.RED}Error with text-to-speech: {str(e)}{Style.RESET_ALL}")
        return False

@debug_timer
def generate_and_play_audio_rest(text):
    """Generate and play audio using ElevenLabs REST API with audio processing."""
    try:
        # Define API request
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }
        
        # Get voice settings from config
        voice_settings = voice_config.to_dict()
        
        data = {
            "text": text,
            "model_id": voice_settings["model_id"],
            "voice_settings": {
                "stability": voice_settings["stability"],
                "similarity_boost": voice_settings["similarity_boost"],
                "style": voice_settings["style"],
                "speaker_boost": voice_settings["speaker_boost"],
                "speed": voice_settings["speed"]
            }
        }
        
        # Make API request
        print(f"{Fore.CYAN}Sending request to ElevenLabs...{Style.RESET_ALL}")
        response = requests.post(url, json=data, headers=headers)
        
        # Check if successful
        if response.status_code == 200:
            print(f"{Fore.GREEN}Successfully received audio from ElevenLabs{Style.RESET_ALL}")
            
            # If audio processing is disabled, play directly
            if DISABLE_AUDIO_PROCESSING:
                # Convert response content to numpy array for playback
                audio_segment = AudioSegment.from_mp3(io.BytesIO(response.content))
                samples = np.array(audio_segment.get_array_of_samples(), dtype=np.float32) / 32768.0
                
                # Play audio directly
                sd.play(samples, audio_segment.frame_rate)
                sd.wait()
                return
            
            # Otherwise proceed with normal processing flow
            os.makedirs('audio/elevenlabs_audio', exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            raw_audio_path = f'audio/elevenlabs_audio/raw_response_{timestamp}.mp3'
            with open(raw_audio_path, 'wb') as f:
                f.write(response.content)
            print(f"{Fore.CYAN}Saved raw audio to {raw_audio_path}{Style.RESET_ALL}")
            
            # Process and play audio using our pipeline
            print(f"{Fore.CYAN}Applying audio effects...{Style.RESET_ALL}")
            
            # Create event loop if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Process and play audio
            processed_path = raw_audio_path.replace('elevenlabs_audio', 'processed_audio')
            os.makedirs(os.path.dirname(processed_path), exist_ok=True)
            
            # Process and play audio, saving to processed_audio directory
            loop.run_until_complete(process_and_play_audio(raw_audio_path))
            
        else:
            print(f"{Fore.RED}Error from ElevenLabs API: Status code {response.status_code}{Style.RESET_ALL}")
            print(response.text)
            
    except Exception as e:
        print(f"{Fore.RED}Error generating or playing audio: {str(e)}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}API Key: {ELEVENLABS_API_KEY[:4]}...{ELEVENLABS_API_KEY[-4:]}{Style.RESET_ALL}")

# Main application loop
@debug_timer
def main():
    """Main application loop."""
    # Test our debug timer
    with DebugTimer("Startup Test"):
        time.sleep(1)  # Force a 1 second delay to test timing
        
    print(f"{Fore.GREEN}{'=' * 50}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}DJ R3X Voice Assistant{Style.RESET_ALL}")
    print(f"{Fore.GREEN}{'=' * 50}{Style.RESET_ALL}")
    
    # Print mode status
    print(f"{Fore.YELLOW}Text-only mode is: {'enabled' if TEXT_ONLY_MODE else 'disabled'}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Debug mode is: {'enabled' if DEBUG_MODE else 'disabled'}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}Push-to-talk mode is: {'enabled' if PUSH_TO_TALK_MODE else 'disabled'}{Style.RESET_ALL}")
    
    if TEXT_ONLY_MODE:
        print(f"{Fore.YELLOW}Running in TEXT ONLY MODE: Responses will be printed, not spoken.{Style.RESET_ALL}")
    
    if PUSH_TO_TALK_MODE:
        print(f"{Fore.YELLOW}Running in PUSH TO TALK MODE: Press Space bar to toggle recording.{Style.RESET_ALL}")
    
    # Show audio processing status
    if DISABLE_AUDIO_PROCESSING:
        print(f"{Fore.YELLOW}Audio processing is DISABLED. Using raw ElevenLabs audio.{Style.RESET_ALL}")
    
    # Print current voice settings
    print(f"\n{Fore.CYAN}Current Voice Settings:{Style.RESET_ALL}")
    settings_dict = voice_config.to_dict()
    for key, value in settings_dict.items():
        print(f"  {key}: {value}")
    print()
    
    # Play startup notification sound
    try:
        startup_sound = pygame.mixer.Sound("audio/startours_audio/startours_ding_short.mp3")
        startup_sound.play()
        time.sleep(0.5)  # Give a short time for the sound to play
    except Exception as e:
        print(f"{Fore.YELLOW}Could not play startup sound: {str(e)}{Style.RESET_ALL}")
    
    # Validate configuration and initialize clients
    validate_config()
    openai_client, recognizer, whisper_manager = initialize_clients()
    
    # Main loop
    while True:
        try:
            # Only prompt for Enter if not in push-to-talk mode
            if not PUSH_TO_TALK_MODE:
                input(f"{Fore.CYAN}🎧 Press {Fore.GREEN}ENTER{Fore.CYAN} to talk...{Style.RESET_ALL}")
            
            # Start listening for speech
            user_input = listen_for_speech(recognizer, whisper_manager)
            
            # Only process if we got valid input
            if user_input:
                # Print what the user said
                print(f"{Fore.GREEN}You: {user_input}{Style.RESET_ALL}")
                # Generate response
                ai_response = generate_response(openai_client, user_input)
                # Print the response
                print(f"{Fore.MAGENTA}R3X: {ai_response}{Style.RESET_ALL}")
                # If not in text only mode, convert to speech
                if not TEXT_ONLY_MODE:
                    text_to_speech(ai_response)
                else:
                    print(f"{Fore.YELLOW}Skipping speech synthesis (TEXT_ONLY_MODE is enabled){Style.RESET_ALL}")
            
            print()  # Add a newline for better readability
            
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Exiting DJ R3X Voice Assistant. WOOP! See you next time!{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"{Fore.RED}Unexpected error: {str(e)}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Continuing...{Style.RESET_ALL}")

if __name__ == "__main__":
    main() 