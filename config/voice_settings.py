"""
DJ R3X Voice Settings

This file contains the voice configuration settings for DJ R3X.
Edit these values to adjust the voice characteristics.
"""

# Voice Personality Settings
VOICE_SETTINGS = {
    # Stability (0.0 to 1.0)
    # - Lower values: More emotional range, varied performance
    # - Higher values: More stable, consistent voice
    "stability": 0.3,

    # Similarity Boost (0.0 to 1.0)
    # - Lower values: More freedom to interpret the voice
    # - Higher values: Closer to the original voice
    "similarity_boost": 0.7,

    # Style Exaggeration (0.0 to 1.0)
    # - 0.0: No style exaggeration (recommended)
    # - Higher values: More pronounced style, but may affect stability
    "style_exaggeration": 0.8,

    # Speaker Boost
    # - False: Normal processing
    # - True: Enhanced speaker similarity (may increase latency)
    "speaker_boost": False,

    # Speed (0.7 to 1.2)
    # - 1.0: Default speed
    # - <1.0: Slower speech
    # - >1.0: Faster speech
    "speed": 1.1,  # Slightly faster for DJ R3X's energetic personality

    # Model Selection
    # - "eleven_multilingual_v2": Most lifelike, supports 29 languages
    # - "eleven_monolingual_v1": Original model
    # - "eleven_flash_v2.5": Faster, supports 32 languages
    "model": "eleven_multilingual_v2"
}

# Create the configuration from these settings
from config.voice_config import VoiceConfig

# This is the configuration that will be used by the application
active_config = VoiceConfig.create_custom(**VOICE_SETTINGS) 