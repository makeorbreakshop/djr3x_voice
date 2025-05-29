"""
Command Functions for LLM Integration

This module defines the function specifications that can be called by the LLM
through OpenAI's function calling capability. These functions represent
actions that DJ R3X can perform based on voice commands.

Each function is defined using Pydantic models for type safety and validation.
"""

from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field

class PlayMusicParams(BaseModel):
    """Parameters for playing music."""
    track: str = Field(
        ..., 
        description="The song or music track to play. Can be a specific song name or a general genre."
    )

class StopMusicParams(BaseModel):
    """Parameters for stopping music."""
    pass  # No parameters needed

class SetEyeColorParams(BaseModel):
    """Parameters for setting eye color."""
    color: str = Field(
        ..., 
        description="The color for the eyes. Can be a basic color name like 'red', 'blue', 'green', etc."
    )
    pattern: Optional[str] = Field(
        None, 
        description="Optional LED pattern to display. Defaults to 'solid' if not specified."
    )
    intensity: Optional[float] = Field(
        None, 
        description="Brightness level from 0.0 to 1.0. Defaults to 1.0 if not specified.",
        ge=0.0,
        le=1.0
    )

class FunctionDefinition(BaseModel):
    """Model for OpenAI function definition structure."""
    type: str = "function"
    function: Dict[str, Any]

# Define the function specifications
def create_play_music_function() -> Dict[str, Any]:
    """Create the play_music function definition for OpenAI."""
    return {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": "Play a specific song or music genre",
            "parameters": PlayMusicParams.schema()
        }
    }

def create_stop_music_function() -> Dict[str, Any]:
    """Create the stop_music function definition for OpenAI."""
    return {
        "type": "function",
        "function": {
            "name": "stop_music",
            "description": "Stop the currently playing music",
            "parameters": StopMusicParams.schema()
        }
    }

def create_set_eye_color_function() -> Dict[str, Any]:
    """Create the set_eye_color function definition for OpenAI."""
    return {
        "type": "function",
        "function": {
            "name": "set_eye_color",
            "description": "Change the color of DJ R3X's LED eyes",
            "parameters": SetEyeColorParams.schema()
        }
    }

# Collection of all available functions
AVAILABLE_FUNCTIONS = [
    create_play_music_function(),
    create_stop_music_function(),
    create_set_eye_color_function()
]

def get_all_function_definitions() -> List[Dict[str, Any]]:
    """Get all function definitions in OpenAI tool format."""
    return AVAILABLE_FUNCTIONS

def function_name_to_model_map() -> Dict[str, Any]:
    """Get a mapping of function names to their parameter models for validation."""
    return {
        "play_music": PlayMusicParams,
        "stop_music": StopMusicParams,
        "set_eye_color": SetEyeColorParams
    } 