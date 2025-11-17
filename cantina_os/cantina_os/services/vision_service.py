"""
Vision Service for CantinaOS

Provides vision capabilities:
- Scene understanding via Claude Haiku vision
- Continuous face recognition for person identification
- Real-time person tracking with event emissions
"""

import asyncio
import base64
import io
import logging
import os
import pickle
import time
from typing import Optional, Dict, List

import cv2
import numpy as np
from anthropic import Anthropic
from PIL import Image

try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False

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

        # Continuous monitoring configuration
        self.enable_continuous_monitoring = self.config.get("enable_continuous_monitoring", True)
        self.monitoring_fps = self.config.get("monitoring_fps", 5)  # 5 FPS = every 5th frame at 30fps camera
        self.face_confidence_threshold = self.config.get("face_confidence_threshold", 0.6)

        # Face recognition state
        self.known_face_encodings: Dict[str, np.ndarray] = {}
        self.face_encodings_path = self.config.get("face_encodings_path", "vision_data/face_encodings.pkl")
        self._load_face_encodings()

        # State
        self.camera = None
        self.startup_scene_captured = False
        self._monitoring_task: Optional[asyncio.Task] = None
        self._monitoring_running = False

        # Person tracking state (for event emission on changes only)
        self._current_person: Optional[str] = None
        self._current_person_confidence: float = 0.0
        self._person_detection_time: Optional[float] = None
        self._no_person_frames = 0  # Frames with no face detected
        self._person_exit_threshold = 10  # Frames before emitting PERSON_EXITED (2 seconds at 5 FPS)

    def _load_face_encodings(self):
        """Load trained face encodings from disk."""
        if not FACE_RECOGNITION_AVAILABLE:
            self.logger.warning("face_recognition library not available, person detection disabled")
            return

        try:
            if os.path.exists(self.face_encodings_path):
                with open(self.face_encodings_path, 'rb') as f:
                    self.known_face_encodings = pickle.load(f)
                self.logger.info(f"Loaded face encodings for {len(self.known_face_encodings)} people: {list(self.known_face_encodings.keys())}")
            else:
                self.logger.warning(f"No face encodings found at {self.face_encodings_path}, person recognition disabled")
        except Exception as e:
            self.logger.error(f"Failed to load face encodings: {e}")

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

        # Start continuous monitoring if enabled
        if self.enable_continuous_monitoring and FACE_RECOGNITION_AVAILABLE and self.known_face_encodings:
            self.logger.info("Starting continuous vision monitoring for person detection")
            self._monitoring_running = True
            self._monitoring_task = asyncio.create_task(self._continuous_monitoring_loop())
        else:
            if not self.enable_continuous_monitoring:
                self.logger.info("Continuous monitoring disabled in config")
            elif not FACE_RECOGNITION_AVAILABLE:
                self.logger.warning("Continuous monitoring disabled: face_recognition not available")
            elif not self.known_face_encodings:
                self.logger.warning("Continuous monitoring disabled: no face encodings loaded")

    async def _stop(self):
        """Stop the vision service and release camera."""
        # Stop continuous monitoring
        if self._monitoring_task:
            self._monitoring_running = False
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            self._monitoring_task = None

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

    async def _continuous_monitoring_loop(self):
        """
        Continuous vision monitoring loop for person detection.

        Runs at configured FPS (default 5 FPS) to detect known people.
        Emits VISION_PERSON_DETECTED when a new person appears.
        Emits VISION_PERSON_EXITED when a person leaves for >2 seconds.
        """
        self.logger.info("Continuous vision monitoring loop started")

        try:
            # Open camera for continuous monitoring
            self.camera = cv2.VideoCapture(self.camera_index)
            if not self.camera.isOpened():
                self.logger.error(f"Failed to open camera {self.camera_index} for monitoring")
                return

            # Set camera resolution
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_width)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_height)

            # Wait for camera to stabilize
            await asyncio.sleep(0.5)

            frame_delay = 1.0 / self.monitoring_fps  # Sleep time between frames

            while self._monitoring_running:
                try:
                    # Capture frame
                    ret, frame = await asyncio.to_thread(self.camera.read)
                    if not ret or frame is None:
                        self.logger.warning("Failed to capture frame, retrying...")
                        await asyncio.sleep(frame_delay)
                        continue

                    # Recognize faces in frame (runs in thread to avoid blocking)
                    person_name, confidence = await asyncio.to_thread(self._recognize_face, frame)

                    # Handle person detection state changes
                    await self._handle_person_detection(person_name, confidence)

                    # Sleep to maintain target FPS
                    await asyncio.sleep(frame_delay)

                except Exception as e:
                    self.logger.error(f"Error in monitoring loop iteration: {e}")
                    await asyncio.sleep(frame_delay)

        except Exception as e:
            self.logger.error(f"Fatal error in continuous monitoring loop: {e}")
        finally:
            if self.camera:
                self.camera.release()
                self.camera = None
            self.logger.info("Continuous vision monitoring loop stopped")

    def _recognize_face(self, frame: np.ndarray) -> tuple[Optional[str], float]:
        """
        Recognize known faces in a frame using face_recognition library.

        Args:
            frame: OpenCV frame (BGR format)

        Returns:
            Tuple of (person_name, confidence) or (None, 0.0) if no face detected
        """
        if not FACE_RECOGNITION_AVAILABLE or not self.known_face_encodings:
            return None, 0.0

        try:
            # Convert BGR to RGB (face_recognition uses RGB)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Detect face locations (using HOG - faster than CNN)
            face_locations = face_recognition.face_locations(rgb_frame, model="hog")

            if not face_locations:
                return None, 0.0

            # Get face encodings for detected faces
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

            if not face_encodings:
                return None, 0.0

            # Use the first detected face (assuming single person in frame)
            face_encoding = face_encodings[0]

            # Compare against all known faces
            best_match_name = None
            best_match_distance = 1.0  # Lower is better, 0.6 is typical threshold

            for name, known_encoding in self.known_face_encodings.items():
                # Compute face distance (lower = more similar)
                distance = face_recognition.face_distance([known_encoding], face_encoding)[0]

                if distance < best_match_distance:
                    best_match_distance = distance
                    best_match_name = name

            # Convert distance to confidence (1.0 - distance)
            # Only return match if distance is below threshold
            if best_match_distance < (1.0 - self.face_confidence_threshold):
                confidence = 1.0 - best_match_distance
                return best_match_name, confidence
            else:
                return None, 0.0

        except Exception as e:
            self.logger.error(f"Error recognizing face: {e}")
            return None, 0.0

    async def _handle_person_detection(self, person_name: Optional[str], confidence: float):
        """
        Handle person detection state changes and emit appropriate events.

        Only emits events when state CHANGES (new person appears or exits).
        Uses hysteresis to prevent flickering (person must be gone for N frames before exit event).
        """
        current_time = time.time()

        if person_name:
            # Face detected
            self._no_person_frames = 0  # Reset exit counter

            # Check if this is a NEW person or confidence changed significantly
            if person_name != self._current_person:
                # New person detected!
                self.logger.info(f"Person detected: {person_name} (confidence: {confidence:.2f})")

                # Emit VISION_PERSON_DETECTED event
                self._event_bus.emit(
                    EventTopics.VISION_PERSON_DETECTED,
                    {
                        "name": person_name,
                        "confidence": confidence,
                        "timestamp": current_time
                    }
                )

                # Update internal state
                self._current_person = person_name
                self._current_person_confidence = confidence
                self._person_detection_time = current_time
            else:
                # Same person, just update confidence if significantly different
                if abs(confidence - self._current_person_confidence) > 0.1:
                    self._current_person_confidence = confidence
        else:
            # No face detected
            if self._current_person:
                # Person was present before
                self._no_person_frames += 1

                # Emit exit event only after threshold frames (prevent flicker)
                if self._no_person_frames >= self._person_exit_threshold:
                    duration = current_time - self._person_detection_time if self._person_detection_time else 0.0
                    self.logger.info(f"Person exited: {self._current_person} (duration: {duration:.1f}s)")

                    # Emit VISION_PERSON_EXITED event
                    self._event_bus.emit(
                        EventTopics.VISION_PERSON_EXITED,
                        {
                            "name": self._current_person,
                            "duration_seconds": duration,
                            "timestamp": current_time
                        }
                    )

                    # Clear person state
                    self._current_person = None
                    self._current_person_confidence = 0.0
                    self._person_detection_time = None
                    self._no_person_frames = 0