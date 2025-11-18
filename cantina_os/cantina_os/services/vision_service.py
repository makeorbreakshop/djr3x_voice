"""
Vision Service for CantinaOS

Captures and analyzes visual scenes using Claude Haiku's vision capabilities.
Runs a single scene capture during startup to provide initial environmental context.
"""

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import time
from typing import Optional, List, Dict

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

        # Camera configuration - auto-detect best camera (skip Continuity Camera)
        self.camera_index = self._find_best_camera(self.config.get("camera_index"))

        # Claude configuration
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            self.logger.error("ANTHROPIC_API_KEY not found in environment")
            self.vision_client = None
        else:
            self.vision_client = Anthropic(api_key=api_key)

        # Model to use (Haiku 4.5 is fast and cheap for vision)
        self.model = "claude-haiku-4-5-20251001"

        # State
        self.camera = None
        self.startup_scene_captured = False

    def _get_camera_names(self) -> Dict[int, str]:
        """
        Get camera names from macOS system_profiler.
        Returns dict mapping OpenCV camera index to actual camera name.
        """
        cameras = {}

        try:
            # Get camera info as JSON
            result = subprocess.run(
                ['system_profiler', 'SPCameraDataType', '-json'],
                capture_output=True,
                text=True,
                timeout=5
            )

            data = json.loads(result.stdout)
            camera_list = data.get('SPCameraDataType', [])

            # Map system cameras to OpenCV indices
            # Test each OpenCV camera to match by resolution/properties
            opencv_cameras = {}
            for idx in range(6):
                try:
                    camera = cv2.VideoCapture(idx)
                    if camera.isOpened():
                        width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
                        height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        opencv_cameras[idx] = (width, height)
                        camera.release()
                except:
                    pass

            # Match cameras by typical resolutions
            # FaceTime HD is usually 1280x720, iPhone/Continuity is 1920x1080
            for idx, (width, height) in opencv_cameras.items():
                matched = False
                for cam_info in camera_list:
                    cam_name = cam_info.get('_name', '')

                    # Match FaceTime HD to 1280x720
                    if 'FaceTime' in cam_name and width == 1280 and height == 720:
                        cameras[idx] = cam_name
                        matched = True
                        break
                    # Match iPhone to 1920x1080
                    elif 'iPhone' in cam_name and width == 1920 and height == 1080:
                        cameras[idx] = cam_name
                        matched = True
                        break

                if not matched:
                    # Use generic name with resolution
                    cameras[idx] = f"Camera {idx} ({width}x{height})"

        except Exception as e:
            self.logger.debug(f"Could not get camera names from system_profiler: {e}")

        return cameras

    def _find_best_camera(self, preferred_index: Optional[int] = None) -> int:
        """
        Find the best available camera, avoiding Continuity Cameras (iPhones).

        Strategy:
        1. If preferred_index is provided, use it
        2. Otherwise, get camera names and pick first non-iPhone camera
        3. Fallback to index 0 if nothing found
        """
        if preferred_index is not None:
            self.logger.info(f"Using preferred camera index: {preferred_index}")
            return preferred_index

        # Get camera names to identify iPhones/Continuity cameras
        camera_names = self._get_camera_names()
        self.logger.info(f"Detected cameras: {camera_names}")

        # Pick the first camera that is NOT an iPhone
        for idx, camera_name in camera_names.items():
            # Skip iPhone cameras
            if 'iPhone' in camera_name:
                self.logger.info(f"Skipping camera {idx}: {camera_name}")
                continue

            # Use this camera
            self.logger.info(f"Selected camera {idx}: {camera_name}")
            return idx

        # Fallback to 0
        self.logger.warning("No suitable camera found in names, defaulting to index 0")
        return 0

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

        # Subscribe to vision window open requests
        self._event_bus.on(EventTopics.VISION_WINDOW_OPEN, self._handle_vision_window_open)

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
        self.logger.info(f"Capturing startup scene from camera index {self.camera_index}...")

        try:
            # Initialize camera (using auto-detected best camera)
            self.logger.debug(f"Opening camera {self.camera_index}...")
            self.camera = cv2.VideoCapture(self.camera_index)
            if not self.camera.isOpened():
                raise Exception(f"Failed to open camera {self.camera_index}")

            self.logger.debug(f"Camera {self.camera_index} opened successfully")

            # Get camera properties
            width = int(self.camera.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = self.camera.get(cv2.CAP_PROP_FPS)
            self.logger.info(f"Camera properties: {width}x{height} @ {fps} FPS")

            # Wait a moment for camera to stabilize
            self.logger.debug("Waiting for camera to stabilize...")
            await asyncio.sleep(0.5)

            # Capture a few frames (camera often needs warmup)
            self.logger.debug("Warming up camera with test captures...")
            for i in range(5):
                ret, frame = self.camera.read()
                if not ret:
                    raise Exception(f"Failed to capture frame {i+1}/5")
                if frame is not None:
                    brightness = frame.mean()
                    self.logger.debug(f"Frame {i+1}/5: {frame.shape}, brightness={brightness:.2f}")
                await asyncio.sleep(0.1)

            # Use the last frame for analysis
            if frame is not None:
                final_brightness = frame.mean()
                self.logger.info(f"Final frame captured: {frame.shape}, brightness={final_brightness:.2f}")
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

    async def _handle_vision_window_open(self, payload: dict) -> None:
        """
        Handle request to open vision detection window.

        This launches the test_vision.py script in a detached subprocess.
        Follows architecture standards by handling via VisionService instead of CommandDispatcher.

        Args:
            payload: Dict containing camera_id and mode
        """
        import sys
        from pathlib import Path
        import tempfile

        try:
            camera_id = payload.get("camera_id", 0)
            mode = payload.get("mode", "combined")

            # Get path to test_vision.py script
            # Path: cantina_os/cantina_os/services/vision_service.py
            # Target: cantina_os/test_vision.py
            script_path = Path(__file__).parent.parent.parent / "test_vision.py"

            if not script_path.exists():
                error_msg = f"Vision test script not found at: {script_path}"
                self.logger.error(error_msg)
                self._event_bus.emit(EventTopics.VISION_WINDOW_ERROR, {"error": error_msg})
                return

            # Get the Python executable from the venv
            venv_python = Path(__file__).parent.parent.parent.parent / "venv" / "bin" / "python"

            if not venv_python.exists():
                # Fallback to system Python if venv not found
                venv_python = sys.executable

            # Launch the script in background (non-blocking)
            # Log errors to a temp file for debugging
            error_log = tempfile.mktemp(suffix=".log", prefix="vision_")

            with open(error_log, 'w') as err_file:
                subprocess.Popen(
                    [str(venv_python), str(script_path), "--mode", mode, "--camera", str(camera_id)],
                    stdout=err_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True  # Detach from parent process
                )

            self.logger.info(f"Vision window launched successfully (mode={mode}, camera={camera_id}, log={error_log})")

            # Emit success event
            self._event_bus.emit(EventTopics.VISION_WINDOW_OPENED, {
                "camera_id": camera_id,
                "mode": mode,
                "log_file": error_log
            })

        except Exception as e:
            error_msg = f"Failed to launch vision window: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            self._event_bus.emit(EventTopics.VISION_WINDOW_ERROR, {"error": error_msg})

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