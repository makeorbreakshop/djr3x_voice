"""
LED Visualizer Service

Provides a real-time visualization of DJ R3X's LED patterns using Pygame.
Displays 14 eye LEDs (2 rings of 7) and 8 mouth LEDs (V-shape) with glow effects.
Subscribes to speech amplitude, pattern changes, and sentiment events to mirror hardware behavior.

Hardware Layout:
- Eyes: 14 LEDs on pin 6
  - Left eye: LED 0 (center) + LEDs 1-6 (ring)
  - Right eye: LED 7 (center) + LEDs 8-13 (ring)
- Mouth: 8 LEDs on pin 5 in V-shape (0=top left, 7=top right)

Architecture:
- Inherits from RealtimeService with 60Hz rendering loop
- Event handlers set target state (_target_*)
- _control_update() reads target state and renders to Pygame window
- Thread-safe state management with proper resource cleanup
"""

import asyncio
import math
import time
from enum import Enum
from typing import Optional, Tuple, Dict, List
import logging

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    logging.warning("pygame not available - LED visualizer will be disabled")

from cantina_os.base_service import RealtimeService
from cantina_os.core.event_topics import EventTopics
from cantina_os.core.event_payloads import (
    BaseEventPayload,
    ServiceStatusPayload,
)


class EyePattern(str, Enum):
    """Eye LED patterns matching EyeLightControllerService"""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    ENGAGED = "ENGAGED"
    HAPPY = "HAPPY"
    SAD = "SAD"
    ANGRY = "ANGRY"
    SURPRISED = "SURPRISED"
    CONFUSED = "CONFUSED"
    FLASH = "FLASH"
    ERROR = "ERROR"
    OFF = "OFF"


class LEDVisualizerConfig:
    """Configuration for LED visualizer display"""
    def __init__(self):
        # Window settings
        self.window_width = 800
        self.window_height = 600
        self.window_title = "DJ R3X LED Visualizer"

        # LED visual settings
        self.eye_led_radius = 15
        self.eye_center_radius = 20
        self.eye_ring_radius = 80
        self.mouth_led_radius = 12
        self.glow_layers = 3

        # Layout positions
        self.left_eye_center = (200, 250)
        self.right_eye_center = (600, 250)
        self.mouth_center = (400, 450)

        # Colors (RGB)
        self.bg_color = (20, 20, 30)
        self.text_color = (200, 200, 200)
        self.status_bg_color = (40, 40, 50, 200)


class LEDState:
    """Represents the state of a single LED"""
    def __init__(self):
        self.color: Tuple[int, int, int] = (0, 0, 0)
        self.brightness: float = 0.0  # 0.0 to 1.0

    def set(self, color: Tuple[int, int, int], brightness: float = 1.0):
        """Set LED color and brightness"""
        self.color = color
        self.brightness = max(0.0, min(1.0, brightness))

    def get_display_color(self) -> Tuple[int, int, int]:
        """Get color adjusted for brightness"""
        return (
            int(self.color[0] * self.brightness),
            int(self.color[1] * self.brightness),
            int(self.color[2] * self.brightness)
        )


