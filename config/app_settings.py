"""
Application-wide settings that are not sensitive.
These settings can be version controlled and shared across environments.
"""

# Operation Modes
TEXT_ONLY_MODE = False
DISABLE_AUDIO_PROCESSING = True
DEBUG_MODE = True
PUSH_TO_TALK_MODE = True  # When True, audio recording only happens while space bar is held down

# Audio Processing Settings
SAMPLE_RATE = 44100
CHANNELS = 1

# LED Settings
LED_SERIAL_PORT = "/dev/tty.usbmodem833301"  # Default port for macOS
LED_BAUD_RATE = 115200
DISABLE_EYES = False

# Debug Settings
DEBUG_TIMING_THRESHOLD = 0.0  # Show all timing data
DEBUG_MEMORY_TRACKING = True

# File Paths
AUDIO_OUTPUT_DIR = "audio/processed_audio"
RAW_AUDIO_DIR = "audio/elevenlabs_audio"
STARTUP_SOUND = "audio/startours_audio/startours_ding.mp3"  # Sound played when application starts

# Load any environment-specific overrides
import os
from dotenv import load_dotenv

load_dotenv()

# Allow environment variables to override these settings only if they exist
if 'TEXT_ONLY_MODE' in os.environ:
    TEXT_ONLY_MODE = os.getenv('TEXT_ONLY_MODE').lower() == 'true'
if 'DISABLE_AUDIO_PROCESSING' in os.environ:
    DISABLE_AUDIO_PROCESSING = os.getenv('DISABLE_AUDIO_PROCESSING').lower() == 'true'
if 'DEBUG_MODE' in os.environ:
    DEBUG_MODE = os.getenv('DEBUG_MODE').lower() == 'true'
if 'PUSH_TO_TALK_MODE' in os.environ:
    PUSH_TO_TALK_MODE = os.getenv('PUSH_TO_TALK_MODE').lower() == 'true'
    
# LED settings overrides
if 'LED_SERIAL_PORT' in os.environ:
    LED_SERIAL_PORT = os.getenv('LED_SERIAL_PORT')
if 'LED_BAUD_RATE' in os.environ:
    LED_BAUD_RATE = int(os.getenv('LED_BAUD_RATE'))
if 'DISABLE_EYES' in os.environ:
    DISABLE_EYES = os.getenv('DISABLE_EYES').lower() == 'true' 