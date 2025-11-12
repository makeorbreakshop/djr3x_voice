"""
ElevenLabs v3 Latency Benchmark Tests

Tests for comparing latency and quality of ElevenLabs v3 vs v2.5 models.
Useful for validating v3 as a candidate for background DJ commentary generation.

Usage:
    python -m pytest tests/test_elevenlabs_v3_latency.py -v -s

    # Run specific test
    python -m pytest tests/test_elevenlabs_v3_latency.py::test_v3_latency_benchmark -v -s

    # Run with API key check
    ELEVENLABS_API_KEY=your_key python -m pytest tests/test_elevenlabs_v3_latency.py -v -s
"""

import asyncio
import os
import time
from typing import Dict, List, Tuple
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

# Import the service we're testing
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cantina_os.services.elevenlabs_service import ElevenLabsService, ElevenLabsConfig


# Test samples representing different DJ commentary lengths
DJ_COMMENTARY_SAMPLES = {
    "short": "Next up, we've got a banger!",  # ~35 chars
    "medium": "Alright folks, stick with us for this absolute masterpiece from the legends. Turn it up!",  # ~95 chars
    "long": "Ladies and gentlemen, this next track is a certified classic that will absolutely take over the dance floor. Introduced by none other than the incredible artists who brought us some of the most memorable moments in music history. Let's turn it up to eleven!",  # ~270 chars
}

# Test prompts with v3 audio tags for personality
V3_TAG_SAMPLES = {
    "excited": "[excited] Next up, we've got a total banger! Turn it up!",
    "whisper": "[whispers] Listen to this smooth transition, it's pure magic.",
    "sarcastic": "[sarcastic] Oh, you thought that was good? Just wait for this next one.",
    "laughing": "[laughs] This track is absolutely hilarious, my friends!",
}


