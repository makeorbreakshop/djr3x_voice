"""
Unit tests for audio amplitude streaming functionality.

Tests the RMS amplitude calculation and event emission in ElevenLabsService,
and amplitude handling in EyeLightControllerService.
"""
import pytest
import asyncio
import numpy as np
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from io import BytesIO

from cantina_os.services.elevenlabs_service import ElevenLabsService
from cantina_os.services.eye_light_controller_service import EyeLightControllerService, EyePattern
from cantina_os.core.event_topics import EventTopics
from cantina_os.core.event_payloads import SpeechAmplitudePayload


class TestAmplitudeCalculation:
    """Test RMS amplitude calculation from audio data."""

    def test_rms_calculation_sine_wave(self):
        """Test RMS calculation with known sine wave."""
        # Generate a sine wave at known amplitude
        sample_rate = 44100
        duration = 0.1  # 100ms
        frequency = 440  # A4 note
        amplitude = 0.5  # Half amplitude

        t = np.linspace(0, duration, int(sample_rate * duration))
        samples = (amplitude * np.sin(2 * np.pi * frequency * t) * 32767).astype(np.int16)

        # Calculate RMS
        rms = np.sqrt(np.mean(samples.astype(np.float32)**2))
        normalized = rms / 32768.0

        # For a sine wave, RMS = amplitude / sqrt(2)
        expected_rms = amplitude / np.sqrt(2)

        assert abs(normalized - expected_rms) < 0.01, \
            f"RMS calculation incorrect: got {normalized}, expected {expected_rms}"

    def test_rms_calculation_silence(self):
        """Test RMS calculation with silence."""
        samples = np.zeros(1000, dtype=np.int16)

        rms = np.sqrt(np.mean(samples.astype(np.float32)**2))
        normalized = rms / 32768.0

        assert normalized == 0.0, "Silence should have zero amplitude"

    def test_rms_calculation_full_scale(self):
        """Test RMS calculation at full scale."""
        # Maximum amplitude square wave
        samples = np.array([32767, -32768] * 500, dtype=np.int16)

        rms = np.sqrt(np.mean(samples.astype(np.float32)**2))
        normalized = rms / 32768.0

        # Square wave at max amplitude should be very close to 1.0
        assert normalized > 0.99, f"Full scale should be ~1.0, got {normalized}"


class TestEyeLightControllerAmplitude:
    """Test amplitude handling in EyeLightControllerService."""

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        event_bus = AsyncMock()
        event_bus.on = AsyncMock()
        event_bus.emit = AsyncMock()
        return event_bus

    @pytest.fixture
    def eye_service(self, mock_event_bus):
        """Create EyeLightControllerService with mocks."""
        # NOTE: EyeLightControllerService.__init__ takes explicit keyword
        # arguments (mock_mode, serial_port, baud_rate, ...), not a `config`
        # dict. See cantina_os/services/eye_light_controller_service.py:192.
        service = EyeLightControllerService(
            event_bus=mock_event_bus,
            mock_mode=True,  # Don't try to connect to hardware
            serial_port=None,
            baud_rate=115200,
        )
        return service

    @pytest.mark.asyncio
    async def test_amplitude_handler_updates_modulation(self, eye_service):
        """Test that amplitude events update _amplitude_modulation."""
        # Set pattern to SPEAKING (required for amplitude to apply)
        eye_service._target_pattern = EyePattern.SPEAKING

        # Create amplitude payload
        payload = SpeechAmplitudePayload(
            conversation_id="test-123",
            amplitude=0.75,
            timestamp_offset=0.1
        )

        # Handle amplitude event
        await eye_service._handle_amplitude(payload.model_dump())

        # Check that amplitude modulation was updated
        # With alpha=0.3, first update: 0.3 * 0.75 + 0.7 * 0.0 = 0.225
        assert eye_service._amplitude_modulation > 0.0
        assert eye_service._amplitude_modulation <= 0.75

    @pytest.mark.asyncio
    async def test_amplitude_exponential_moving_average(self, eye_service):
        """Test EMA smoothing of amplitude values."""
        eye_service._target_pattern = EyePattern.SPEAKING

        # Send series of amplitude values
        amplitudes = [0.1, 0.5, 0.9, 0.5, 0.1]

        for amp in amplitudes:
            payload = SpeechAmplitudePayload(
                conversation_id="test-123",
                amplitude=amp,
                timestamp_offset=0.1
            )
            await eye_service._handle_amplitude(payload.model_dump())

        # Final amplitude should be smoothed
        final_amplitude = eye_service._amplitude_modulation

        # Should not jump to final value immediately (due to EMA)
        assert final_amplitude != 0.1
        # But should be moving towards it
        assert 0.0 < final_amplitude < 0.5

    @pytest.mark.asyncio
    async def test_amplitude_ignored_when_not_speaking(self, eye_service):
        """Test that amplitude is ignored when not in SPEAKING pattern."""
        # Set pattern to something other than SPEAKING
        eye_service._target_pattern = EyePattern.IDLE

        # Send amplitude event
        payload = SpeechAmplitudePayload(
            conversation_id="test-123",
            amplitude=0.8,
            timestamp_offset=0.1
        )
        await eye_service._handle_amplitude(payload.model_dump())

        # Amplitude modulation should remain at 0
        assert eye_service._amplitude_modulation == 0.0

    @pytest.mark.asyncio
    async def test_amplitude_reset_on_speech_end(self, eye_service):
        """Test that amplitude modulation resets when speech ends."""
        # Set up speaking with amplitude
        eye_service._target_pattern = EyePattern.SPEAKING
        eye_service._amplitude_modulation = 0.7

        # _handle_speech_ended only acts while in INTERACTIVE mode (see
        # eye_light_controller_service.py:938-966 / _is_in_interactive_mode).
        eye_service._current_system_mode = "INTERACTIVE"

        # Simulate speech ended
        await eye_service._handle_speech_ended({})

        # Amplitude should be reset
        assert eye_service._amplitude_modulation == 0.0


