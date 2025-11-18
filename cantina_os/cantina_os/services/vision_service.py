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
import pickle
import subprocess
import time
from typing import Optional, List, Dict

import cv2
import numpy as np
from anthropic import Anthropic
from PIL import Image

# Optional face_recognition import
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

        # Scene capture state
        self._last_scene_capture_time: Optional[float] = None
        self._scene_staleness_threshold = 300.0  # 5 minutes in seconds
        self._frame_count = 0  # Track frames for first-frame detection

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

    def _load_face_encodings(self):
        """Load trained face encodings from disk.

        Supports two formats:
        1. Legacy format: {'encodings': [enc1, enc2, ...], 'names': ['name1', 'name2', ...]}
        2. New format: {'name1': encoding1, 'name2': encoding2, ...}
        """
        if not FACE_RECOGNITION_AVAILABLE:
            self.logger.warning("face_recognition library not available, person detection disabled")
            return

        try:
            if os.path.exists(self.face_encodings_path):
                with open(self.face_encodings_path, 'rb') as f:
                    data = pickle.load(f)

                # Check if it's the legacy format (has 'encodings' and 'names' keys)
                if isinstance(data, dict) and 'encodings' in data and 'names' in data:
                    # Legacy format - convert to new format
                    self.logger.info("Converting legacy face encodings format to new format")
                    encodings_list = data['encodings']
                    names_list = data['names']

                    # Convert to new format: {name: encoding}
                    # If multiple encodings for same person, take the first one
                    # (or could average them, but this is simpler)
                    self.known_face_encodings = {}
                    for name, encoding in zip(names_list, encodings_list):
                        if name not in self.known_face_encodings:
                            self.known_face_encodings[name] = encoding
                        else:
                            # If duplicate, could log a warning
                            self.logger.debug(f"Skipping duplicate encoding for {name}")

                    self.logger.info(f"Loaded face encodings for {len(self.known_face_encodings)} people: {list(self.known_face_encodings.keys())}")
                elif isinstance(data, dict):
                    # New format - use directly
                    self.known_face_encodings = data
                    self.logger.info(f"Loaded face encodings for {len(self.known_face_encodings)} people: {list(self.known_face_encodings.keys())}")
                else:
                    self.logger.error(f"Unknown face encodings format: {type(data)}")
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

        # Subscribe to vision window open requests
        self._event_bus.on(EventTopics.VISION_WINDOW_OPEN, self._handle_vision_window_open)

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

    async def _continuous_monitoring_loop(self):
        """
        Unified continuous monitoring loop for person detection AND scene capture.

        Runs at configured FPS (default 5 FPS) to:
        - Detect known people (every frame, local)
        - Capture scene descriptions (smart triggers, Claude API)

        Smart scene capture triggers:
        1. First frame (startup)
        2. Person state changes (new person enters/exits)
        3. Scene staleness (>5 minutes since last capture)
        4. On-demand requests (via events)

        Emits VISION_PERSON_DETECTED when a new person appears.
        Emits VISION_PERSON_EXITED when a person leaves for >2 seconds.
        Emits VISION_SCENE_CAPTURED when scene is analyzed.
        """
        self.logger.info("Continuous vision monitoring loop started (unified face recognition + scene capture)")

        try:
            # Open camera for continuous monitoring
            self.camera = cv2.VideoCapture(self.camera_index)
            if not self.camera.isOpened():
                self.logger.error(f"Failed to open camera {self.camera_index} for monitoring")
                return

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

                    self._frame_count += 1

                    # Recognize faces in frame (runs in thread to avoid blocking)
                    person_name, confidence = await asyncio.to_thread(self._recognize_face, frame)

                    # Handle person detection state changes
                    # This will emit PERSON_DETECTED/EXITED events and trigger scene capture if needed
                    await self._handle_person_detection(person_name, confidence, frame)

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

    async def _handle_person_detection(self, person_name: Optional[str], confidence: float, frame: np.ndarray):
        """
        Handle person detection state changes and emit appropriate events.

        Only emits events when state CHANGES (new person appears or exits).
        Uses hysteresis to prevent flickering (person must be gone for N frames before exit event).

        Also triggers smart scene capture based on:
        - First frame (startup)
        - Person state changes
        - Scene staleness (>5 minutes)

        Args:
            person_name: Detected person's name (or None)
            confidence: Detection confidence (0.0-1.0)
            frame: Current frame from camera
        """
        current_time = time.time()
        should_capture_scene = False
        capture_reason = None

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
                old_person = self._current_person
                self._current_person = person_name
                self._current_person_confidence = confidence
                self._person_detection_time = current_time

                # Trigger scene capture on person change
                should_capture_scene = True
                capture_reason = f"person_changed_from_{old_person or 'none'}_to_{person_name}"
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

                    # Trigger scene capture on person exit
                    should_capture_scene = True
                    capture_reason = f"person_exited_{self._current_person}"

                    # Clear person state
                    self._current_person = None
                    self._current_person_confidence = 0.0
                    self._person_detection_time = None
                    self._no_person_frames = 0

        # Check for first frame (startup scene capture)
        if self._frame_count == 1 and not self.startup_scene_captured:
            should_capture_scene = True
            capture_reason = "startup_first_frame"

        # Check for scene staleness (>5 minutes since last capture)
        if (self._last_scene_capture_time is not None and
            current_time - self._last_scene_capture_time > self._scene_staleness_threshold):
            should_capture_scene = True
            capture_reason = "scene_staleness_exceeded"

        # Capture scene if needed
        if should_capture_scene:
            await self._capture_scene_with_person(frame, person_name, confidence, capture_reason)

    async def _capture_scene_with_person(self, frame: np.ndarray, person_name: Optional[str],
                                         confidence: float, reason: str):
        """
        Capture and analyze scene with enriched context about person detection.

        Args:
            frame: Current frame from camera
            person_name: Detected person's name (or None)
            confidence: Detection confidence
            reason: Why scene capture was triggered
        """
        self.logger.info(f"Capturing scene (reason: {reason})")

        try:
            # Build context-aware prompt for Claude
            if person_name:
                prompt = (
                    f"Describe the scene you see. "
                    f"Face recognition detected {person_name} (confidence: {confidence:.2f}). "
                    f"Focus on the environment, objects, and activities. Keep it concise (under 100 words)."
                )
            else:
                prompt = (
                    "Describe the scene you see. "
                    "No known person detected in frame. "
                    "Focus on the environment and any notable objects. Keep it concise (under 100 words)."
                )

            # Analyze scene using Claude vision API
            description = await self._analyze_scene(frame, prompt)

            # Emit scene captured event
            payload = VisionScenePayload(
                description=description,
                camera_index=self.camera_index,
                metadata={
                    "capture_reason": reason,
                    "person_detected": person_name,
                    "confidence": confidence if person_name else 0.0,
                    "frame_count": self._frame_count
                }
            )
            self._event_bus.emit(
                EventTopics.VISION_SCENE_CAPTURED,
                payload.model_dump()
            )

            # Update scene capture state
            self._last_scene_capture_time = time.time()
            if reason == "startup_first_frame":
                self.startup_scene_captured = True
                # Send CLI notification about startup scene
                self._event_bus.emit(
                    EventTopics.CLI_RESPONSE,
                    {"message": f"📷 Vision initialized: {description[:80]}...", "type": "info"}
                )

            self.logger.info(f"Scene captured successfully: {description[:100]}...")

        except Exception as e:
            self.logger.error(f"Failed to capture scene: {e}")
            await self._emit_error(str(e), "scene_capture")

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