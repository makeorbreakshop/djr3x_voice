"""
Unit tests for PCM amplitude calculation in ElevenLabsService.

Tests the core RMS calculation logic without requiring ElevenLabs API access.
"""

import pytest
import numpy as np
from datetime import datetime

# Mark all tests to skip auto-mocking (pure math tests, no API calls)
pytestmark = pytest.mark.no_mocking


class TestPCMAmplitudeCalculation:
    """Test suite for PCM audio amplitude RMS calculation."""

    def test_silence_amplitude(self):
        """Test that silence produces zero amplitude."""
        # Create silent PCM samples (all zeros)
        samples = np.zeros(1000, dtype=np.int16)

        # Calculate RMS
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        normalized = rms / 32768.0

        assert rms == 0.0, f"Expected RMS 0.0 for silence, got {rms}"
        assert normalized == 0.0, f"Expected normalized 0.0 for silence, got {normalized}"

    def test_full_scale_amplitude(self):
        """Test that full-scale audio produces maximum amplitude."""
        # Create maximum amplitude PCM samples (all max positive)
        samples = np.full(1000, 32767, dtype=np.int16)

        # Calculate RMS
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        normalized = rms / 32768.0

        # Should be very close to 1.0 (32767 / 32768 ≈ 0.99997)
        assert normalized > 0.999, f"Expected normalized > 0.999 for full scale, got {normalized}"

    def test_sine_wave_amplitude(self):
        """Test RMS calculation on a sine wave matches theoretical value."""
        # Generate 440Hz sine wave at 24kHz sample rate (1 second)
        sample_rate = 24000
        frequency = 440
        duration = 1.0
        t = np.linspace(0, duration, int(sample_rate * duration))

        # Generate sine wave with amplitude of 0.5 (half of max)
        amplitude = 0.5
        sine_wave = amplitude * np.sin(2 * np.pi * frequency * t)

        # Convert to 16-bit PCM
        samples = (sine_wave * 32767).astype(np.int16)

        # Calculate RMS
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        normalized = rms / 32768.0

        # Theoretical RMS of sine wave = amplitude / sqrt(2)
        # For amplitude 0.5: RMS = 0.5 / 1.414 ≈ 0.3536
        expected_rms = amplitude / np.sqrt(2)

        # Allow 1% tolerance
        assert abs(normalized - expected_rms) < 0.01, \
            f"Expected RMS ≈ {expected_rms:.4f}, got {normalized:.4f}"

    def test_positive_negative_samples(self):
        """Test that RMS treats positive and negative samples equally."""
        # Create positive samples
        positive_samples = np.full(1000, 1000, dtype=np.int16)
        rms_positive = np.sqrt(np.mean(positive_samples.astype(np.float32) ** 2))

        # Create negative samples (same magnitude)
        negative_samples = np.full(1000, -1000, dtype=np.int16)
        rms_negative = np.sqrt(np.mean(negative_samples.astype(np.float32) ** 2))

        # RMS should be identical (squaring removes sign)
        assert rms_positive == rms_negative, \
            f"RMS should be equal for positive and negative samples: {rms_positive} != {rms_negative}"

    def test_chunk_size_independence(self):
        """Test that RMS is consistent regardless of chunk size."""
        # Generate test signal
        samples_large = np.random.randint(-10000, 10000, size=10000, dtype=np.int16)

        # Calculate RMS on full chunk
        rms_full = np.sqrt(np.mean(samples_large.astype(np.float32) ** 2))

        # Calculate RMS on smaller chunks and average
        chunk_size = 1000
        rms_chunks = []
        for i in range(0, len(samples_large), chunk_size):
            chunk = samples_large[i:i+chunk_size]
            rms_chunk = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            rms_chunks.append(rms_chunk)

        # Average of chunk RMS should be similar to full RMS (within 10% for random data)
        avg_rms_chunks = np.mean(rms_chunks)
        difference_pct = abs(rms_full - avg_rms_chunks) / rms_full * 100

        assert difference_pct < 20, \
            f"Chunk RMS average differs from full RMS by {difference_pct:.1f}% (expected < 20%)"

    def test_normalization_clamping(self):
        """Test that normalization properly clamps values above 1.0."""
        # NOTE: int16 PCM samples are capped at 32767 (max positive int16), so an RMS computed
        # from real int16 data can never exceed 32768 and this test's own clamp branch was
        # unreachable (rms/32768.0 tops out at ~0.99997, never 1.0). This isn't a case of
        # production drift - the assertion's premise was mathematically impossible. Simulate an
        # out-of-range RMS directly (e.g. from a differently-scaled source) to actually exercise
        # the min(1.0, ...) clamp.
        rms = 40000.0
        normalized = min(1.0, rms / 32768.0)

        # Should be clamped to 1.0
        assert normalized == 1.0, f"Expected normalized to be clamped at 1.0, got {normalized}"

    def test_realistic_speech_amplitude(self):
        """Test amplitude calculation on realistic speech-like waveform."""
        # Generate a speech-like signal with varying amplitude
        sample_rate = 24000
        duration = 0.5  # 500ms chunk
        t = np.linspace(0, duration, int(sample_rate * duration))

        # Mix multiple frequencies with amplitude modulation (simulates formants)
        speech_like = (
            0.3 * np.sin(2 * np.pi * 150 * t) +  # Fundamental frequency
            0.2 * np.sin(2 * np.pi * 600 * t) +  # First formant
            0.15 * np.sin(2 * np.pi * 1200 * t)  # Second formant
        )

        # Apply envelope (speech has natural amplitude variation)
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 5 * t)  # 5Hz modulation
        speech_like *= envelope

        # Convert to PCM
        samples = (speech_like * 32767).astype(np.int16)

        # Calculate RMS
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        normalized = rms / 32768.0

        # Speech RMS should be in reasonable range (0.1 to 0.5)
        assert 0.1 <= normalized <= 0.5, \
            f"Speech-like signal RMS should be 0.1-0.5, got {normalized:.4f}"

    def test_pcm_24khz_format(self):
        """Test that PCM format assumptions match ElevenLabs output."""
        # Simulate ElevenLabs PCM chunk (pcm_24000 format)
        # Format: 16-bit signed little-endian PCM at 24kHz

        # Create 100ms worth of audio at 24kHz
        sample_rate = 24000
        duration = 0.1
        num_samples = int(sample_rate * duration)

        # Generate test signal
        samples = np.random.randint(-10000, 10000, size=num_samples, dtype=np.int16)

        # Convert to bytes (simulating what ElevenLabs returns)
        pcm_bytes = samples.tobytes()

        # Convert back from bytes (what our code does)
        decoded_samples = np.frombuffer(pcm_bytes, dtype=np.int16)

        # Should be identical
        assert np.array_equal(samples, decoded_samples), \
            "PCM encoding/decoding should be lossless"

        # Calculate RMS to ensure it's in valid range
        rms = np.sqrt(np.mean(decoded_samples.astype(np.float32) ** 2))
        normalized = rms / 32768.0

        assert 0.0 <= normalized <= 1.0, \
            f"Normalized RMS should be 0.0-1.0, got {normalized}"


