"""
Vision Service for CantinaOS

Captures and analyzes visual scenes using Claude Haiku's vision capabilities.
Runs a single scene capture during startup to provide initial environmental context.
"""

import asyncio
import base64
import io
import logging
import os
import time
from typing import Optional

import cv2
import numpy as np
from anthropic import Anthropic
from PIL import Image

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.event_payloads import (
    VisionScenePayload,
    VisionErrorPayload,
    VisionRequestPayload,
)


class VisionService(BaseService):
    """
    Service for visual scene understanding using Claude Haiku vision model.

    This service:
    - Captures a single frame during startup
    - Sends it to Claude Haiku for scene description
    - Stores the description in MemoryService for context
    - Can be triggered for on-demand vision analysis
    """

    def __init__(self, event_bus, config=None):
        super().__init__(service_name="vision", event_bus=event_bus)
        self.config = config or {}

        # Camera configuration
        self.camera_index = self.config.get("camera_index", 0)
        self.capture_width = self.config.get("capture_width", 640)
        self.capture_height = self.config.get("capture_height", 480)

        # Claude configuration
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            self.logger.error("ANTHROPIC_API_KEY not found in environment")
            self.vision_client = None
        else:
            self.vision_client = Anthropic(api_key=api_key)

        # Model to use (Haiku 4.5 is fast and cheap for vision)
        self.model = self.config.get("model", "claude-haiku-4-5-20251001")

        # State
        self.camera = None
        self.startup_scene_captured = False

    async def _start(self):
        """Start the vision service and subscribe to events."""
        await super()._start()

        if not self.vision_client:
            self.logger.error("Vision service cannot start without ANTHROPIC_API_KEY")
            await self._emit_error("Missing API key", "configuration")
            return

        # Subscribe to on-demand vision requests
        self._event_bus.on(EventTopics.VISION_ON_DEMAND_REQUEST, self._handle_vision_request)

        # Subscribe to startup scene capture request (triggered after all services are ready)
        self._event_bus.on(EventTopics.VISION_STARTUP_CAPTURE, self._handle_startup_capture_request)

    async def _stop(self):
        """Stop the vision service and release camera."""
        if self.camera:
            self.camera.release()
            self.camera = None
        await super()._stop()

    async def _handle_startup_capture_request(self, payload: dict) -> None:
        """Handle request to capture startup scene (after all services are ready)."""
        if not self.startup_scene_captured:
            await self._capture_startup_scene()

    async def _capture_startup_scene(self):
        """Capture and analyze the initial scene during startup."""
        self.logger.info("Capturing startup scene...")

        try:
            # Initialize camera
            self.camera = cv2.VideoCapture(self.camera_index)
            if not self.camera.isOpened():
                raise Exception(f"Failed to open camera {self.camera_index}")

            # Set camera resolution
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_width)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_height)

            # Wait a moment for camera to stabilize
            await asyncio.sleep(0.5)

            # Capture a few frames (camera often needs warmup)
            for _ in range(5):
                ret, frame = self.camera.read()
                if not ret:
                    raise Exception("Failed to capture frame")
                await asyncio.sleep(0.1)

            # Use the last frame for analysis
            if frame is not None:
                # Analyze the scene
                description = await self._analyze_scene(frame,
                    "Describe what you see in this scene. Focus on the environment, any people present, and notable objects. Keep it concise (under 100 words).")

                # Emit the scene captured event (use model_dump for Pydantic model)
                payload = VisionScenePayload(
                    description=description,
                    camera_index=self.camera_index,
                    metadata={"startup": True}
                )
                self._event_bus.emit(
                    EventTopics.VISION_SCENE_CAPTURED,
                    payload.model_dump()
                )

                self.startup_scene_captured = True
                self.logger.info(f"Startup scene captured: {description[:100]}...")

                # Send CLI notification about scene capture
                self._event_bus.emit(
                    EventTopics.CLI_RESPONSE,
                    {"message": f"📷 Vision initialized: {description[:80]}...", "type": "info"}
                )

            # Release camera after startup (we'll reopen if needed)
            self.camera.release()
            self.camera = None

        except Exception as e:
            self.logger.error(f"Failed to capture startup scene: {e}")
            await self._emit_error(str(e), "camera")

    async def _analyze_scene(self, frame: np.ndarray, prompt: str) -> str:
        """
        Send a frame to Claude Haiku for analysis.

        Args:
            frame: OpenCV frame (BGR format)
            prompt: The question to ask about the image

        Returns:
            Text description from Claude
        """
        start_time = time.time()

        try:
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Convert to PIL Image
            pil_image = Image.fromarray(rgb_frame)

            # Compress to JPEG with reasonable quality
            buffered = io.BytesIO()
            pil_image.save(buffered, format="JPEG", quality=85)
            image_data = buffered.getvalue()

            # Encode to base64
            image_b64 = base64.b64encode(image_data).decode('utf-8')

            # Send to Claude Haiku
            response = await asyncio.to_thread(
                self.vision_client.messages.create,
                model=self.model,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }]
            )

            analysis_time = (time.time() - start_time) * 1000
            self.logger.debug(f"Scene analysis took {analysis_time:.0f}ms")

            return response.content[0].text

        except Exception as e:
            self.logger.error(f"Failed to analyze scene: {e}")
            raise

    async def _handle_vision_request(self, payload: VisionRequestPayload):
        """Handle on-demand vision analysis requests."""
        self.logger.info(f"Handling vision request: {payload.query}")

        try:
            # Reopen camera if needed
            if not self.camera:
                self.camera = cv2.VideoCapture(self.camera_index)
                if not self.camera.isOpened():
                    raise Exception(f"Failed to open camera {self.camera_index}")

                # Set camera resolution
                self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_width)
                self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_height)

                # Wait for camera to stabilize
                await asyncio.sleep(0.5)

            # Capture frame
            ret, frame = self.camera.read()
            if not ret or frame is None:
                raise Exception("Failed to capture frame")

            # Build prompt based on request
            if payload.query:
                prompt = payload.query
            elif payload.detailed:
                prompt = "Provide a detailed description of everything you see in this scene. Include people, their positions, objects, environment, lighting, and any notable details."
            else:
                prompt = "Describe what you see in this scene. Focus on people, objects, and the environment."

            # Analyze the scene
            start_time = time.time()
            description = await self._analyze_scene(frame, prompt)
            analysis_time = (time.time() - start_time) * 1000

            # Emit the updated scene (use model_dump for Pydantic model)
            scene_payload = VisionScenePayload(
                description=description,
                camera_index=self.camera_index,
                analysis_time_ms=analysis_time,
                conversation_id=payload.conversation_id,
                metadata={
                    "on_demand": True,
                    "query": payload.query,
                    "detailed": payload.detailed
                }
            )
            self._event_bus.emit(
                EventTopics.VISION_SCENE_UPDATED,
                scene_payload.model_dump()
            )

            self.logger.info(f"Vision analysis complete in {analysis_time:.0f}ms")

        except Exception as e:
            self.logger.error(f"Failed to handle vision request: {e}")
            await self._emit_error(str(e), "processing")

    async def _emit_error(self, error_message: str, error_type: str):
        """Emit a vision error event."""
        error_payload = VisionErrorPayload(
            error_message=error_message,
            error_type=error_type,
            camera_index=self.camera_index
        )
        self._event_bus.emit(
            EventTopics.VISION_SCENE_ERROR,
            error_payload.model_dump()
        )