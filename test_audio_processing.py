#!/usr/bin/env python3
"""
Test script for audio processing chain.
Processes audio files through the DJ R-3X effect chain and saves/plays the results.
"""

import os
import argparse
from pathlib import Path
import sounddevice as sd
from pydub import AudioSegment
import numpy as np
from audio_processor import AudioProcessor, process_and_play_audio
import asyncio

def get_default_output_path(input_path: str) -> str:
    """Generate default output path in processed_audio directory."""
    input_filename = os.path.basename(input_path)
    return os.path.join('audio', 'processed_audio', input_filename)

def process_file(input_path: str, output_path: str = None, play: bool = True):
    """
    Process an audio file through the effect chain.
    
    Args:
        input_path: Path to input audio file
        output_path: Path to save processed audio (optional)
        play: Whether to play the processed audio
    """
    print(f"Processing file: {input_path}")
    
    # Set default output path if none provided
    if output_path is None:
        output_path = get_default_output_path(input_path)
    
    # Load audio file
    audio = AudioSegment.from_file(input_path)
    original_sample_rate = audio.frame_rate
    
    # If not 44.1kHz, resample before processing
    if original_sample_rate != 44100:
        print(f"Resampling from {original_sample_rate}Hz to 44100Hz")
        audio = audio.set_frame_rate(44100)
    
    # Convert to numpy array
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    
    # Convert to mono if stereo
    if audio.channels == 2:
        samples = samples.reshape((-1, 2))
        samples = samples.mean(axis=1)
    
    # Normalize
    samples = samples / 32768.0
    
    # Process audio
    processor = AudioProcessor()
    processed = processor.process_audio(samples, 44100)  # Always use 44.1kHz
    
    if output_path:
        # Convert back to int16
        processed_int = (processed * 32768).astype(np.int16)
        
        # Create output audio segment
        processed_audio = AudioSegment(
            processed_int.tobytes(),
            frame_rate=44100,  # Always use 44.1kHz for output
            sample_width=2,
            channels=1
        )
        
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Save processed audio
        print(f"Saving processed audio to: {output_path}")
        processed_audio.export(output_path, format=output_path.split('.')[-1])
    
    if play:
        print("Playing processed audio...")
        sd.play(processed, 44100)  # Always use 44.1kHz for playback
        sd.wait()

async def process_file_async(input_path: str, output_path: str = None, play: bool = True):
    """Async wrapper for process_file to use our async processing chain."""
    if play:
        await process_and_play_audio(input_path)
    else:
        # If we're not playing, use the sync version to save the file
        process_file(input_path, output_path, play=False)

def main():
    parser = argparse.ArgumentParser(description='Test DJ R-3X audio processing chain')
    parser.add_argument('input', help='Input audio file path')
    parser.add_argument('--output', '-o', help='Output audio file path')
    parser.add_argument('--no-play', '-n', action='store_true', help='Don\'t play the processed audio')
    parser.add_argument('--async', '-a', action='store_true', help='Use async processing chain')
    parser.add_argument('--compare', '-c', action='store_true', help='Compare with real DJ R-3X audio')
    args = parser.parse_args()
    
    # Ensure input file exists
    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}")
        return
    
    # Set default output path if none provided
    if args.output is None:
        args.output = get_default_output_path(args.input)
    
    # Create output directory if needed
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    
    if getattr(args, 'async'):
        # Run async version
        asyncio.run(process_file_async(args.input, args.output, not args.no_play))
    else:
        # Run sync version
        process_file(args.input, args.output, not args.no_play)
        
        # If compare flag is set, play a real DJ R-3X clip for reference
        if args.compare:
            real_audio_dir = os.path.join('audio', 'real_audio')
            if os.path.exists(real_audio_dir):
                real_clips = [f for f in os.listdir(real_audio_dir) 
                            if f.endswith(('.wav', '.mp3'))]
                if real_clips:
                    print("\nPlaying reference clip from real DJ R-3X...")
                    reference_clip = os.path.join(real_audio_dir, real_clips[0])
                    audio = AudioSegment.from_file(reference_clip)
                    samples = np.array(audio.get_array_of_samples(), dtype=np.float32) / 32768.0
                    sd.play(samples, audio.frame_rate)
                    sd.wait()

if __name__ == "__main__":
    main() 