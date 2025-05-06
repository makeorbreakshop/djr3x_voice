"""
Audio envelope analysis utilities for LED synchronization.
"""

import numpy as np
from typing import Union, Tuple

class AudioEnvelope:
    """
    Analyzes audio data to extract amplitude envelope for LED synchronization.
    Uses RMS (Root Mean Square) and peak detection to generate LED intensity values.
    """
    
    def __init__(self, 
                 window_size: int = 1024,
                 hop_length: int = 512,
                 smoothing: float = 0.8):
        """
        Initialize the envelope analyzer.
        
        Args:
            window_size: Size of the analysis window in samples
            hop_length: Number of samples between successive windows
            smoothing: Smoothing factor for the envelope (0-1)
        """
        self.window_size = window_size
        self.hop_length = hop_length
        self.smoothing = smoothing
        self._prev_level = 0.0
    
    def process_frame(self, audio_data: np.ndarray) -> int:
        """
        Process a single frame of audio data and return LED intensity.
        
        Args:
            audio_data: Audio samples as numpy array (-1.0 to 1.0)
            
        Returns:
            int: LED intensity value (0-255)
        """
        # Calculate RMS
        rms = np.sqrt(np.mean(np.square(audio_data)))
        
        # Find peak in this window
        peak = np.max(np.abs(audio_data))
        
        # Combine RMS and peak with more weight on RMS
        raw_level = (0.7 * rms) + (0.3 * peak)
        
        # Apply smoothing
        smoothed = (self.smoothing * self._prev_level) + ((1 - self.smoothing) * raw_level)
        self._prev_level = smoothed
        
        # Scale to 0-255 range for LED intensity
        # Using a log scale for better visual dynamics
        if smoothed > 0:
            intensity = int(255 * (1 + np.log10(smoothed)) / 2)
        else:
            intensity = 0
            
        return max(0, min(255, intensity))
    
    def process_audio(self, audio_data: np.ndarray, sample_rate: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Process a complete audio buffer and return envelope and timestamps.
        
        Args:
            audio_data: Complete audio buffer as numpy array
            sample_rate: Audio sample rate in Hz
            
        Returns:
            Tuple[np.ndarray, np.ndarray]: (envelope values, timestamps in seconds)
        """
        # Ensure audio is mono
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)
        
        # Calculate number of frames
        num_frames = 1 + (len(audio_data) - self.window_size) // self.hop_length
        envelope = np.zeros(num_frames)
        
        # Process each frame
        for i in range(num_frames):
            start = i * self.hop_length
            end = start + self.window_size
            frame = audio_data[start:end]
            envelope[i] = self.process_frame(frame)
        
        # Generate timestamps
        times = np.arange(num_frames) * (self.hop_length / sample_rate)
        
        return envelope, times
    
    def reset(self) -> None:
        """Reset the envelope follower state."""
        self._prev_level = 0.0 