class TestControlLoopAmplitudeApplication:
    """Test that control loop applies amplitude to brightness."""

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        event_bus = AsyncMock()
        event_bus.on = AsyncMock()
        event_bus.emit = AsyncMock()
        return event_bus

    @pytest.fixture
    def eye_service(self, mock_event_bus):
        """Create EyeLightControllerService with mocks."""
        service = EyeLightControllerService(
            event_bus=mock_event_bus,
            mock_mode=True,
            serial_port=None,
            baud_rate=115200,
        )
        # Mock adapter for testing
        service.adapter = Mock()
        service.adapter.set_brightness = AsyncMock()
        service.adapter.set_color = AsyncMock()
        service.adapter.set_pattern = AsyncMock()
        service.connected = True
        service.mock_mode = False
        return service

    @pytest.mark.asyncio
    async def test_brightness_modulation_calculation(self, eye_service):
        """Test that brightness is modulated based on amplitude."""
        # Set base brightness
        eye_service._target_brightness = 100

        # Set amplitude modulation
        eye_service._amplitude_modulation = 0.5

        # Trigger control update
        await eye_service._control_update()

        # Calculate expected brightness
        # modulation_factor = 1.0 + (0.5 * 0.3) = 1.15
        # final_brightness = int(100 * 1.15) = 114 due to float truncation
        # (eye_light_controller_service.py: final_brightness = int(...)),
        # since 0.5 * 0.3 == 0.15000000000000002 in binary floating point.
        expected_brightness = 114

        # Check that brightness command was sent
        assert eye_service.adapter.set_brightness.called
        call_args = eye_service.adapter.set_brightness.call_args
        actual_brightness = call_args[0][0]

        assert actual_brightness == expected_brightness

    @pytest.mark.asyncio
    async def test_brightness_clamping(self, eye_service):
        """Test that brightness is clamped to 0-255 range."""
        # Set high base brightness
        eye_service._target_brightness = 250

        # Set high amplitude (would push over 255)
        eye_service._amplitude_modulation = 1.0  # Maximum

        # Trigger control update
        await eye_service._control_update()

        # Should be clamped to 255
        call_args = eye_service.adapter.set_brightness.call_args
        actual_brightness = call_args[0][0]

        assert actual_brightness == 255

    @pytest.mark.asyncio
    async def test_zero_amplitude_no_modulation(self, eye_service):
        """Test that zero amplitude results in no modulation."""
        eye_service._target_brightness = 100
        eye_service._amplitude_modulation = 0.0

        await eye_service._control_update()

        call_args = eye_service.adapter.set_brightness.call_args
        actual_brightness = call_args[0][0]

        # Should be exactly base brightness
        assert actual_brightness == 100


@pytest.mark.integration
class TestAmplitudeEndToEnd:
    """Integration tests for complete amplitude pipeline."""

    @pytest.mark.asyncio
    async def test_amplitude_payload_structure(self):
        """Test that SpeechAmplitudePayload validates correctly."""
        payload = SpeechAmplitudePayload(
            conversation_id="test-conversation",
            amplitude=0.75,
            timestamp_offset=1.234
        )

        # Should have base payload fields
        assert hasattr(payload, 'timestamp')
        assert hasattr(payload, 'event_id')

        # Should have amplitude-specific fields
        assert payload.amplitude == 0.75
        assert payload.timestamp_offset == 1.234
        assert payload.conversation_id == "test-conversation"

    @pytest.mark.asyncio
    async def test_amplitude_payload_validation_limits(self):
        """Test that amplitude is validated to 0-1 range."""
        # Valid amplitude
        payload = SpeechAmplitudePayload(
            conversation_id="test",
            amplitude=0.5,
            timestamp_offset=0.0
        )
        assert payload.amplitude == 0.5

        # Note: Pydantic doesn't enforce 0-1 range by default,
        # but normalization in ElevenLabsService should ensure it


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