class LEDVisualizerService(RealtimeService):
    """
    Visualizes DJ R3X LED patterns in real-time using Pygame.

    Subscribes to:
    - SPEECH_SYNTHESIS_AMPLITUDE: Updates mouth LED brightness
    - EYE_PATTERN_CHANGED: Updates eye pattern
    - LLM_SENTIMENT_ANALYZED: Updates eye color based on sentiment
    - SYSTEM_MODE_CHANGED: Updates status display
    - LED_COMMAND: Direct LED commands from CLI

    Emits:
    - SERVICE_STATUS: Health and performance metrics
    """

    def __init__(self, event_bus, config: Optional[LEDVisualizerConfig] = None, logger=None):
        """
        Initialize LED visualizer service.

        Args:
            event_bus: Event bus for service communication
            config: Display configuration
            logger: Optional logger instance
        """
        super().__init__(
            service_name="led_visualizer",
            event_bus=event_bus,
            loop_rate_hz=60,
            logger=logger
        )

        self._config = config or LEDVisualizerConfig()

        # Target state (set by event handlers)
        self._target_eye_pattern: EyePattern = EyePattern.IDLE
        self._target_eye_color: Tuple[int, int, int] = (100, 150, 255)  # Default blue
        self._target_mouth_brightness: float = 0.0
        self._target_system_mode: str = "UNKNOWN"

        # Current LED states
        self._eye_leds: List[LEDState] = [LEDState() for _ in range(14)]
        self._mouth_leds: List[LEDState] = [LEDState() for _ in range(8)]

        # Pygame resources
        self._screen: Optional[pygame.Surface] = None
        self._clock: Optional[pygame.time.Clock] = None
        self._font: Optional[pygame.font.Font] = None
        self._running: bool = False

        # Animation state
        self._animation_time: float = 0.0
        self._frame_count: int = 0
        self._fps: float = 60.0
        self._last_fps_update: float = 0.0

        # Pattern color mappings
        self._pattern_colors: Dict[EyePattern, Tuple[int, int, int]] = {
            EyePattern.IDLE: (100, 150, 255),      # Blue
            EyePattern.LISTENING: (100, 255, 100), # Green
            EyePattern.THINKING: (255, 200, 100),  # Orange
            EyePattern.SPEAKING: (255, 150, 200),  # Pink
            EyePattern.ENGAGED: (150, 255, 150),   # Bright green
            EyePattern.HAPPY: (255, 255, 100),     # Yellow
            EyePattern.SAD: (100, 100, 255),       # Dark blue
            EyePattern.ANGRY: (255, 50, 50),       # Red
            EyePattern.SURPRISED: (255, 255, 255), # White
            EyePattern.CONFUSED: (200, 100, 255),  # Purple
            EyePattern.FLASH: (255, 255, 255),     # White
            EyePattern.ERROR: (255, 0, 0),         # Red
            EyePattern.OFF: (0, 0, 0),             # Off
        }

    async def _start(self) -> None:
        """Initialize Pygame and subscribe to events"""
        try:
            if not PYGAME_AVAILABLE:
                self._logger.error("pygame not available - visualizer cannot start")
                await self._emit_status("error", "pygame not installed")
                return

            # Initialize Pygame
            pygame.init()
            self._screen = pygame.display.set_mode(
                (self._config.window_width, self._config.window_height)
            )
            pygame.display.set_caption(self._config.window_title)
            self._clock = pygame.time.Clock()
            self._font = pygame.font.SysFont("monospace", 14)
            self._running = True

            # Subscribe to events
            await self._setup_subscriptions()

            self._logger.info("LED visualizer started successfully")
            await self._emit_status("running", "Visualizer active")

        except Exception as e:
            self._logger.error(f"Failed to start LED visualizer: {e}", exc_info=True)
            await self._emit_status("error", f"Startup failed: {e}")
            raise

    async def _stop(self) -> None:
        """Clean up Pygame resources"""
        try:
            self._running = False

            if PYGAME_AVAILABLE and pygame.get_init():
                pygame.quit()

            self._logger.info("LED visualizer stopped")
            await self._emit_status("stopped", "Visualizer closed")

        except Exception as e:
            self._logger.error(f"Error stopping LED visualizer: {e}", exc_info=True)

    async def _setup_subscriptions(self) -> None:
        """Subscribe to relevant events"""
        asyncio.create_task(
            self.subscribe(EventTopics.SPEECH_SYNTHESIS_AMPLITUDE, self._handle_speech_amplitude)
        )
        asyncio.create_task(
            self.subscribe(EventTopics.EYE_PATTERN_CHANGED, self._handle_eye_pattern_change)
        )
        asyncio.create_task(
            self.subscribe(EventTopics.LLM_SENTIMENT_ANALYZED, self._handle_sentiment_change)
        )
        asyncio.create_task(
            self.subscribe(EventTopics.SYSTEM_MODE_CHANGED, self._handle_mode_change)
        )
        asyncio.create_task(
            self.subscribe(EventTopics.LED_COMMAND, self._handle_led_command)
        )

    async def _control_update(self) -> None:
        """
        Called 60 times per second by RealtimeService.
        Renders the LED visualization based on target state.
        """
        if not self._running or not self._screen:
            return

        try:
            # Handle Pygame events (window close, etc.)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._logger.info("Window closed by user")
                    self._running = False
                    return

            # Update animation time
            self._animation_time += 1.0 / 60.0
            self._frame_count += 1

            # Update FPS calculation
            current_time = time.time()
            if current_time - self._last_fps_update >= 1.0:
                self._fps = self._frame_count / (current_time - self._last_fps_update)
                self._frame_count = 0
                self._last_fps_update = current_time

            # Update LED states based on target pattern
            self._update_eye_leds()
            self._update_mouth_leds()

            # Render to screen
            self._render_frame()

            # Limit to 60 FPS
            if self._clock:
                self._clock.tick(60)

        except Exception as e:
            self._logger.error(f"Error in control update: {e}", exc_info=True)

    def _update_eye_leds(self) -> None:
        """Update eye LED states based on current pattern"""
        pattern = self._target_eye_pattern
        base_color = self._pattern_colors.get(pattern, self._target_eye_color)

        # Apply pattern-specific animations
        if pattern == EyePattern.IDLE:
            # Slow breathing effect
            brightness = 0.3 + 0.2 * math.sin(self._animation_time * 2)
            for led in self._eye_leds:
                led.set(base_color, brightness)

        elif pattern == EyePattern.LISTENING:
            # Pulsing effect
            brightness = 0.5 + 0.5 * math.sin(self._animation_time * 4)
            for led in self._eye_leds:
                led.set(base_color, brightness)

        elif pattern == EyePattern.THINKING:
            # Rotating wave effect
            for i, led in enumerate(self._eye_leds):
                phase = (i / 14.0) * math.pi * 2
                brightness = 0.5 + 0.5 * math.sin(self._animation_time * 3 + phase)
                led.set(base_color, brightness)

        elif pattern == EyePattern.SPEAKING:
            # Bright and steady
            for led in self._eye_leds:
                led.set(base_color, 0.9)

        elif pattern == EyePattern.FLASH:
            # Fast strobe
            brightness = 1.0 if int(self._animation_time * 10) % 2 == 0 else 0.0
            for led in self._eye_leds:
                led.set(base_color, brightness)

        elif pattern == EyePattern.OFF:
            # All off
            for led in self._eye_leds:
                led.set((0, 0, 0), 0.0)

        else:
            # Default: solid color
            for led in self._eye_leds:
                led.set(base_color, 0.8)

    def _update_mouth_leds(self) -> None:
        """Update mouth LED states based on speech amplitude"""
        mouth_color = (255, 100, 150)  # Pink/red for mouth

        # Map brightness to mouth LEDs with V-shape emphasis
        # Center LEDs (1,2,5,6) are brightest, outer LEDs dimmer
        v_shape_weights = [0.6, 1.0, 1.0, 0.8, 0.8, 1.0, 1.0, 0.6]

        for i, (led, weight) in enumerate(zip(self._mouth_leds, v_shape_weights)):
            brightness = self._target_mouth_brightness * weight
            led.set(mouth_color, brightness)

    def _render_frame(self) -> None:
        """Render the complete frame to the Pygame window"""
        # Clear screen
        self._screen.fill(self._config.bg_color)

        # Render eyes
        self._render_eye_ring(self._config.left_eye_center, self._eye_leds[0:7])
        self._render_eye_ring(self._config.right_eye_center, self._eye_leds[7:14])

        # Render mouth
        self._render_mouth_v(self._config.mouth_center, self._mouth_leds)

        # Render status overlay
        self._render_status_overlay()

        # Update display
        pygame.display.flip()

    def _render_eye_ring(self, center: Tuple[int, int], leds: List[LEDState]) -> None:
        """
        Render an eye ring with center pupil and 6 surrounding LEDs.

        Args:
            center: (x, y) position of eye center
            leds: List of 7 LEDState objects (0=center, 1-6=ring)
        """
        # Render center pupil (LED 0)
        self._render_led_with_glow(
            center,
            self._config.eye_center_radius,
            leds[0].get_display_color()
        )

        # Render ring LEDs (LEDs 1-6)
        for i in range(6):
            angle = (i / 6.0) * math.pi * 2 - math.pi / 2  # Start at top
            x = center[0] + int(math.cos(angle) * self._config.eye_ring_radius)
            y = center[1] + int(math.sin(angle) * self._config.eye_ring_radius)

            self._render_led_with_glow(
                (x, y),
                self._config.eye_led_radius,
                leds[i + 1].get_display_color()
            )

    def _render_mouth_v(self, center: Tuple[int, int], leds: List[LEDState]) -> None:
        """
        Render mouth LEDs in V-shape.

        Layout: 0 (top left) ... 7 (top right)

        Args:
            center: (x, y) center position of mouth
            leds: List of 8 LEDState objects
        """
        # V-shape positions
        v_positions = [
            (-80, -20),  # 0: top left
            (-50, 0),    # 1: inner left
            (-30, 10),   # 2: inner left bottom
            (-10, 15),   # 3: center left
            (10, 15),    # 4: center right
            (30, 10),    # 5: inner right bottom
            (50, 0),     # 6: inner right
            (80, -20),   # 7: top right
        ]

        for i, (dx, dy) in enumerate(v_positions):
            pos = (center[0] + dx, center[1] + dy)
            self._render_led_with_glow(
                pos,
                self._config.mouth_led_radius,
                leds[i].get_display_color()
            )

    def _render_led_with_glow(self, pos: Tuple[int, int], radius: int, color: Tuple[int, int, int]) -> None:
        """
        Render an LED with glow effect.

        Args:
            pos: (x, y) position
            radius: LED radius
            color: RGB color tuple
        """
        # Skip if LED is off
        if color == (0, 0, 0):
            # Draw dark circle
            pygame.draw.circle(self._screen, (30, 30, 40), pos, radius)
            pygame.draw.circle(self._screen, (50, 50, 60), pos, radius, 1)
            return

        # Render glow layers (outer to inner)
        for layer in range(self._config.glow_layers, 0, -1):
            glow_radius = radius + (layer * radius // 2)
            glow_alpha = 0.3 / layer
            glow_color = tuple(int(c * glow_alpha) for c in color)

            # Create glow surface with alpha
            glow_surface = pygame.Surface((glow_radius * 2, glow_radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(
                glow_surface,
                (*glow_color, int(255 * glow_alpha)),
                (glow_radius, glow_radius),
                glow_radius
            )
            self._screen.blit(
                glow_surface,
                (pos[0] - glow_radius, pos[1] - glow_radius)
            )

        # Render solid LED core
        pygame.draw.circle(self._screen, color, pos, radius)

        # Add highlight
        highlight_color = tuple(min(255, c + 50) for c in color)
        pygame.draw.circle(
            self._screen,
            highlight_color,
            (pos[0] - radius // 3, pos[1] - radius // 3),
            radius // 3
        )

    def _render_status_overlay(self) -> None:
        """Render status information overlay"""
        padding = 10
        line_height = 20
        y_offset = padding

        # Create semi-transparent background
        overlay = pygame.Surface((250, 150), pygame.SRCALPHA)
        overlay.fill(self._config.status_bg_color)
        self._screen.blit(overlay, (padding, padding))

        # Render status text
        status_lines = [
            f"Pattern: {self._target_eye_pattern.value}",
            f"Mode: {self._target_system_mode}",
            f"Mouth: {int(self._target_mouth_brightness * 100)}%",
            f"FPS: {self._fps:.1f}",
            f"Time: {self._animation_time:.1f}s",
        ]

        for i, line in enumerate(status_lines):
            text_surface = self._font.render(line, True, self._config.text_color)
            self._screen.blit(
                text_surface,
                (padding + 10, y_offset + 10 + i * line_height)
            )

    # Event Handlers

    async def _handle_speech_amplitude(self, event_data: dict) -> None:
        """Handle speech amplitude events to update mouth brightness"""
        try:
            amplitude = event_data.get("amplitude", 0.0)
            # Clamp amplitude to [0.0, 1.0]
            self._target_mouth_brightness = max(0.0, min(1.0, amplitude))

        except Exception as e:
            self._logger.error(f"Error handling speech amplitude: {e}", exc_info=True)

    async def _handle_eye_pattern_change(self, event_data: dict) -> None:
        """Handle eye pattern change events"""
        try:
            pattern_name = event_data.get("pattern", "IDLE")

            # Convert string to enum
            try:
                self._target_eye_pattern = EyePattern(pattern_name)
            except ValueError:
                self._logger.warning(f"Unknown eye pattern: {pattern_name}")
                self._target_eye_pattern = EyePattern.IDLE

            self._logger.debug(f"Eye pattern changed to: {self._target_eye_pattern.value}")

        except Exception as e:
            self._logger.error(f"Error handling eye pattern change: {e}", exc_info=True)

    async def _handle_sentiment_change(self, event_data: dict) -> None:
        """Handle sentiment analysis events to update eye color"""
        try:
            sentiment = event_data.get("sentiment", "neutral")

            # Map sentiment to colors
            sentiment_colors = {
                "positive": (100, 255, 100),  # Green
                "negative": (255, 100, 100),  # Red
                "neutral": (100, 150, 255),   # Blue
                "happy": (255, 255, 100),     # Yellow
                "sad": (100, 100, 255),       # Dark blue
                "angry": (255, 50, 50),       # Bright red
            }

            self._target_eye_color = sentiment_colors.get(sentiment, (100, 150, 255))

            # Update pattern color mapping for current pattern
            if self._target_eye_pattern in self._pattern_colors:
                self._pattern_colors[self._target_eye_pattern] = self._target_eye_color

            self._logger.debug(f"Sentiment changed to: {sentiment}")

        except Exception as e:
            self._logger.error(f"Error handling sentiment change: {e}", exc_info=True)

    async def _handle_mode_change(self, event_data: dict) -> None:
        """Handle system mode change events"""
        try:
            mode = event_data.get("mode", "UNKNOWN")
            self._target_system_mode = mode
            self._logger.debug(f"System mode changed to: {mode}")

        except Exception as e:
            self._logger.error(f"Error handling mode change: {e}", exc_info=True)

    async def _handle_led_command(self, event_data: dict) -> None:
        """Handle direct LED command events from CLI"""
        try:
            command = event_data.get("command", "")

            # Parse command (e.g., "S3" for state change, "M128" for mouth brightness)
            if command.startswith("S"):
                # State/pattern change
                state_code = command[1:]
                # Map state codes to patterns (example mapping)
                state_map = {
                    "0": EyePattern.OFF,
                    "1": EyePattern.IDLE,
                    "2": EyePattern.LISTENING,
                    "3": EyePattern.THINKING,
                    "4": EyePattern.SPEAKING,
                }
                if state_code in state_map:
                    self._target_eye_pattern = state_map[state_code]

            elif command.startswith("M"):
                # Mouth brightness (0-255)
                try:
                    brightness_255 = int(command[1:])
                    self._target_mouth_brightness = brightness_255 / 255.0
                except ValueError:
                    self._logger.warning(f"Invalid mouth brightness value: {command}")

        except Exception as e:
            self._logger.error(f"Error handling LED command: {e}", exc_info=True)

    async def _emit_status(self, status: str, message: str) -> None:
        """Emit service status event"""
        try:
            payload = ServiceStatusPayload(
                service_name=self._service_name,
                status=status,
                message=message,
                timestamp=time.time()
            )
            self._event_bus.emit(EventTopics.SERVICE_STATUS, payload.model_dump())
        except Exception as e:
            self._logger.error(f"Failed to emit status: {e}", exc_info=True)
