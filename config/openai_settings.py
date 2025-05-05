"""
OpenAI Settings

This file contains the OpenAI configuration settings for DJ R3X.
Edit these values to adjust the AI response characteristics.
"""

# OpenAI Response Settings
OPENAI_SETTINGS = {
    # Maximum Tokens (50-250)
    # - Lower values: Shorter, more concise responses
    # - Higher values: Longer, more detailed responses
    "max_tokens": 75,  # Default shorter for DJ R3X's snappy style

    # Temperature (0.0 to 1.0)
    # - Lower values: More focused, consistent responses
    # - Higher values: More creative, varied responses
    "temperature": 0.7,

    # Top P (0.0 to 1.0)
    # - Controls response diversity
    # - Lower values: More focused on likely tokens
    "top_p": 0.9,

    # Presence Penalty (-2.0 to 2.0)
    # - Positive values encourage new topics
    # - Negative values encourage focusing on existing topics
    "presence_penalty": 0.2,

    # Frequency Penalty (-2.0 to 2.0)
    # - Positive values discourage repetition
    # - Negative values encourage repetition
    "frequency_penalty": 0.3
}

# Create the configuration class
class OpenAIConfig:
    @staticmethod
    def get_settings():
        """Get the OpenAI settings"""
        return OPENAI_SETTINGS.copy()

# This is the configuration that will be used by the application
active_config = OpenAIConfig.get_settings() 