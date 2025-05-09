"""
Audio utility functions for CantinaOS.

Provides functions for playing audio files using platform-appropriate methods.
"""

import asyncio
import logging
import os
import platform
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

async def play_audio_file(
    file_path: str, 
    blocking: bool = False
) -> None:
    """
    Play an audio file using the appropriate method for the current platform.
    
    Args:
        file_path: Path to the audio file to play
        blocking: If True, wait for playback to complete before returning
        
    Raises:
        FileNotFoundError: If the audio file doesn't exist
        RuntimeError: If playback fails
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    
    logger.debug(f"Playing audio file: {file_path}")
    
    # First try using sounddevice if available
    try:
        import sounddevice as sd
        import soundfile as sf
        
        # Read audio file
        data, sample_rate = sf.read(file_path)
        
        if blocking:
            # Play audio synchronously
            sd.play(data, sample_rate)
            sd.wait()
            logger.debug("Audio playback completed (sounddevice)")
            return
        else:
            # Play audio asynchronously
            sd.play(data, sample_rate)
            logger.debug("Audio playback started (sounddevice)")
            return
            
    except ImportError:
        logger.debug("sounddevice not available, falling back to system commands")
    except Exception as e:
        logger.warning(f"sounddevice playback failed: {e}, falling back to system commands")
    
    # Fall back to system commands
    system = platform.system().lower()
    
    if system == "darwin":
        # macOS
        cmd = ["afplay", file_path]
    elif system == "linux":
        # Linux
        cmd = ["aplay", file_path]
    elif system == "windows":
        # Windows
        cmd = ["powershell", "-c", f"(New-Object Media.SoundPlayer '{file_path}').PlaySync()"]
    else:
        raise RuntimeError(f"Unsupported platform: {system}")
    
    try:
        if blocking:
            # Run command and wait for completion
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                stderr_text = stderr.decode().strip() if stderr else "No error output"
                logger.warning(f"Audio playback failed: {stderr_text}")
                
            logger.debug("Audio playback completed (system command)")
        else:
            # Start command and return immediately
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            logger.debug("Audio playback started (system command)")
    except Exception as e:
        raise RuntimeError(f"Failed to play audio: {e}") 