class TestEMASmoothing:
    """Test suite for Exponential Moving Average smoothing of amplitude."""

    def test_ema_initial_value(self):
        """Test that EMA starts from zero."""
        # Simulate first amplitude event
        current_ema = 0.0
        new_amplitude = 0.5
        alpha = 0.3

        # Calculate EMA: EMA_new = alpha * new_value + (1 - alpha) * EMA_old
        ema_result = alpha * new_amplitude + (1 - alpha) * current_ema

        # First value should be: 0.3 * 0.5 + 0.7 * 0.0 = 0.15
        assert abs(ema_result - 0.15) < 0.001, \
            f"Expected initial EMA 0.15, got {ema_result}"

    def test_ema_smoothing_effect(self):
        """Test that EMA smooths out rapid changes."""
        alpha = 0.3
        ema = 0.0

        # Simulate rapid spike to 1.0
        amplitudes = [1.0, 1.0, 1.0, 1.0, 1.0]

        for amp in amplitudes:
            ema = alpha * amp + (1 - alpha) * ema

        # After 5 updates, should be close to 1.0 but not quite there (smoothing effect)
        assert 0.8 < ema < 1.0, \
            f"EMA after 5 spikes should be 0.8-1.0, got {ema:.3f}"

    def test_ema_decay(self):
        """Test that EMA decays gradually when signal drops."""
        alpha = 0.3

        # Start with high EMA
        ema = 1.0

        # Simulate signal dropping to zero
        amplitudes = [0.0, 0.0, 0.0, 0.0, 0.0]

        for amp in amplitudes:
            ema = alpha * amp + (1 - alpha) * ema

        # After 5 zero updates, should decay but not be zero yet
        assert 0.1 < ema < 0.3, \
            f"EMA after 5 zeros should decay to 0.1-0.3, got {ema:.3f}"

    def test_ema_alpha_sensitivity(self):
        """Test that alpha parameter affects smoothing strength."""
        # High alpha (fast response)
        alpha_fast = 0.8
        ema_fast = 0.0
        ema_fast = alpha_fast * 1.0 + (1 - alpha_fast) * ema_fast

        # Low alpha (slow response)
        alpha_slow = 0.1
        ema_slow = 0.0
        ema_slow = alpha_slow * 1.0 + (1 - alpha_slow) * ema_slow

        # Fast should respond more than slow
        assert ema_fast > ema_slow, \
            f"Higher alpha should respond faster: {ema_fast:.3f} vs {ema_slow:.3f}"


class TestMouthAmplitudeThrottling:
    """Test suite for mouth amplitude command throttling."""

    def test_throttle_timing(self):
        """Test that throttling prevents commands within 50ms."""
        import time

        MOUTH_UPDATE_INTERVAL = 0.05  # 50ms = 20Hz
        last_command_time = 0.0

        # First command should always send
        now = time.time()
        should_send_1 = (now - last_command_time >= MOUTH_UPDATE_INTERVAL)
        assert should_send_1, "First command should always send"
        last_command_time = now

        # Immediate second command should be throttled
        now = time.time()
        should_send_2 = (now - last_command_time >= MOUTH_UPDATE_INTERVAL)
        assert not should_send_2, "Immediate second command should be throttled"

        # After 50ms delay, should send again
        time.sleep(0.051)
        now = time.time()
        should_send_3 = (now - last_command_time >= MOUTH_UPDATE_INTERVAL)
        assert should_send_3, "Command after 50ms should send"

    def test_throttle_rate_calculation(self):
        """Test that 50ms interval equals 20Hz."""
        interval = 0.05  # 50ms
        frequency = 1.0 / interval

        assert frequency == 20.0, \
            f"50ms interval should equal 20Hz, got {frequency:.1f}Hz"


if __name__ == "__main__":
    # Run with: python -m pytest cantina_os/tests/test_pcm_amplitude.py -v
    pytest.main([__file__, "-v", "--tb=short"])
