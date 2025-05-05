import json
import time
import asyncio
import numpy as np
from pathlib import Path
from typing import Union, Optional
from functools import wraps
import sounddevice as sd
from pydub import AudioSegment
from pedalboard import Pedalboard, Compressor, HighpassFilter, Gain, Distortion, Limiter
from scipy.signal import lfilter
from config.voice_config import DISABLE_AUDIO_PROCESSING
from colorama import Fore, Style
from debug_utils import debug_timer, DebugTimer  # Add debug timing utilities

@debug_timer
def load_config() -> dict:
    """Load audio effects configuration from JSON file."""
    config_path = Path("config/audio_effects.json")
    if not config_path.exists():
        raise FileNotFoundError("Audio effects configuration file not found")
    with open(config_path) as f:
        return json.load(f)

@debug_timer
def timing_decorator(func):
    """Decorator to measure and log processing time."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        processing_time = (time.perf_counter() - start_time) * 1000  # Convert to ms
        
        if processing_time > 20:  # Alert if over our target latency
            print(f"WARNING: Audio processing exceeded 20ms target: {processing_time:.2f}ms")
        return result
    return wrapper

@debug_timer
def create_comb_filter(delay_ms: float, feedback: float, sample_rate: int) -> tuple:
    """Create coefficients for a comb filter.
    
    Args:
        delay_ms: Delay time in milliseconds
        feedback: Feedback coefficient (0-1)
        sample_rate: Audio sample rate in Hz
    """
    delay_samples = int(sample_rate * delay_ms / 1000)  # Convert ms to samples using actual rate
    b = np.zeros(delay_samples + 1)
    b[0] = 1
    b[-1] = feedback
    a = [1]
    return b, a

@debug_timer
def apply_ring_modulation(audio: np.ndarray, frequency: float, sample_rate: int, mix: float = 1.0) -> np.ndarray:
    """Apply ring modulation effect to audio.
    
    Args:
        audio: Audio samples as numpy array
        frequency: Modulation frequency in Hz
        sample_rate: Sample rate in Hz
        mix: Mix level (0-1)
        
    Returns:
        Processed audio samples
    """
    # Create modulator signal (sine wave)
    duration = len(audio) / sample_rate
    t = np.linspace(0, duration, len(audio), endpoint=False)
    modulator = np.sin(2 * np.pi * frequency * t)
    
    # Apply modulation
    modulated = audio * modulator
    
    # Apply mix
    return audio * (1 - mix) + modulated * mix

@debug_timer
def apply_bit_crusher(audio: np.ndarray, bit_depth: int, mix: float = 1.0) -> np.ndarray:
    """Apply bit crusher effect to audio.
    
    Args:
        audio: Audio samples as numpy array
        bit_depth: Target bit depth (lower = more distortion)
        mix: Mix level (0-1)
        
    Returns:
        Processed audio samples
    """
    # Calculate step size for the target bit depth
    steps = 2 ** bit_depth
    step_size = 2.0 / steps
    
    # Quantize the signal
    crushed = np.round(audio / step_size) * step_size
    
    # Apply mix
    return audio * (1 - mix) + crushed * mix

class AudioProcessor:
    def __init__(self):
        self.config = load_config()
        self.setup_effects()
        
    @debug_timer
    def setup_effects(self):
        """Initialize the audio effects chain."""
        config = self.config
        
        # Create main effects chain with Pedalboard
        self.board = Pedalboard([
            HighpassFilter(cutoff_frequency_hz=config["eq"]["highpass_freq"]),
            Gain(gain_db=config["eq"]["presence_boost_gain"]),
            Compressor(
                threshold_db=config["compressor"]["threshold_db"],
                ratio=config["compressor"]["ratio"],
                attack_ms=config["compressor"]["attack_ms"],
                release_ms=config["compressor"]["release_ms"]
            ),
            Distortion(drive_db=config["distortion"]["drive_db"]),
            # Add output gain adjustment if configured
            Gain(gain_db=config["output_gain"]["gain_db"]) if "output_gain" in config else Gain(gain_db=0),
            Limiter(
                threshold_db=config["limiter"]["threshold_db"],
                release_ms=config["limiter"]["release_ms"]
            )
        ])
        
        # Comb filter will be initialized in process_audio with correct sample rate
        self.comb_b = None
        self.comb_a = None

    @debug_timer
    def process_audio(self, audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply the audio effects chain to the input audio.
        
        Args:
            audio_data: Audio samples as numpy array
            sample_rate: Sample rate in Hz (must be 44100 for ElevenLabs output)
        """
        # If audio processing is disabled, return the original audio data
        if DISABLE_AUDIO_PROCESSING:
            print(f"{Fore.YELLOW}Audio processing is disabled. Using unprocessed audio.{Style.RESET_ALL}")
            return audio_data
            
        try:
            # Validate sample rate
            if sample_rate != 44100:
                print(f"Warning: Expected 44.1kHz sample rate, got {sample_rate}Hz")
                # Could optionally resample here if needed
            
            # Initialize comb filter with correct sample rate if not done
            if self.comb_b is None or self.comb_a is None:
                self.comb_b, self.comb_a = create_comb_filter(
                    self.config["comb_filter"]["delay_ms"],
                    self.config["comb_filter"]["feedback"],
                    sample_rate
                )
            
            # Apply pedalboard effects
            processed = self.board(audio_data, sample_rate)
            
            # Apply comb filter
            processed = lfilter(self.comb_b, self.comb_a, processed)
            
            # Apply ring modulation if configured
            if "ring_modulator" in self.config:
                processed = apply_ring_modulation(
                    processed,
                    self.config["ring_modulator"]["frequency"],
                    sample_rate,
                    self.config["ring_modulator"]["mix"]
                )
            
            # Apply bit crusher if configured
            if "bit_crusher" in self.config:
                processed = apply_bit_crusher(
                    processed,
                    self.config["bit_crusher"]["bit_depth"],
                    self.config["bit_crusher"]["mix"]
                )
            
            return processed
        except Exception as e:
            print(f"Warning: Audio processing failed, using fallback: {str(e)}")
            return audio_data  # Fallback to unprocessed audio