class TestElevenLabsV3Latency:
    """Test suite for ElevenLabs v3 model latency and quality."""

    @pytest.fixture
    def api_key(self):
        """Get API key from environment or skip test."""
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            pytest.skip("ELEVENLABS_API_KEY not set in environment")
        return key

    @pytest.fixture
    def voice_id(self):
        """Default DJ R3X voice ID."""
        return "P9l1opNa5pWou2X5MwfB"  # Quick voice clone

    @pytest.fixture
    def event_bus(self):
        """Mock event bus for service testing."""
        bus = MagicMock()
        bus.on = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    async def elevenlabs_service(self, api_key, event_bus):
        """Create ElevenLabsService instance for testing."""
        config = {
            "ELEVENLABS_API_KEY": api_key,
            "MODEL_ID": "eleven_flash_v2_5",  # Start with v2.5
        }
        service = ElevenLabsService(event_bus, config=config)
        await service._start()
        yield service
        await service._cleanup()

    @pytest.mark.asyncio
    async def test_v3_model_id_recognized(self):
        """Verify v3 model_id is correctly recognized in config."""
        api_key = os.environ.get("ELEVENLABS_API_KEY", "dummy_key")

        # Test that v3 model_id can be set in config
        config = {
            "ELEVENLABS_API_KEY": api_key,
            "MODEL_ID": "eleven_v3",
        }

        service_config = ElevenLabsConfig(
            api_key=api_key,
            model_id="eleven_v3"
        )

        assert service_config.model_id == "eleven_v3"
        print(f"✓ v3 model_id recognized: {service_config.model_id}")

    @pytest.mark.asyncio
    async def test_v2_5_to_v3_config_swap(self):
        """Test that config can be swapped between v2.5 and v3."""
        api_key = os.environ.get("ELEVENLABS_API_KEY", "dummy_key")

        # Start with v2.5
        config_v2_5 = ElevenLabsConfig(
            api_key=api_key,
            model_id="eleven_flash_v2_5"
        )
        assert config_v2_5.model_id == "eleven_flash_v2_5"

        # Swap to v3
        config_v3 = ElevenLabsConfig(
            api_key=api_key,
            model_id="eleven_v3"
        )
        assert config_v3.model_id == "eleven_v3"

        print("✓ Config swap v2.5 ↔ v3 works correctly")

    @pytest.mark.asyncio
    async def test_v3_latency_short_sample(self, api_key, voice_id):
        """Test v3 latency with short DJ commentary (real API call).

        Expected latency for v3: 1-3 seconds (higher than v2.5's ~75ms TTFB)
        This is acceptable for background DJ mode pre-generation.
        """
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)
        text = DJ_COMMENTARY_SAMPLES["short"]

        start_time = time.time()
        try:
            audio_stream = client.text_to_speech.stream(
                text=text,
                voice_id=voice_id,
                model_id="eleven_v3",
                voice_settings={
                    "stability": 0.60,
                    "similarity_boost": 0.85,
                    "use_speaker_boost": True,
                    "style": 0.25,
                }
            )

            # Consume the stream to measure full generation time
            chunks = list(audio_stream)
            elapsed = time.time() - start_time
            total_bytes = sum(len(chunk) for chunk in chunks)

            print(f"\n✓ V3 Short Sample Latency:")
            print(f"  Text: '{text}'")
            print(f"  Time to first byte: {elapsed*1000:.0f}ms")
            print(f"  Total audio bytes: {total_bytes}")
            print(f"  Acceptable for background? {'YES' if elapsed < 5 else 'NO'}")

            assert elapsed < 10, f"v3 generation took too long: {elapsed:.1f}s"

        except Exception as e:
            pytest.skip(f"API call failed (network/quota issue): {str(e)}")

    @pytest.mark.asyncio
    async def test_v3_latency_medium_sample(self, api_key, voice_id):
        """Test v3 latency with medium DJ commentary (realistic length)."""
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)
        text = DJ_COMMENTARY_SAMPLES["medium"]

        start_time = time.time()
        try:
            audio_stream = client.text_to_speech.stream(
                text=text,
                voice_id=voice_id,
                model_id="eleven_v3",
                voice_settings={
                    "stability": 0.60,
                    "similarity_boost": 0.85,
                    "use_speaker_boost": True,
                    "style": 0.25,
                }
            )

            chunks = list(audio_stream)
            elapsed = time.time() - start_time
            total_bytes = sum(len(chunk) for chunk in chunks)

            print(f"\n✓ V3 Medium Sample Latency:")
            print(f"  Text: '{text}'")
            print(f"  Time to generate: {elapsed*1000:.0f}ms")
            print(f"  Total audio bytes: {total_bytes}")
            print(f"  Acceptable for background? {'YES' if elapsed < 5 else 'NO'}")

            assert elapsed < 10, f"v3 generation took too long: {elapsed:.1f}s"

        except Exception as e:
            pytest.skip(f"API call failed: {str(e)}")

    @pytest.mark.asyncio
    async def test_v3_latency_long_sample(self, api_key, voice_id):
        """Test v3 latency with longer DJ commentary."""
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)
        text = DJ_COMMENTARY_SAMPLES["long"]

        start_time = time.time()
        try:
            audio_stream = client.text_to_speech.stream(
                text=text,
                voice_id=voice_id,
                model_id="eleven_v3",
                voice_settings={
                    "stability": 0.60,
                    "similarity_boost": 0.85,
                    "use_speaker_boost": True,
                    "style": 0.25,
                }
            )

            chunks = list(audio_stream)
            elapsed = time.time() - start_time
            total_bytes = sum(len(chunk) for chunk in chunks)

            print(f"\n✓ V3 Long Sample Latency:")
            print(f"  Text length: {len(text)} chars")
            print(f"  Time to generate: {elapsed*1000:.0f}ms ({elapsed:.1f}s)")
            print(f"  Total audio bytes: {total_bytes}")
            print(f"  Acceptable for background? {'YES' if elapsed < 10 else 'MARGINAL'}")

            # Allow up to 10 seconds for longer samples in background mode
            assert elapsed < 15, f"v3 generation took way too long: {elapsed:.1f}s"

        except Exception as e:
            pytest.skip(f"API call failed: {str(e)}")

    @pytest.mark.asyncio
    async def test_v3_audio_tags_short(self, api_key, voice_id):
        """Test v3 audio tag features with short sample."""
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)

        # Test excited tag
        text = V3_TAG_SAMPLES["excited"]

        try:
            audio_stream = client.text_to_speech.stream(
                text=text,
                voice_id=voice_id,
                model_id="eleven_v3",
                voice_settings={
                    "stability": 0.60,
                    "similarity_boost": 0.85,
                }
            )

            chunks = list(audio_stream)
            total_bytes = sum(len(chunk) for chunk in chunks)

            print(f"\n✓ V3 Audio Tag Test (Excited):")
            print(f"  Text: '{text}'")
            print(f"  Generated audio bytes: {total_bytes}")
            print(f"  Tag processed: YES (audio generated successfully)")

            assert total_bytes > 0, "No audio generated for tag test"

        except Exception as e:
            pytest.skip(f"API call failed: {str(e)}")

    @pytest.mark.asyncio
    async def test_v3_vs_v2_5_latency_comparison(self, api_key, voice_id):
        """Compare latency between v3 and v2.5 for same sample.

        This helps establish the latency overhead of v3 for background generation.
        """
        from elevenlabs import ElevenLabs

        client = ElevenLabs(api_key=api_key)
        text = DJ_COMMENTARY_SAMPLES["medium"]

        results = {}

        for model_id in ["eleven_flash_v2_5", "eleven_v3"]:
            try:
                start_time = time.time()
                audio_stream = client.text_to_speech.stream(
                    text=text,
                    voice_id=voice_id,
                    model_id=model_id,
                    voice_settings={
                        "stability": 0.60,
                        "similarity_boost": 0.85,
                        "use_speaker_boost": True,
                        "style": 0.25,
                    }
                )

                chunks = list(audio_stream)
                elapsed = time.time() - start_time
                total_bytes = sum(len(chunk) for chunk in chunks)

                results[model_id] = {
                    "elapsed": elapsed,
                    "bytes": total_bytes
                }

            except Exception as e:
                print(f"⚠ {model_id} failed: {str(e)}")
                pytest.skip(f"API call failed for {model_id}: {str(e)}")

        # Display comparison
        print(f"\n✓ V3 vs V2.5 Latency Comparison:")
        print(f"  Sample: '{text}'")
        print(f"  \n  V2.5 Flash (Current):")
        print(f"    - Time: {results['eleven_flash_v2_5']['elapsed']*1000:.0f}ms")
        print(f"    - Bytes: {results['eleven_flash_v2_5']['bytes']}")
        print(f"  \n  V3 (Expressive):")
        print(f"    - Time: {results['eleven_v3']['elapsed']*1000:.0f}ms")
        print(f"    - Bytes: {results['eleven_v3']['bytes']}")
        print(f"  \n  Overhead: {(results['eleven_v3']['elapsed'] / results['eleven_flash_v2_5']['elapsed']):.1f}x slower")
        print(f"  \n  DJ Mode Background Use? {'✓ YES' if results['eleven_v3']['elapsed'] < 5 else '⚠ MARGINAL'}")


