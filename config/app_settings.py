"""
Application-wide settings that are not sensitive.
These settings can be version controlled and shared across environments.
"""

import os
from dotenv import load_dotenv

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

# Holocron RAG System Settings
HOLOCRON_TABLE_NAME = "holocron_knowledge"
HOLOCRON_SIMILARITY_THRESHOLD = 0.5
HOLOCRON_MAX_RESULTS = 3
HOLOCRON_ENABLED = True
HOLOCRON_CHUNK_SIZE = 1000
HOLOCRON_CHUNK_OVERLAP = 100
EMBEDDING_MODEL = "text-embedding-3-small"
SUPABASE_URL = "https://xkotscjkvejcgrweolsd.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Load any environment-specific overrides
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

# Holocron settings overrides
if 'HOLOCRON_TABLE_NAME' in os.environ:
    HOLOCRON_TABLE_NAME = os.getenv('HOLOCRON_TABLE_NAME')
if 'HOLOCRON_SIMILARITY_THRESHOLD' in os.environ:
    HOLOCRON_SIMILARITY_THRESHOLD = float(os.getenv('HOLOCRON_SIMILARITY_THRESHOLD'))
if 'HOLOCRON_MAX_RESULTS' in os.environ:
    HOLOCRON_MAX_RESULTS = int(os.getenv('HOLOCRON_MAX_RESULTS'))
if 'HOLOCRON_ENABLED' in os.environ:
    HOLOCRON_ENABLED = os.getenv('HOLOCRON_ENABLED').lower() == 'true' 

if 'HOLOCRON_CHUNK_SIZE' in os.environ:
    HOLOCRON_CHUNK_SIZE = int(os.getenv('HOLOCRON_CHUNK_SIZE'))
if 'HOLOCRON_CHUNK_OVERLAP' in os.environ:
    HOLOCRON_CHUNK_OVERLAP = int(os.getenv('HOLOCRON_CHUNK_OVERLAP'))
if 'EMBEDDING_MODEL' in os.environ:
    EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL')
if 'SUPABASE_URL' in os.environ:
    SUPABASE_URL = os.getenv('SUPABASE_URL')
if 'SUPABASE_KEY' in os.environ:
    SUPABASE_KEY = os.getenv('SUPABASE_KEY') 