@debug_timer
async def process_and_play_audio(audio_data: Union[bytes, str, np.ndarray], 
                               sample_rate: Optional[int] = 44100) -> None:
    """
    Process and play audio data without blocking the main event loop.
    
    Args:
        audio_data: Audio data as bytes, file path, or numpy array
        sample_rate: Sample rate of the audio (default: 44100)
    """
    processor = AudioProcessor()
    
    # Convert input to numpy array if needed
    if isinstance(audio_data, (bytes, str)):
        if isinstance(audio_data, str):
            audio = AudioSegment.from_file(audio_data)
        else:
            audio = AudioSegment.from_wav(audio_data)
            
        # Resample if not 44.1kHz
        if audio.frame_rate != 44100:
            print(f"Resampling from {audio.frame_rate}Hz to 44100Hz")
            audio = audio.set_frame_rate(44100)
            
        audio_array = np.array(audio.get_array_of_samples(), dtype=np.float32) / 32768.0
        sample_rate = 44100  # Always use 44.1kHz after resampling
    else:
        audio_array = audio_data
        if sample_rate != 44100:
            print(f"Warning: Input array sample rate {sample_rate}Hz differs from expected 44.1kHz")
            # Could add resampling here if needed
    
    # Process audio in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    processed_audio = await loop.run_in_executor(
        None, 
        processor.process_audio,
        audio_array,
        44100  # Always use 44.1kHz for processing
    )
    
    # Save processed audio if input was a file
    if isinstance(audio_data, str):
        # Generate output path in processed_audio directory
        input_path = Path(audio_data)
        output_path = Path('audio/processed_audio') / input_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Convert to int16 and save
        processed_int = (processed_audio * 32768).astype(np.int16)
        processed_segment = AudioSegment(
            processed_int.tobytes(),
            frame_rate=44100,  # Always use 44.1kHz for output
            sample_width=2,
            channels=1
        )
        processed_segment.export(str(output_path), format=output_path.suffix[1:])
        print(f"Saved processed audio to: {output_path}")
    
    # Play audio non-blocking
    sd.play(processed_audio, 44100)  # Always use 44.1kHz for playback
    sd.wait()  # Wait for playback to finish

@debug_timer
def apply_audio_effects(audio_data: np.ndarray, sample_rate: int) -> np.ndarray:
    """
    Apply audio effects chain to numpy array of audio data.
    
    Args:
        audio_data: Audio data as numpy array
        sample_rate: Sample rate of the audio
    
    Returns:
        Processed audio data as numpy array
    """
    processor = AudioProcessor()
    return processor.process_audio(audio_data, sample_rate) 