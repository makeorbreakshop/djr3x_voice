"""
Eleven Labs Voice Configuration
"""
from dataclasses import dataclass
from typing import Literal, Dict, Any
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

@dataclass
class VoiceConfig:
    # Voice Settings
    stability: float = 0.5         # Range: 0-1, Default: 0.5
    similarity_boost: float = 0.75  # Range: 0-1, Default: 0.75
    style_exaggeration: float = 0   # Range: 0-1, Default: 0
    speaker_boost: bool = False     # Default: False
    speed: float = 1.0             # Range: 0.7-1.2, Default: 1.0
    
    # Model Selection
    model: Literal["eleven_multilingual_v2", "eleven_monolingual_v1", "eleven_flash_v2.5"] = "eleven_multilingual_v2"
    
    # Model Specific Settings
    character_limit: int = 10000   # 10,000 for multilingual_v2, 40,000 for flash_v2.5

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary format for API calls"""
        return {
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style_exaggeration,
            "speaker_boost": self.speaker_boost,
            "model_id": self.model,
            "speed": self.speed
        }

    @classmethod
    def create_custom(cls, **kwargs) -> 'VoiceConfig':
        """Create a custom configuration with validation"""
        config = cls()
        
        for key, value in kwargs.items():
            if hasattr(config, key):
                # Validate ranges for float values
                if isinstance(value, (int, float)):
                    if key in ['stability', 'similarity_boost', 'style_exaggeration']:
                        value = float(max(0.0, min(1.0, value)))
                    elif key == 'speed':
                        value = float(max(0.7, min(1.2, value)))
                setattr(config, key, value)
        
        # Update character limit based on model
        config.character_limit = 40000 if config.model == "eleven_flash_v2.5" else 10000
        
        return config

# Get audio processing settings from environment
DISABLE_AUDIO_PROCESSING = os.getenv("DISABLE_AUDIO_PROCESSING", "false").lower() == "true"

# Default configuration instance
default_config = VoiceConfig()

# Example usage:
"""
from config.voice_config import VoiceConfig

# Create custom config
custom_config = VoiceConfig.create_custom(
    stability=0.45,
    similarity_boost=0.85,
    speed=1.1,
    model="eleven_flash_v2.5"
)

# Get settings for API call
api_settings = custom_config.to_dict()
""" 