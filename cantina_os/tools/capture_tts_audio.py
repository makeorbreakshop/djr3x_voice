#!/usr/bin/env python3
"""
Capture TTS Audio from ElevenLabs for Analysis

This script generates TTS audio using the ElevenLabs service and saves
the raw PCM chunks to a file for offline analysis.

Usage:
    python capture_tts_audio.py "This is a test of the DJ R3X mouth LEDs." output.pcm
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Add cantina_os to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
import numpy as np


async def capture_tts_audio(
    text: str,
    output_file: Path,
    api_key: Optional[str] = None,
    voice_id: str = "P9l1opNa5pWou2X5MwfB",
    model_id: str = "eleven_turbo_v2_5"
):
    """
    Generate TTS and save raw PCM audio to file.

    Args:
        text: Text to synthesize
        output_file: Path to save PCM audio
        api_key: ElevenLabs API key (or load from .env)
        voice_id: Voice ID to use
        model_id: Model ID to use
    """
    # Load API key from environment
    if not api_key:
        load_dotenv()
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY not found in environment")

    print(f"Generating TTS for text: '{text[:50]}...'")
    print(f"  Voice: {voice_id}")
    print(f"  Model: {model_id}")
    print(f"  Output: {output_file}")

    # Initialize ElevenLabs client
    eleven_client = ElevenLabs(api_key=api_key)

    # Voice settings (same as production)
    voice_settings = {
        "stability": 0.60,
        "similarity_boost": 0.85,
        "style": 0.25,
        "use_speaker_boost": True,
    }

    # Get streaming audio
    print("\nStreaming audio from ElevenLabs...")
    audio_stream = eleven_client.text_to_speech.stream(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        voice_settings=voice_settings,
        output_format="pcm_24000"  # 24kHz PCM - same as production
    )

    # Collect all chunks
    all_chunks = []
    chunk_count = 0

    try:
        for chunk in audio_stream:
            if chunk:
                all_chunks.append(chunk)
                chunk_count += 1
                print(f"  Received chunk {chunk_count} ({len(chunk)} bytes)")

    except Exception as e:
        print(f"Error streaming audio: {e}")
        return False

    # Concatenate all chunks
    print(f"\nConcatenating {chunk_count} chunks...")
    audio_bytes = b''.join(all_chunks)

    # Save to file
    print(f"Writing {len(audio_bytes):,} bytes to {output_file}...")
    output_file.write_bytes(audio_bytes)

    # Print summary
    duration = len(audio_bytes) / (24000 * 2)  # 24kHz, 16-bit (2 bytes per sample)
    print(f"\n✓ Audio captured successfully!")
    print(f"  Total size: {len(audio_bytes):,} bytes")
    print(f"  Duration: {duration:.2f} seconds")
    print(f"  Sample rate: 24000 Hz")
    print(f"  Channels: 1 (mono)")
    print(f"  Format: 16-bit signed PCM")
    print(f"\nTo analyze, run:")
    print(f"  python audio_amplitude_analyzer.py {output_file}")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Capture TTS audio for analysis")
    parser.add_argument("text", help="Text to synthesize")
    parser.add_argument("output", type=Path, help="Output PCM file")
    parser.add_argument("--voice", default="P9l1opNa5pWou2X5MwfB", help="Voice ID")
    parser.add_argument("--model", default="eleven_turbo_v2_5", help="Model ID")

    args = parser.parse_args()

    # Run capture
    asyncio.run(capture_tts_audio(
        text=args.text,
        output_file=args.output,
        voice_id=args.voice,
        model_id=args.model
    ))


if __name__ == "__main__":
    main()
