"""
Eye Light Controller Service V3 Patch

This patch simplifies the eye light controller to use the V3 protocol.
Apply this by replacing key methods in eye_light_controller_service.py
"""

# Add this import at the top
from cantina_os.adapters.simple_eye_adapter_v3 import SimpleEyeAdapterV3

# Replace _connect_to_arduino method:
async def _connect_to_arduino(self) -> None:
    """Establish connection to Arduino using V3 adapter."""
    if not self.serial_port:
        self.logger.error("No serial port specified")
        self.connected = False
        return

    try:
        self.logger.info(f"Connecting to Arduino V3 at {self.serial_port}")

        # Use V3 adapter
        self.adapter = SimpleEyeAdapterV3(
            serial_port=self.serial_port,
            baud_rate=self.baud_rate,
            timeout=self.command_timeout,
            logger=self.logger
        )

        # Connect
        self.connected = await self.adapter.connect()

        if self.connected:
            self.logger.info("Successfully connected to Arduino V3")
            # Set initial state
            await self.adapter.set_state("idle")
        else:
            self.logger.error("Failed to connect to Arduino")
            self.adapter = None

    except Exception as e:
        self.logger.error(f"Error connecting: {e}")
        self.connected = False
        self.adapter = None


# Replace _handle_mode_change method:
async def _handle_mode_change(self, event_payload) -> None:
    """Handle system mode changes with V3 states."""
    try:
        # Extract new mode
        if isinstance(event_payload, dict):
            new_mode = event_payload.get("new_mode", "").upper()
        elif hasattr(event_payload, "new_mode"):
            new_mode = event_payload.new_mode.upper()
        else:
            return

        self._current_system_mode = new_mode
        self.logger.info(f"System mode changed to: {new_mode}")

        if not self.adapter:
            return

        # Map system modes to Arduino states
        if new_mode == "IDLE":
            await self.adapter.set_state("idle")

        elif new_mode == "INTERACTIVE":
            await self.adapter.set_state("engaged")

        elif new_mode == "AMBIENT":
            await self.adapter.set_state("idle")

        elif new_mode == "SLEEPING":
            await self.adapter.set_state("idle")

    except Exception as e:
        self.logger.error(f"Error handling mode change: {e}")


# Replace _handle_voice_listening_started method:
async def _handle_voice_listening_started(self, event_payload) -> None:
    """Handle voice listening started."""
    if self._is_in_interactive_mode() and self.adapter:
        self.logger.info("Voice recording started, setting LISTENING state")
        await self.adapter.set_state("listening")


# Replace _handle_mouse_recording_stopped method:
async def _handle_mouse_recording_stopped(self, event_payload) -> None:
    """Handle recording stopped - trigger thinking."""
    if self._is_in_interactive_mode() and self.adapter:
        self.logger.info("Recording stopped, setting THINKING state")
        await self.adapter.set_state("thinking")


# Replace _handle_speech_started method:
async def _handle_speech_started(self, event_payload) -> None:
    """Handle speech started."""
    if self._is_in_interactive_mode() and self.adapter:
        self.logger.info("Speech started, setting SPEAKING state")
        await self.adapter.set_state("speaking")


# Replace _handle_speech_ended method:
async def _handle_speech_ended(self, event_payload) -> None:
    """Handle speech ended."""
    if self._is_in_interactive_mode() and self.adapter:
        self.logger.info("Speech ended, triggering flash and returning to ENGAGED")
        # Reset mouth
        await self.adapter.set_mouth_amplitude(0)
        # Flash confirmation
        await self.adapter.trigger_flash()
        # Note: Arduino automatically returns to ENGAGED after flash


# Simplify _handle_amplitude method:
async def _handle_amplitude(self, event_payload: dict) -> None:
    """Handle real-time audio amplitude for mouth."""
    try:
        from cantina_os.core.event_payloads import SpeechAmplitudePayload

        payload = SpeechAmplitudePayload.model_validate(event_payload)

        # Only send during SPEAKING state
        if self._current_system_mode == "INTERACTIVE" and self.adapter:
            # Scale amplitude 0.0-1.0 to 0-255
            # Apply boost for better dynamics
            amplitude = payload.amplitude * 8.0  # 8x boost as mentioned in original
            amplitude = min(1.0, amplitude)  # Cap at 1.0
            mouth_level = int(amplitude * 255)

            # Send to Arduino (fire-and-forget)
            await self.adapter.set_mouth_amplitude(mouth_level)

    except Exception as e:
        self.logger.error(f"Error handling amplitude: {e}")


# Disable control loop since V3 doesn't need it:
async def _control_update(self) -> None:
    """Control loop disabled for V3 - Arduino handles everything."""
    pass