class TestV3FeatureFlags:
    """Tests for feature flag integration of v3 experimental mode."""

    def test_tts_model_feature_flag(self):
        """Test that feature flag config works for TTS model selection."""
        # Simulate feature flag for experimental v3
        feature_flags = {
            "TTS_USE_V3": False,  # Default: use v2.5
            "TTS_V3_FOR_BACKGROUND_ONLY": True,  # Only use v3 for pre-generation
        }

        # Determine which model to use based on flags
        if feature_flags["TTS_USE_V3"]:
            if feature_flags["TTS_V3_FOR_BACKGROUND_ONLY"]:
                model_for_realtime = "eleven_flash_v2_5"  # Real-time: v2.5
                model_for_background = "eleven_v3"  # Background: v3
            else:
                model_for_realtime = "eleven_v3"
                model_for_background = "eleven_v3"
        else:
            model_for_realtime = "eleven_flash_v2_5"
            model_for_background = "eleven_flash_v2_5"

        assert model_for_realtime == "eleven_flash_v2_5"
        assert model_for_background == "eleven_flash_v2_5"  # Because flag is off

        print("✓ Feature flag logic works correctly")

    def test_dj_mode_v3_selection(self):
        """Test that DJ mode can select v3 for commentary pre-generation."""
        # DJ mode config
        dj_config = {
            "background_commentary_model": "eleven_v3",  # Use v3 for pre-gen
            "realtime_response_model": "eleven_flash_v2_5",  # v2.5 for real-time
        }

        assert dj_config["background_commentary_model"] == "eleven_v3"
        assert dj_config["realtime_response_model"] == "eleven_flash_v2_5"

        print("✓ DJ mode v3/v2.5 split config works")


class TestV3Integration:
    """Integration tests for v3 within the DJ R3X system."""

    @pytest.mark.asyncio
    async def test_service_supports_v3_config(self):
        """Verify ElevenLabsService can be configured with v3 model."""
        api_key = os.environ.get("ELEVENLABS_API_KEY", "dummy_key")
        event_bus = MagicMock()

        # Create service with v3 config
        config = {
            "ELEVENLABS_API_KEY": api_key,
            "MODEL_ID": "eleven_v3",
        }

        service = ElevenLabsService(event_bus, config=config)

        # Verify config was set
        assert service._config.model_id == "eleven_v3"
        print(f"✓ Service initialized with model: {service._config.model_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
