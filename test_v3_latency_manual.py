#!/usr/bin/env python3
"""
Manual latency test for ElevenLabs v3.
This script compares v2.5 Flash vs v3 latency for DJ commentary generation.
"""

import asyncio
import os
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from elevenlabs import ElevenLabs

# Test samples
DJ_SAMPLES = {
    "short": "Next up, we've got a banger!",
    "medium": "Alright folks, stick with us for this absolute masterpiece from the legends. Turn it up!",
}

async def test_model_latency(client, model_id, text, voice_id):
    """Test latency for a specific model and text."""
    print(f"\n{'='*60}")
    print(f"Testing {model_id}")
    print(f"Text: '{text}'")
    print(f"{'='*60}")

    try:
        start_time = time.time()

        # V3 requires stability in [0.0, 0.5, 1.0], while v2.5 uses continuous range
        stability = 0.5 if model_id == "eleven_v3" else 0.60

        # Generate audio stream
        audio_stream = client.text_to_speech.stream(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            voice_settings={
                "stability": stability,
                "similarity_boost": 0.85,
                "use_speaker_boost": True,
                "style": 0.25,
            }
        )

        # Consume the stream to measure total time
        chunks = list(audio_stream)
        elapsed = time.time() - start_time
        total_bytes = sum(len(chunk) for chunk in chunks)

        print(f"✓ Success!")
        print(f"  Total time: {elapsed:.2f}s ({elapsed*1000:.0f}ms)")
        print(f"  Audio bytes: {total_bytes}")
        print(f"  Characters: {len(text)}")
        print(f"  Bytes/second: {total_bytes/elapsed:.0f}")

        return {
            "model": model_id,
            "elapsed": elapsed,
            "bytes": total_bytes,
            "text_len": len(text)
        }

    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return None

async def main():
    """Run latency comparison tests."""
    api_key = os.getenv("ELEVENLABS_API_KEY")

    if not api_key:
        print("✗ ELEVENLABS_API_KEY not found in environment")
        return

    print("\n🎯 ElevenLabs v3 vs v2.5 Latency Benchmark")
    print("=" * 60)

    client = ElevenLabs(api_key=api_key)
    voice_id = "P9l1opNa5pWou2X5MwfB"  # DJ R3X voice

    results = {}

    # Test short sample with both models
    for text_key, text in DJ_SAMPLES.items():
        print(f"\n\n📝 Sample: {text_key.upper()}")
        print(f"Text: '{text}' ({len(text)} chars)")

        sample_results = {}

        for model_id in ["eleven_flash_v2_5", "eleven_v3"]:
            result = await test_model_latency(client, model_id, text, voice_id)
            if result:
                sample_results[model_id] = result

        # Compare results
        if "eleven_flash_v2_5" in sample_results and "eleven_v3" in sample_results:
            v2_5 = sample_results["eleven_flash_v2_5"]
            v3 = sample_results["eleven_v3"]

            overhead = v3["elapsed"] / v2_5["elapsed"]

            print(f"\n📊 Comparison ({text_key}):")
            print(f"  V2.5 Flash: {v2_5['elapsed']*1000:.0f}ms")
            print(f"  V3:         {v3['elapsed']*1000:.0f}ms")
            print(f"  Overhead:   {overhead:.1f}x slower")
            print(f"  DJ Mode OK? {'✓ YES' if v3['elapsed'] < 5 else '⚠ MARGINAL'}")

        results[text_key] = sample_results

    # Summary
    print(f"\n\n{'='*60}")
    print("📋 Summary")
    print(f"{'='*60}")

    for text_key in DJ_SAMPLES:
        if text_key in results:
            print(f"\n{text_key.upper()}:")
            if "eleven_flash_v2_5" in results[text_key]:
                v2_5_time = results[text_key]["eleven_flash_v2_5"]["elapsed"]
                print(f"  V2.5: {v2_5_time*1000:.0f}ms")

            if "eleven_v3" in results[text_key]:
                v3_time = results[text_key]["eleven_v3"]["elapsed"]
                print(f"  V3:   {v3_time*1000:.0f}ms")

    print("\n✓ Benchmark complete!")

if __name__ == "__main__":
    asyncio.run(main())
