#!/usr/bin/env python3
"""
Test script for Porcupine wake word detection with "DJ Rex"
This is a standalone test - NOT integrated into CantinaOS yet.
Uses PyAudio (already installed for Deepgram) instead of pvrecorder.
"""

import os
import sys
import struct
import pvporcupine
import pyaudio
from dotenv import load_dotenv

def main():
    # Load environment variables from .env file
    load_dotenv()

    # Get access key from environment
    access_key = os.getenv("PICOVOICE_ACCESS_KEY")

    if not access_key:
        print("ERROR: PICOVOICE_ACCESS_KEY not found in environment")
        print("Please add it to your .env file or export it:")
        print("  export PICOVOICE_ACCESS_KEY='your_key_here'")
        sys.exit(1)

    # Path to the custom keyword file
    keyword_path = "cantina_os/wake_word/DJ-Rex_en_mac_v3_0_0.ppn"

    if not os.path.exists(keyword_path):
        print(f"ERROR: Keyword file not found at {keyword_path}")
        sys.exit(1)

    print(f"Initializing Porcupine with keyword file: {keyword_path}")
    print(f"Access key: {access_key[:10]}...")

    try:
        # Initialize Porcupine with the custom keyword
        porcupine = pvporcupine.create(
            access_key=access_key,
            keyword_paths=[keyword_path]
        )

        print(f"\nPorcupine initialized successfully!")
        print(f"  - Sample rate: {porcupine.sample_rate} Hz")
        print(f"  - Frame length: {porcupine.frame_length} samples")
        print(f"  - Version: {porcupine.version}")
        print(f"\nListening for wake word 'DJ Rex'...")
        print("(Press Ctrl+C to stop)\n")

        # Initialize PyAudio
        pa = pyaudio.PyAudio()

        # Open audio stream
        audio_stream = pa.open(
            rate=porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=porcupine.frame_length
        )

        try:
            while True:
                # Read audio frame from microphone
                pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

                # Process frame with Porcupine
                keyword_index = porcupine.process(pcm)

                # Check if wake word was detected
                if keyword_index >= 0:
                    print("🎤 WAKE WORD DETECTED: DJ Rex!")
                    print("    (Detection successful)\n")

        except KeyboardInterrupt:
            print("\n\nStopping...")

        finally:
            audio_stream.stop_stream()
            audio_stream.close()
            pa.terminate()
            porcupine.delete()
            print("Cleanup complete")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
