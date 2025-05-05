import whisper
import numpy as np
import torch
from pathlib import Path
import time
from colorama import Fore, Style
import scipy.signal as signal

class WhisperManager:
    def __init__(self, model_size: str = "small"):
        """Initialize Whisper manager with specified model size.
        
        Args:
            model_size: Size of Whisper model to use ("tiny", "base", "small", "medium", "large")
        """
        self.model = None
        self.model_size = model_size
        self.device = "cpu"  # Using CPU since MPS doesn't fully support Whisper's operations
        print(f"{Fore.CYAN}Initializing Whisper on {self.device}{Style.RESET_ALL}")
        
    def load_model(self):
        """Load the Whisper model if not already loaded."""
        if self.model is None:
            print(f"{Fore.YELLOW}Loading Whisper {self.model_size} model...{Style.RESET_ALL}")
            load_start = time.time()
            self.model = whisper.load_model(self.model_size, device=self.device)
            load_time = time.time() - load_start
            print(f"{Fore.GREEN}Whisper model loaded in {load_time:.2f}s{Style.RESET_ALL}")
    
    def preprocess_audio(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """Preprocess audio data for optimal Whisper performance."""
        # Print audio stats
        print(f"{Fore.CYAN}Audio stats before preprocessing:{Style.RESET_ALL}")
        print(f"  Shape: {audio_data.shape}")
        print(f"  Sample rate: {sample_rate}Hz")
        print(f"  Max amplitude: {np.abs(audio_data).max()}")
        print(f"  RMS: {np.sqrt(np.mean(audio_data**2))}")
        
        # Convert to mono if stereo
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)
        
        # Resample to 16kHz (Whisper's expected rate)
        if sample_rate != 16000:
            print(f"{Fore.YELLOW}Resampling from {sample_rate}Hz to 16000Hz{Style.RESET_ALL}")
            audio_data = signal.resample_poly(audio_data, 16000, sample_rate)
        
        # Normalize audio (robust normalization)
        percentile = np.percentile(np.abs(audio_data), 95)
        if percentile > 0:
            audio_data = audio_data / percentile
            audio_data = np.clip(audio_data, -1, 1)
        
        # Convert to float32
        audio_data = audio_data.astype(np.float32)
        
        print(f"{Fore.CYAN}Audio stats after preprocessing:{Style.RESET_ALL}")
        print(f"  Shape: {audio_data.shape}")
        print(f"  Max amplitude: {np.abs(audio_data).max()}")
        print(f"  RMS: {np.sqrt(np.mean(audio_data**2))}")
        
        return audio_data
    
    def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe audio data to text using Whisper.
        
        Args:
            audio_data: Audio samples as numpy array
            sample_rate: Sample rate of the audio
            
        Returns:
            Transcribed text
        """
        # Load model if needed
        if self.model is None:
            self.load_model()
        
        # Preprocess audio
        print(f"{Fore.YELLOW}Preprocessing audio...{Style.RESET_ALL}")
        audio_data = self.preprocess_audio(audio_data, sample_rate)
        
        # Start timing
        transcribe_start = time.time()
        
        # Transcribe
        print(f"{Fore.YELLOW}Starting transcription...{Style.RESET_ALL}")
        result = self.model.transcribe(
            audio_data,
            language="en",
            task="transcribe",
            fp16=False  # Explicitly disable FP16 to avoid warning
        )
        
        transcribe_time = time.time() - transcribe_start
        print(f"{Fore.YELLOW}Whisper transcription took: {transcribe_time:.2f}s{Style.RESET_ALL}")
        
        # Add debug output for the transcribed text
        transcribed_text = result["text"].strip()
        print(f"{Fore.CYAN}Whisper heard: '{transcribed_text}'{Style.RESET_ALL}")
        
        return transcribed_text 