"""
Manual Interaction Test for SDK 5.x

This test starts DJ R3X and lets you manually test voice transcription.
Instructions:
1. Run this test
2. Press SPACE to start recording (simulates engage command)
3. Speak into your microphone
4. Press SPACE again to stop recording
5. Check the transcript output
6. Repeat or press 'q' to quit

This verifies the end-to-end SDK 5.x per-session connection pattern works.
"""

import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cantina_os.core.event_bus import AsyncIOEventEmitter
from cantina_os.core.event_topics import EventTopics
from cantina_os.services.deepgram_direct_mic_service import DeepgramDirectMicService

async def main():
    print("\n" + "="*60)
    print("SDK 5.x Manual Voice Transcription Test")
    print("="*60)
    print()
    print("Instructions:")
    print("  1. Press 'r' to START recording")
    print("  2. Speak into your microphone")
    print("  3. Press 's' to STOP recording")
    print("  4. See your transcription appear")
    print("  5. Press 'q' to QUIT")
    print()
    print("="*60)
    print()

    # Create event bus
    event_bus = AsyncIOEventEmitter()

    # Create service
    service = DeepgramDirectMicService(event_bus=event_bus)

    # Listen for transcription events
    def on_transcript(data):
        text = data.get('text', '')
        is_final = data.get('is_final', False)
        if is_final and text:
            print(f"\n✓ TRANSCRIPTION: {text}\n")

    event_bus.on(EventTopics.TRANSCRIPTION_FINAL, on_transcript)

    # Start service
    await service.start()
    print("✓ Service started\n")

    # Interactive loop
    print("Ready! Press 'r' to record, 's' to stop, 'q' to quit")
    is_recording = False

    try:
        while True:
            # Wait for input (non-blocking)
            await asyncio.sleep(0.1)

            # Check for keypress (simple stdin read)
            # Note: This is a simple approach - in production you'd use pynput or similar
            print("Commands: [r]ecord, [s]top, [q]uit > ", end='', flush=True)
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            cmd = line.strip().lower()

            if cmd == 'r' and not is_recording:
                print("\n🎤 RECORDING... (speak now)")
                await event_bus.emit(EventTopics.MIC_RECORDING_START, {})
                is_recording = True

            elif cmd == 's' and is_recording:
                print("\n⏹️  STOPPED")
                await event_bus.emit(EventTopics.MIC_RECORDING_STOP, {})
                is_recording = False

            elif cmd == 'q':
                print("\nQuitting...")
                break

            else:
                print("Invalid command or wrong state")

    except KeyboardInterrupt:
        print("\n\nInterrupted")

    finally:
        print("\nCleaning up...")
        await service.stop()
        print("✓ Service stopped")


if __name__ == "__main__":
    asyncio.run(main())
