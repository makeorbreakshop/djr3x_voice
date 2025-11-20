#!/usr/bin/env python3
"""
Standalone Vision Test Script for DJ R3X

Tests face detection and recognition without integrating into CantinaOS.
This allows you to validate webcam functionality and collect training data.

Usage:
    # Test face detection only
    python test_vision.py --mode detect

    # Collect training data for a person
    python test_vision.py --mode train --name "Brandon"

    # Test face recognition
    python test_vision.py --mode recognize

Requirements:
    pip install opencv-python face-recognition pillow
"""

import cv2
import face_recognition
import argparse
import os
import time
import pickle
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np
from ultralytics import YOLO
import mediapipe as mp


class VisionTester:
    """Standalone vision testing class."""

    def __init__(self, camera_id: int = 0):
        """
        Initialize the vision tester.

        Args:
            camera_id: Camera device ID (0 for default webcam)
        """
        self.camera_id = camera_id
        self.camera = None

        # Directories for storing data
        self.base_dir = Path(__file__).parent / "vision_data"
        self.training_dir = self.base_dir / "training"
        self.encodings_file = self.base_dir / "face_encodings.pkl"

        # Create directories if they don't exist
        self.base_dir.mkdir(exist_ok=True)
        self.training_dir.mkdir(exist_ok=True)

        # Known face encodings
        self.known_face_encodings = []
        self.known_face_names = []

        # Performance tracking
        self.fps = 0
        self.frame_count = 0
        self.start_time = time.time()

    @staticmethod
    def get_camera_names() -> List[str]:
        """
        Get names of all camera devices (macOS specific).

        Returns:
            List of camera names
        """
        try:
            import subprocess
            # Use system_profiler to get camera names on macOS
            result = subprocess.run(
                ['system_profiler', 'SPCameraDataType'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Parse camera names from output
                # Camera names appear after "Camera:" and end with ":"
                lines = result.stdout.split('\n')
                camera_names = []

                for line in lines:
                    stripped = line.strip()
                    # Look for lines that end with ":" and contain camera names
                    if stripped.endswith(':') and stripped != 'Camera:':
                        # Remove the trailing colon
                        camera_name = stripped[:-1]
                        # Filter out section headers
                        if 'Model ID' not in camera_name and 'Unique ID' not in camera_name:
                            camera_names.append(camera_name)

                return camera_names
        except Exception:
            pass

        return []

    @staticmethod
    def get_camera_name(camera_id: int, all_names: List[str] = None) -> str:
        """
        Get the name/description of a camera device.

        Args:
            camera_id: Camera device ID
            all_names: Pre-fetched list of camera names (optional)

        Returns:
            Camera name or generic description
        """
        if all_names is None:
            all_names = VisionTester.get_camera_names()

        if camera_id < len(all_names):
            return all_names[camera_id]

        return f"Camera {camera_id}"

    @staticmethod
    def list_available_cameras(max_cameras: int = 10) -> List[Tuple[int, str, str]]:
        """
        List all available cameras on the system.

        Args:
            max_cameras: Maximum number of camera indices to check

        Returns:
            List of tuples (camera_id, camera_name, camera_info)
        """
        available_cameras = []

        print("Scanning for available cameras...")

        # Fetch all camera names once
        all_camera_names = VisionTester.get_camera_names()

        for camera_id in range(max_cameras):
            cap = cv2.VideoCapture(camera_id)

            if cap.isOpened():
                # Try to read a frame to ensure it's really working
                ret, _ = cap.read()

                if ret:
                    # Get camera properties
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = int(cap.get(cv2.CAP_PROP_FPS))

                    # Get camera name
                    camera_name = VisionTester.get_camera_name(camera_id, all_camera_names)

                    info = f"{width}x{height} @ {fps}fps"
                    available_cameras.append((camera_id, camera_name, info))

                cap.release()

        return available_cameras

    @staticmethod
    def select_camera_interactive() -> Optional[int]:
        """
        Interactive camera selection menu.

        Returns:
            Selected camera ID, or None if cancelled
        """
        available_cameras = VisionTester.list_available_cameras()

        if not available_cameras:
            print("❌ No cameras found!")
            return None

        print("\n" + "="*60)
        print("AVAILABLE CAMERAS")
        print("="*60)

        for camera_id, camera_name, info in available_cameras:
            print(f"  [{camera_id}] {camera_name} - {info}")

        print("="*60)

        # Get user selection
        while True:
            try:
                user_input = input(f"\nSelect camera [0-{len(available_cameras)-1}] or 'q' to quit: ").strip()

                if user_input.lower() == 'q':
                    return None

                camera_id = int(user_input)

                # Check if selected camera is in available list
                if any(cam_id == camera_id for cam_id, _, _ in available_cameras):
                    # Get camera name for confirmation
                    selected_name = next((name for cid, name, _ in available_cameras if cid == camera_id), f"Camera {camera_id}")
                    print(f"✅ Selected {selected_name}")
                    return camera_id
                else:
                    print(f"❌ Camera {camera_id} not available. Please choose from the list.")

            except ValueError:
                print("❌ Invalid input. Please enter a number or 'q'.")
            except KeyboardInterrupt:
                print("\n\nCancelled by user")
                return None

    def open_camera(self) -> bool:
        """
        Open the camera.

        Returns:
            True if successful, False otherwise
        """
        print(f"Opening camera {self.camera_id}...")
        self.camera = cv2.VideoCapture(self.camera_id)

        if not self.camera.isOpened():
            print(f"❌ Failed to open camera {self.camera_id}")
            return False

        # Set camera properties for Logitech C930e widescreen
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        self.camera.set(cv2.CAP_PROP_FPS, 30)

        print("✅ Camera opened successfully")
        print(f"   Resolution: {int(self.camera.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(self.camera.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
        print(f"   FPS: {int(self.camera.get(cv2.CAP_PROP_FPS))}")

        return True

    def close_camera(self):
        """Release the camera."""
        if self.camera is not None:
            self.camera.release()
            cv2.destroyAllWindows()
            print("Camera released")

    def update_fps(self):
        """Update FPS counter."""
        self.frame_count += 1
        elapsed = time.time() - self.start_time
        if elapsed > 1.0:
            self.fps = self.frame_count / elapsed
            self.frame_count = 0
            self.start_time = time.time()

    def test_detection(self):
        """
        Test basic face detection using OpenCV Haar Cascade.

        This is the fastest method (20-40 FPS on CPU).
        """
        print("\n" + "="*60)
        print("FACE DETECTION TEST (OpenCV Haar Cascade)")
        print("="*60)
        print("This tests basic face detection without recognition.")
        print("Press 'q' to quit")
        print()

        if not self.open_camera():
            return

        # Load Haar Cascade face detector
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        face_cascade = cv2.CascadeClassifier(cascade_path)

        if face_cascade.empty():
            print("❌ Failed to load Haar Cascade classifier")
            self.close_camera()
            return

        print("✅ Face detector loaded")
        print("\nStarting detection loop...")

        try:
            while True:
                ret, frame = self.camera.read()

                if not ret:
                    print("❌ Failed to read frame")
                    break

                # Convert to grayscale for detection
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                # Detect faces
                start = time.time()
                faces = face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(50, 50)
                )
                detection_time = (time.time() - start) * 1000

                # Draw rectangles around faces
                for (x, y, w, h) in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.putText(frame, "Face", (x, y - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

                # Update FPS
                self.update_fps()

                # Display info
                info_text = [
                    f"FPS: {self.fps:.1f}",
                    f"Faces: {len(faces)}",
                    f"Detection: {detection_time:.1f}ms"
                ]

                y_offset = 30
                for text in info_text:
                    cv2.putText(frame, text, (10, y_offset),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    y_offset += 30

                # Show frame
                cv2.imshow('Face Detection Test', frame)

                # Check for quit
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            self.close_camera()

    def collect_training_data(self, person_name: str, num_images: int = 20):
        """
        Collect training images for a person.

        Args:
            person_name: Name of the person
            num_images: Number of images to collect (default 20)
        """
        print("\n" + "="*60)
        print(f"COLLECTING TRAINING DATA FOR: {person_name}")
        print("="*60)
        print(f"Will collect {num_images} images")
        print("Position your face in frame and press SPACE to capture")
        print("Try different angles, expressions, and lighting")
        print("Press 'q' to quit early")
        print()

        if not self.open_camera():
            return

        # Create person directory
        person_dir = self.training_dir / person_name
        person_dir.mkdir(exist_ok=True)

        images_collected = 0
        frame_count = 0

        print(f"Saving images to: {person_dir}")
        print(f"Using face_recognition library (dlib HOG detector)")
        print(f"\nCollected: {images_collected}/{num_images}")

        try:
            while images_collected < num_images:
                ret, frame = self.camera.read()

                if not ret:
                    print("❌ Failed to read frame")
                    break

                frame_count += 1
                display_frame = frame.copy()

                # Only detect faces every 3rd frame for performance
                if frame_count % 3 == 0:
                    # Use face_recognition library for better accuracy
                    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                    rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                    face_locations = face_recognition.face_locations(rgb_small)

                    # Scale back up to full size
                    faces = [(top*4, right*4, bottom*4, left*4) for (top, right, bottom, left) in face_locations]
                else:
                    # Use cached face locations from previous frame
                    if 'faces' not in locals():
                        faces = []

                # Draw rectangles
                for (top, right, bottom, left) in faces:
                    color = (0, 255, 0) if len(faces) == 1 else (0, 255, 255)
                    cv2.rectangle(display_frame, (left, top), (right, bottom), color, 2)

                # Display instructions
                status_text = f"Collected: {images_collected}/{num_images}"
                instruction_text = "Press SPACE to capture" if len(faces) > 0 else "No face detected"

                cv2.putText(display_frame, status_text, (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(display_frame, instruction_text, (10, 60),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

                if len(faces) != 1:
                    cv2.putText(display_frame, "⚠ Ensure exactly ONE face in frame", (10, 90),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                cv2.imshow(f'Training Data Collection - {person_name}', display_frame)

                # Handle key presses
                key = cv2.waitKey(1) & 0xFF

                if key == ord(' ') and len(faces) == 1:
                    # Capture image
                    image_path = person_dir / f"{person_name}_{images_collected:03d}.jpg"
                    cv2.imwrite(str(image_path), frame)
                    images_collected += 1
                    print(f"✅ Captured image {images_collected}/{num_images}")

                    # Brief pause to prevent accidental double-capture
                    time.sleep(0.3)

                elif key == ord('q'):
                    print("\nStopped by user")
                    break

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            self.close_camera()

        print(f"\n✅ Collected {images_collected} images for {person_name}")
        print(f"   Saved to: {person_dir}")

    def train_recognizer(self):
        """
        Train face recognition model from collected images.

        Creates face encodings for all people in the training directory.
        """
        print("\n" + "="*60)
        print("TRAINING FACE RECOGNITION MODEL")
        print("="*60)

        # Find all person directories
        person_dirs = [d for d in self.training_dir.iterdir() if d.is_dir()]

        if not person_dirs:
            print("❌ No training data found")
            print(f"   Run: python test_vision.py --mode train --name \"YourName\"")
            return

        print(f"Found {len(person_dirs)} people:")
        for person_dir in person_dirs:
            image_count = len(list(person_dir.glob("*.jpg")))
            print(f"  - {person_dir.name}: {image_count} images")

        print("\nGenerating face encodings with jittering (this may take a minute)...")

        encodings = []
        names = []

        for person_dir in person_dirs:
            person_name = person_dir.name
            image_files = list(person_dir.glob("*.jpg"))

            print(f"\nProcessing {person_name} ({len(image_files)} images)...")

            person_encodings = []

            for i, image_file in enumerate(image_files, 1):
                # Load image
                image = face_recognition.load_image_file(str(image_file))

                # Find face encodings with jittering for better accuracy
                # num_jitters=10 is a good balance (100 is best but slower)
                face_encodings = face_recognition.face_encodings(image, num_jitters=10)

                if len(face_encodings) == 0:
                    print(f"  ⚠ No face found in {image_file.name}")
                    continue

                if len(face_encodings) > 1:
                    print(f"  ⚠ Multiple faces in {image_file.name}, using first")

                # Collect encoding for averaging
                person_encodings.append(face_encodings[0])

                print(f"  ✅ Processed {i}/{len(image_files)}", end='\r')

            print()  # New line after progress

            if len(person_encodings) > 0:
                # Average all encodings for this person into a single encoding
                averaged_encoding = np.mean(person_encodings, axis=0)
                encodings.append(averaged_encoding)
                names.append(person_name)
                print(f"  📊 Averaged {len(person_encodings)} encodings into 1 master encoding")
            else:
                print(f"  ❌ No valid encodings found for {person_name}")

        # Save encodings
        print(f"\nSaving {len(encodings)} encodings to {self.encodings_file}...")

        with open(self.encodings_file, 'wb') as f:
            pickle.dump({
                'encodings': encodings,
                'names': names
            }, f)

        print("✅ Training complete!")
        print(f"   Total encodings: {len(encodings)}")
        print(f"   People: {len(set(names))}")

    def validate_model(self, test_split: float = 0.2):
        """
        Validate the trained model using a test split of the training data.

        Args:
            test_split: Fraction of images to use for validation (default 0.2 = 20%)
        """
        print("\n" + "="*60)
        print("MODEL VALIDATION")
        print("="*60)

        # Find all person directories
        person_dirs = [d for d in self.training_dir.iterdir() if d.is_dir()]

        if not person_dirs:
            print("❌ No training data found")
            return

        # Load the trained model
        if not self.load_encodings():
            return

        print(f"\nTesting with {int(test_split * 100)}% validation split...")
        print("=" * 60)

        total_tests = 0
        correct_predictions = 0
        confidence_scores = []

        for person_dir in person_dirs:
            person_name = person_dir.name
            image_files = list(person_dir.glob("*.jpg"))

            if len(image_files) < 5:
                print(f"⚠ Skipping {person_name}: Need at least 5 images for validation")
                continue

            # Use last 20% of images for testing
            num_test = max(1, int(len(image_files) * test_split))
            test_files = image_files[-num_test:]

            print(f"\nTesting {person_name} ({num_test} validation images)...")

            for test_file in test_files:
                # Load test image
                test_image = face_recognition.load_image_file(str(test_file))
                test_encodings = face_recognition.face_encodings(test_image, num_jitters=5)

                if len(test_encodings) == 0:
                    print(f"  ⚠ No face in {test_file.name}")
                    continue

                total_tests += 1
                test_encoding = test_encodings[0]

                # Compare with all known faces
                face_distances = face_recognition.face_distance(
                    self.known_face_encodings,
                    test_encoding
                )

                best_match_index = np.argmin(face_distances)
                predicted_name = self.known_face_names[best_match_index]
                confidence = 1.0 - face_distances[best_match_index]
                is_match = face_distances[best_match_index] < 0.6

                if predicted_name == person_name and is_match:
                    correct_predictions += 1
                    status = "✅"
                else:
                    status = "❌"

                confidence_scores.append(confidence)
                print(f"  {status} {test_file.name}: Predicted '{predicted_name}' (confidence: {confidence:.2f}, distance: {face_distances[best_match_index]:.2f})")

        # Print summary
        print("\n" + "="*60)
        print("VALIDATION RESULTS")
        print("="*60)

        if total_tests > 0:
            accuracy = (correct_predictions / total_tests) * 100
            avg_confidence = np.mean(confidence_scores)
            print(f"Accuracy: {accuracy:.1f}% ({correct_predictions}/{total_tests} correct)")
            print(f"Average Confidence: {avg_confidence:.2f}")
            print(f"Min Confidence: {min(confidence_scores):.2f}")
            print(f"Max Confidence: {max(confidence_scores):.2f}")

            if accuracy >= 95:
                print("\n🎉 Excellent! Model is working very well.")
            elif accuracy >= 80:
                print("\n👍 Good! Model is working well.")
            elif accuracy >= 60:
                print("\n⚠️  Fair. Consider collecting more diverse training images.")
            else:
                print("\n❌ Poor accuracy. Retrain with better quality images.")
        else:
            print("❌ No valid test images found")

    def load_encodings(self) -> bool:
        """
        Load face encodings from file.

        Returns:
            True if successful, False otherwise
        """
        if not self.encodings_file.exists():
            print("❌ No trained model found")
            print("   Run: python test_vision.py --mode train --name \"YourName\"")
            print("   Then: python test_vision.py --train")
            return False

        print(f"Loading encodings from {self.encodings_file}...")

        with open(self.encodings_file, 'rb') as f:
            data = pickle.load(f)

        self.known_face_encodings = data['encodings']
        self.known_face_names = data['names']

        unique_names = set(self.known_face_names)
        print(f"✅ Loaded {len(self.known_face_encodings)} encodings")
        print(f"   People: {', '.join(unique_names)}")

        return True

    def test_recognition(self):
        """
        Test face recognition with trained model.

        Recognizes faces in real-time from webcam.
        """
        print("\n" + "="*60)
        print("FACE RECOGNITION TEST")
        print("="*60)
        print("Press 'q' to quit")
        print()

        # Load encodings
        if not self.load_encodings():
            return

        if not self.open_camera():
            return

        print("\nStarting recognition loop...")

        # Process every Nth frame for performance
        process_every_n_frames = 3
        frame_number = 0

        # Cache results for frames we don't process
        last_face_locations = []
        last_face_names = []

        try:
            while True:
                ret, frame = self.camera.read()

                if not ret:
                    print("❌ Failed to read frame")
                    break

                frame_number += 1

                # Only process every Nth frame
                if frame_number % process_every_n_frames == 0:
                    # Resize for faster processing
                    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                    rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

                    # Find faces
                    start = time.time()
                    face_locations = face_recognition.face_locations(rgb_small_frame)
                    face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
                    processing_time = (time.time() - start) * 1000

                    # Match faces
                    face_names = []
                    for face_encoding in face_encodings:
                        matches = face_recognition.compare_faces(
                            self.known_face_encodings,
                            face_encoding,
                            tolerance=0.5
                        )
                        name = "Unknown"
                        confidence = 0.0

                        if True in matches:
                            # Find best match
                            face_distances = face_recognition.face_distance(
                                self.known_face_encodings,
                                face_encoding
                            )
                            best_match_index = np.argmin(face_distances)

                            if matches[best_match_index]:
                                name = self.known_face_names[best_match_index]
                                confidence = 1.0 - face_distances[best_match_index]

                        face_names.append((name, confidence))

                    # Scale back up locations
                    last_face_locations = [(top * 4, right * 4, bottom * 4, left * 4)
                                          for (top, right, bottom, left) in face_locations]
                    last_face_names = face_names

                # Draw results (using cached data for skipped frames)
                for (top, right, bottom, left), (name, confidence) in zip(last_face_locations, last_face_names):
                    # Draw box
                    color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
                    cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

                    # Draw label
                    label = f"{name} ({confidence:.2f})" if name != "Unknown" else "Unknown"
                    cv2.rectangle(frame, (left, bottom - 35), (right, bottom), color, cv2.FILLED)
                    cv2.putText(frame, label, (left + 6, bottom - 6),
                               cv2.FONT_HERSHEY_DUPLEX, 0.6, (255, 255, 255), 1)

                # Update FPS
                self.update_fps()

                # Display info
                info_text = [
                    f"FPS: {self.fps:.1f}",
                    f"Faces: {len(last_face_locations)}",
                    f"Processing: {processing_time:.0f}ms" if frame_number % process_every_n_frames == 0 else ""
                ]

                y_offset = 30
                for text in info_text:
                    if text:
                        cv2.putText(frame, text, (10, y_offset),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                        y_offset += 30

                # Show frame
                cv2.imshow('Face Recognition Test', frame)

                # Check for quit
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            self.close_camera()

    def test_object_detection(self):
        """
        Test YOLO object detection to see what objects are recognized.

        Uses YOLO11n (nano) model for fast real-time detection with latest optimizations.
        """
        print("\n" + "="*60)
        print("YOLO OBJECT DETECTION TEST")
        print("="*60)
        print("Testing YOLO11n (latest) for real-time object recognition")
        print("Press 'q' to quit")
        print()

        if not self.open_camera():
            return

        # Load YOLO11 model (will download yolo11n.pt on first run)
        print("Loading YOLO11n model...")
        try:
            model = YOLO('yolo11n.pt')  # YOLO11 Nano - latest, fastest
            model.to('mps')  # Use M1 GPU (Metal Performance Shaders) for 10-20x speedup
            print("✅ YOLO11 model loaded")
            print("   22% fewer parameters than YOLOv8, higher accuracy")
            print("   Running on M1 GPU (MPS) for maximum performance")
        except Exception as e:
            print(f"❌ Failed to load YOLO model: {e}")
            self.close_camera()
            return

        print("\nStarting object detection loop...")
        print("YOLO11 can detect 80 object classes including:")
        print("  - People, animals, vehicles")
        print("  - Food, drinks, utensils")
        print("  - Electronics, furniture, sports equipment")
        print("\nOptimizations enabled:")
        print("  - Half-precision (FP16) for M1 Mac")
        print("  - Streaming mode for memory efficiency")
        print("  - Confidence threshold: 0.5 (adjustable)")
        print("  - IOU threshold: 0.7 for NMS")
        print()

        # Process every Nth frame for performance
        process_every_n_frames = 2
        frame_number = 0

        # Cache results for frames we don't process
        last_results = None

        try:
            while True:
                ret, frame = self.camera.read()

                if not ret:
                    print("❌ Failed to read frame")
                    break

                frame_number += 1

                # Only process every Nth frame
                if frame_number % process_every_n_frames == 0:
                    start = time.time()
                    # Run YOLO11 inference with optimizations
                    results = model(
                        frame,
                        conf=0.5,      # Confidence threshold (default 0.25, higher = fewer false positives)
                        iou=0.7,       # IOU threshold for NMS (default 0.7)
                        imgsz=640,     # Input image size (explicit)
                        half=True,     # FP16 precision for M1 Mac speed boost
                        verbose=False, # Suppress per-frame output
                        stream=True,   # Memory-efficient streaming mode
                        device='mps'   # Explicit M1 GPU device
                    )
                    # Get first result from generator
                    last_results = next(iter(results))
                    processing_time = (time.time() - start) * 1000
                else:
                    processing_time = 0

                # Update FPS
                self.update_fps()

                # Draw results on frame
                if last_results is not None:
                    # Get detected boxes
                    boxes = last_results.boxes

                    detected_objects = []

                    for box in boxes:
                        # Get box coordinates
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        # Get confidence and class
                        confidence = float(box.conf[0])
                        class_id = int(box.cls[0])
                        class_name = model.names[class_id]

                        # Filter already handled by conf parameter, but double-check
                        if confidence > 0.5:
                            detected_objects.append((class_name, confidence))

                            # Draw bounding box
                            color = (0, 255, 0)
                            cv2.rectangle(frame,
                                        (int(x1), int(y1)),
                                        (int(x2), int(y2)),
                                        color, 2)

                            # Draw label
                            label = f"{class_name} {confidence:.2f}"
                            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]

                            # Background for text
                            cv2.rectangle(frame,
                                        (int(x1), int(y1) - label_size[1] - 10),
                                        (int(x1) + label_size[0], int(y1)),
                                        color, -1)

                            # Text
                            cv2.putText(frame, label,
                                      (int(x1), int(y1) - 5),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                      (0, 0, 0), 2)

                    # Display info overlay
                    info_y = 30
                    cv2.putText(frame, f"FPS: {self.fps:.1f}",
                              (10, info_y),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                    info_y += 30
                    cv2.putText(frame, f"Objects: {len(detected_objects)}",
                              (10, info_y),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                    if processing_time > 0:
                        info_y += 30
                        cv2.putText(frame, f"Processing: {processing_time:.0f}ms",
                                  (10, info_y),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                    # Show detected objects list at bottom
                    if detected_objects:
                        objects_text = ", ".join([f"{obj} ({conf:.2f})" for obj, conf in detected_objects[:5]])
                        cv2.putText(frame, f"Detected: {objects_text}",
                                  (10, frame.shape[0] - 20),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)

                # Show frame
                cv2.imshow('YOLO Object Detection Test', frame)

                # Check for quit
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            self.close_camera()

        print("\n" + "="*60)
        print("Object detection test complete!")
        print("YOLO11n can detect 80 classes from the COCO dataset")
        print("Model: YOLO11 (Sept 2024) - Latest from Ultralytics")
        print("="*60)

    def test_hand_gestures(self):
        """
        Test MediaPipe GestureRecognizer with pre-trained gestures.

        Uses MediaPipe's built-in gesture recognition model.
        """
        print("\n" + "="*60)
        print("HAND GESTURE RECOGNITION TEST")
        print("="*60)
        print("Testing MediaPipe GestureRecognizer (pre-trained)")
        print("Press 'q' to quit")
        print()

        if not self.open_camera():
            return

        # Initialize MediaPipe GestureRecognizer
        print("Loading MediaPipe GestureRecognizer model...")

        try:
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            from mediapipe import solutions
            from mediapipe.framework.formats import landmark_pb2

            # Download model if needed
            import urllib.request
            import os

            model_path = 'gesture_recognizer.task'
            if not os.path.exists(model_path):
                print("Downloading gesture recognition model...")
                model_url = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task"
                urllib.request.urlretrieve(model_url, model_path)
                print("✅ Model downloaded")

            # Create GestureRecognizer
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.GestureRecognizerOptions(
                base_options=base_options,
                running_mode=vision.RunningMode.VIDEO,
                num_hands=2,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5
            )
            recognizer = vision.GestureRecognizer.create_from_options(options)

            print("✅ MediaPipe GestureRecognizer loaded")
            print("   Pre-trained gestures: Thumb Up, Thumb Down, Victory,")
            print("   Pointing Up, Open Palm, Closed Fist, ILoveYou")

        except Exception as e:
            print(f"❌ Failed to load GestureRecognizer: {e}")
            self.close_camera()
            return

        print("\nStarting gesture recognition...")
        print()

        frame_count = 0

        try:
            while True:
                ret, frame = self.camera.read()

                if not ret:
                    print("❌ Failed to read frame")
                    break

                frame_count += 1

                # Convert to MediaPipe Image format
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

                # Process with GestureRecognizer (timestamp in milliseconds)
                timestamp_ms = int(frame_count * (1000 / 30))  # Assuming 30 FPS
                results = recognizer.recognize_for_video(mp_image, timestamp_ms)

                # Update FPS
                self.update_fps()

                # Draw results
                if results.gestures:
                    # Draw hand landmarks and gestures
                    for hand_idx in range(len(results.gestures)):
                        # Get gesture
                        gesture = results.gestures[hand_idx][0]  # Top gesture
                        handedness = results.handedness[hand_idx][0]  # Left/Right

                        # Draw landmarks
                        if results.hand_landmarks:
                            hand_landmarks = results.hand_landmarks[hand_idx]

                            # Convert to format for drawing
                            hand_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
                            hand_landmarks_proto.landmark.extend([
                                landmark_pb2.NormalizedLandmark(x=landmark.x, y=landmark.y, z=landmark.z)
                                for landmark in hand_landmarks
                            ])

                            solutions.drawing_utils.draw_landmarks(
                                frame,
                                hand_landmarks_proto,
                                solutions.hands.HAND_CONNECTIONS,
                                solutions.drawing_styles.get_default_hand_landmarks_style(),
                                solutions.drawing_styles.get_default_hand_connections_style()
                            )

                        # Display gesture info
                        y_offset = 30 + (hand_idx * 80)
                        gesture_text = f"{handedness.category_name}: {gesture.category_name}"
                        confidence_text = f"Confidence: {gesture.score:.2f}"

                        cv2.putText(frame, gesture_text,
                                  (10, y_offset),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        cv2.putText(frame, confidence_text,
                                  (10, y_offset + 30),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                else:
                    cv2.putText(frame, "No hands detected",
                              (10, 30),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                # Display FPS
                cv2.putText(frame, f"FPS: {self.fps:.1f}",
                          (frame.shape[1] - 150, 30),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                # Show frame
                cv2.imshow('Hand Gesture Recognition', frame)

                # Check for quit
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            recognizer.close()
            self.close_camera()

        print("\n" + "="*60)
        print("Hand gesture recognition test complete!")
        print("Used MediaPipe's pre-trained GestureRecognizer model")
        print("="*60)

    def _recognize_gesture(self, hand_landmarks, handedness_label="Right") -> str:
        """
        Recognize hand gesture from landmarks.

        Args:
            hand_landmarks: MediaPipe hand landmarks
            handedness_label: "Left" or "Right" hand

        Returns:
            Gesture name as string
        """
        # Get landmark positions
        landmarks = hand_landmarks.landmark

        # Finger tip indices: thumb, index, middle, ring, pinky
        tip_ids = [4, 8, 12, 16, 20]
        # Finger PIP (middle joint) indices - best for detecting finger extension
        pip_ids = [3, 6, 10, 14, 18]  # IP for thumb, PIP for others

        # Check which fingers are extended
        fingers_up = []

        # Thumb detection: Must be both EXTENDED and pointing UP
        # This prevents false positives when hand is sideways or thumb is down
        thumb_tip = landmarks[tip_ids[0]]
        thumb_ip = landmarks[pip_ids[0]]  # IP joint for thumb
        thumb_mcp = landmarks[2]  # MCP joint for thumb
        wrist = landmarks[0]

        # Check 1: Is thumb extended? (tip is further from wrist than IP joint)
        thumb_tip_dist_x = abs(thumb_tip.x - wrist.x)
        thumb_ip_dist_x = abs(thumb_ip.x - wrist.x)
        thumb_extended = thumb_tip_dist_x > thumb_ip_dist_x

        # Check 2: Is thumb pointing UP? (tip is above MCP joint vertically)
        thumb_pointing_up = thumb_tip.y < thumb_mcp.y

        # Thumb is "up" only if BOTH conditions are true
        fingers_up.append(1 if (thumb_extended and thumb_pointing_up) else 0)

        # Other fingers (check if tip is above PIP joint)
        # Finger is up if tip y-coordinate is LESS than PIP (closer to top of image)
        for i in range(1, 5):
            tip = landmarks[tip_ids[i]]
            pip = landmarks[pip_ids[i]]
            fingers_up.append(1 if tip.y < pip.y else 0)

        # Recognize gestures based on finger configuration
        total_fingers = sum(fingers_up)

        # Debug mode: show finger detection states
        return f"[{fingers_up}] = {total_fingers}"

        if total_fingers == 0:
            return "✊ Closed Fist"
        elif total_fingers == 5:
            return "✋ Open Palm"
        elif fingers_up == [1, 0, 0, 0, 0]:
            return "👍 Thumbs Up"
        elif fingers_up == [0, 1, 1, 0, 0]:
            return "✌️ Peace Sign"
        elif fingers_up == [0, 1, 0, 0, 0]:
            return "👉 Pointing"
        elif fingers_up == [0, 0, 0, 0, 1]:
            return "🤙 Pinky (Shaka)"
        elif fingers_up == [1, 1, 0, 0, 1]:
            return "🤘 Rock Sign"
        elif fingers_up == [1, 1, 0, 0, 0]:
            return "🤙 Hang Loose"
        else:
            return f"Custom ({total_fingers} fingers)"

    def test_combined_vision(self):
        """
        Test combined MediaPipe vision pipeline: gestures + holistic + face ID + objects.

        Uses all MediaPipe models for unified processing:
        - GestureRecognizer: Hand gesture recognition
        - Holistic: Face mesh + pose landmarks
        - Object Detector: 80 COCO class objects
        - face_recognition: Person identification on detected faces
        """
        print("\n" + "="*60)
        print("COMBINED MEDIAPIPE VISION PIPELINE TEST")
        print("="*60)
        print("Running: Gestures + Holistic + Face ID + Objects")
        print("Press 'q' to quit, '1/2/3/4' to toggle components, 'h' for help")
        print()

        if not self.open_camera():
            return

        # Load face encodings for person identification
        print("Loading face recognition data...")
        if self.encodings_file.exists():
            with open(self.encodings_file, 'rb') as f:
                data = pickle.load(f)
                self.known_face_encodings = data['encodings']
                self.known_face_names = data['names']
            print(f"✅ Loaded {len(self.known_face_names)} known faces")
        else:
            print("⚠️  No face encodings found - person ID disabled")
            print("   Run training first: python test_vision.py --mode train --name 'YourName'")
            self.known_face_encodings = []
            self.known_face_names = []

        # Initialize MediaPipe components
        print("\nInitializing MediaPipe models...")

        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        from mediapipe.framework.formats import landmark_pb2
        import urllib.request

        # 1. GestureRecognizer
        print("Loading GestureRecognizer...")
        try:
            gesture_model_path = 'gesture_recognizer.task'
            if not os.path.exists(gesture_model_path):
                print("  Downloading gesture model...")
                urllib.request.urlretrieve(
                    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
                    gesture_model_path
                )
            gesture_options = vision.GestureRecognizerOptions(
                base_options=python.BaseOptions(
                    model_asset_path=gesture_model_path
                    # Note: Disabled GPU due to buffer format issues on M1
                    # Using CPU (default) for stability
                ),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=2,  # Back to tracking both hands
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5
            )
            gesture_recognizer = vision.GestureRecognizer.create_from_options(gesture_options)
            print("✅ GestureRecognizer loaded")
        except Exception as e:
            print(f"❌ Failed to load GestureRecognizer: {e}")
            self.close_camera()
            return

        # 2. Pose Landmarker (body pose only - much faster than Holistic)
        print("Loading Pose Landmarker...")
        try:
            mp_pose = mp.solutions.pose
            pose = mp_pose.Pose(
                static_image_mode=False,
                model_complexity=0,  # 0=lite, 1=full, 2=heavy (using LITE for speed)
                enable_segmentation=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            print("✅ Pose Landmarker loaded (LITE model - 33 landmarks)")
        except Exception as e:
            print(f"❌ Failed to load Pose Landmarker: {e}")
            gesture_recognizer.close()
            self.close_camera()
            return

        # 3. Object Detector
        print("Loading Object Detector...")
        try:
            object_model_path = 'efficientdet_lite0.tflite'
            if not os.path.exists(object_model_path):
                print("  Downloading object detection model...")
                urllib.request.urlretrieve(
                    "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/latest/efficientdet_lite0.tflite",
                    object_model_path
                )
            object_options = vision.ObjectDetectorOptions(
                base_options=python.BaseOptions(
                    model_asset_path=object_model_path
                    # Note: int8 quantized model doesn't work with GPU delegate on M1
                    # Using CPU (default) for Object Detector
                ),
                running_mode=vision.RunningMode.VIDEO,
                max_results=10,
                score_threshold=0.5
            )
            object_detector = vision.ObjectDetector.create_from_options(object_options)
            print("✅ Object Detector loaded (EfficientDet-Lite0)")
        except Exception as e:
            print(f"❌ Failed to load Object Detector: {e}")
            gesture_recognizer.close()
            pose.close()
            self.close_camera()
            return

        # 4. Face Detector (for fast face detection, then face_recognition for ID)
        print("Loading Face Detector...")
        try:
            face_model_path = 'face_detector.tflite'
            if not os.path.exists(face_model_path):
                print("  Downloading face detection model...")
                urllib.request.urlretrieve(
                    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
                    face_model_path
                )
            face_options = vision.FaceDetectorOptions(
                base_options=python.BaseOptions(
                    model_asset_path=face_model_path
                    # Note: Disabled GPU due to buffer format issues on M1
                    # Using CPU (default) for stability
                ),
                running_mode=vision.RunningMode.VIDEO,
                min_detection_confidence=0.5
            )
            face_detector = vision.FaceDetector.create_from_options(face_options)
            print("✅ Face Detector loaded (BlazeFace)")
        except Exception as e:
            print(f"❌ Failed to load Face Detector: {e}")
            gesture_recognizer.close()
            pose.close()
            object_detector.close()
            self.close_camera()
            return

        mp_drawing = mp.solutions.drawing_utils
        mp_drawing_styles = mp.solutions.drawing_styles

        print("\n" + "="*60)
        print("PIPELINE ACTIVE - All MediaPipe Models Running")
        print("="*60)
        print("Frame scheduling:")
        print("  - Gestures: Every 5 frames (7 gestures, ~6 Hz)")
        print("  - Pose: Every 2 frames (33 body landmarks)")
        print("  - Objects: Every 2 frames (80 COCO classes)")
        print("  - Face Detection: Every 5 frames (MediaPipe BlazeFace)")
        print("  - Face ID: Every 5 frames (face_recognition encoding match)")
        print()
        print("Controls:")
        print("  '1': Toggle gestures | '2': Toggle pose")
        print("  '3': Toggle objects  | '4': Toggle face ID")
        print("  'h': Toggle help     | 'q': Quit")
        print()

        # Frame scheduling
        frame_number = 0

        # Cached results
        last_object_detections = []
        last_pose_results = None
        last_gesture_results = None  # Cache gestures to prevent flickering
        last_face_detections = []  # MediaPipe face bounding boxes
        last_face_ids = {}  # Maps face bbox to (name, confidence)

        # Performance tracking
        model_times = {'gestures': [], 'pose': [], 'objects': [], 'face_detect': [], 'face_id': []}

        # Display toggles
        show_gestures = True
        show_pose = True
        show_objects = True
        show_face_id = True
        show_help = False

        try:
            while True:
                ret, frame = self.camera.read()
                if not ret:
                    print("❌ Failed to read frame")
                    break

                frame_number += 1
                display_frame = frame.copy()

                # Convert to RGB and MediaPipe Image format
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                timestamp_ms = int(frame_number * (1000 / 30))

                # ===== 1. HAND GESTURES (Every 5 frames - gestures don't change that fast) =====
                if frame_number % 5 == 0 and show_gestures:
                    start_time = time.time()
                    last_gesture_results = gesture_recognizer.recognize_for_video(mp_image, timestamp_ms)
                    model_times['gestures'].append((time.time() - start_time) * 1000)

                # ===== 2. POSE (Every 2 frames for speed) =====
                if frame_number % 2 == 0 and show_pose:
                    start_time = time.time()
                    last_pose_results = pose.process(rgb_frame)
                    model_times['pose'].append((time.time() - start_time) * 1000)

                # ===== 3. OBJECT DETECTION (Every 2 frames, same as gestures) =====
                if frame_number % 2 == 0 and show_objects:
                    start_time = time.time()
                    object_results = object_detector.detect_for_video(mp_image, timestamp_ms)
                    last_object_detections = object_results.detections if object_results else []
                    model_times['objects'].append((time.time() - start_time) * 1000)

                # ===== 4. FACE DETECTION (Every 5 frames - MediaPipe BlazeFace) =====
                if frame_number % 5 == 0 and show_face_id:
                    detect_start = time.time()
                    face_results = face_detector.detect_for_video(mp_image, timestamp_ms)
                    last_face_detections = face_results.detections if face_results else []
                    model_times['face_detect'].append((time.time() - detect_start) * 1000)

                    # ===== 5. FACE IDENTIFICATION (Only if faces detected and we have encodings) =====
                    if last_face_detections and len(self.known_face_encodings) > 0:
                        id_start = time.time()

                        # Use first detected face only (for now)
                        face_detection = last_face_detections[0]
                        bbox = face_detection.bounding_box

                        # Convert MediaPipe bbox to pixel coordinates
                        left = int(bbox.origin_x)
                        top = int(bbox.origin_y)
                        right = int(bbox.origin_x + bbox.width)
                        bottom = int(bbox.origin_y + bbox.height)

                        # Crop face region from RGB frame (minimal padding for speed)
                        height, width = rgb_frame.shape[:2]
                        padding = 10  # Reduced padding
                        top_pad = max(0, top - padding)
                        left_pad = max(0, left - padding)
                        bottom_pad = min(height, bottom + padding)
                        right_pad = min(width, right + padding)

                        face_crop = rgb_frame[top_pad:bottom_pad, left_pad:right_pad]

                        if face_crop.size > 0 and face_crop.shape[0] > 0 and face_crop.shape[1] > 0:
                            # Resize face to standard size for faster encoding
                            face_crop_resized = cv2.resize(face_crop, (150, 150))

                            try:
                                # Tell face_recognition the face is the entire cropped image
                                # This avoids dlib running face detection again
                                known_face_location = [(0, 150, 150, 0)]  # Full image is the face

                                # Use face_recognition to encode with optimizations:
                                # - small model (5-point vs 68-point landmarks)
                                # - num_jitters=0 (no resampling, faster but slightly less accurate)
                                # - known location (skip detection)
                                face_encodings = face_recognition.face_encodings(
                                    face_crop_resized,
                                    known_face_locations=known_face_location,
                                    num_jitters=0,  # No jittering for speed
                                    model='small'   # 5-point model instead of 68-point
                                )

                                if face_encodings:
                                    encoding = face_encodings[0]
                                    matches = face_recognition.compare_faces(
                                        self.known_face_encodings, encoding, tolerance=0.6
                                    )
                                    name = "Unknown"
                                    confidence = 0.0

                                    if True in matches:
                                        distances = face_recognition.face_distance(
                                            self.known_face_encodings, encoding
                                        )
                                        best_idx = np.argmin(distances)
                                        if matches[best_idx]:
                                            name = self.known_face_names[best_idx]
                                            confidence = 1 - distances[best_idx]

                                    last_face_ids = {
                                        'bbox': (left, top, right, bottom),
                                        'name': name,
                                        'confidence': confidence
                                    }
                                else:
                                    last_face_ids = {}
                            except Exception as e:
                                # If encoding fails, skip this face
                                last_face_ids = {}
                        else:
                            last_face_ids = {}

                        model_times['face_id'].append((time.time() - id_start) * 1000)
                    else:
                        last_face_ids = {}

                # ===== DRAW ALL RESULTS =====

                # Draw object detections (green boxes)
                if show_objects and last_object_detections:
                    for detection in last_object_detections:
                        bbox = detection.bounding_box
                        category = detection.categories[0]

                        x1 = int(bbox.origin_x)
                        y1 = int(bbox.origin_y)
                        x2 = int(bbox.origin_x + bbox.width)
                        y2 = int(bbox.origin_y + bbox.height)

                        cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f"{category.category_name} {category.score:.2f}"
                        cv2.putText(display_frame, label, (x1, y1 - 5),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                # Draw pose landmarks
                if show_pose and last_pose_results:
                    if last_pose_results.pose_landmarks:
                        mp_drawing.draw_landmarks(
                            display_frame,
                            last_pose_results.pose_landmarks,
                            mp_pose.POSE_CONNECTIONS,
                            landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style()
                        )

                # Draw face identification (blue box on top of holistic face)
                if show_face_id and last_face_ids:
                    left, top, right, bottom = last_face_ids['bbox']
                    name = last_face_ids['name']
                    conf = last_face_ids['confidence']

                    cv2.rectangle(display_frame, (left, top), (right, bottom), (255, 0, 0), 2)
                    cv2.rectangle(display_frame, (left, bottom - 35), (right, bottom), (255, 0, 0), -1)
                    label = f"{name} ({conf:.2f})" if conf > 0 else name
                    cv2.putText(display_frame, label, (left + 6, bottom - 6),
                              cv2.FONT_HERSHEY_DUPLEX, 0.6, (255, 255, 255), 1)

                # Draw hand gestures (yellow skeleton + text) - use cached results to prevent flicker
                gesture_y_offset = 30
                if show_gestures and last_gesture_results and last_gesture_results.gestures:
                    for hand_idx in range(len(last_gesture_results.gestures)):
                        gesture = last_gesture_results.gestures[hand_idx][0]
                        handedness = last_gesture_results.handedness[hand_idx][0]

                        # Draw hand landmarks
                        if last_gesture_results.hand_landmarks:
                            hand_landmarks = last_gesture_results.hand_landmarks[hand_idx]

                            # Convert to proto format for drawing
                            hand_landmarks_proto = landmark_pb2.NormalizedLandmarkList()
                            hand_landmarks_proto.landmark.extend([
                                landmark_pb2.NormalizedLandmark(x=lm.x, y=lm.y, z=lm.z)
                                for lm in hand_landmarks
                            ])

                            mp_drawing.draw_landmarks(
                                display_frame,
                                hand_landmarks_proto,
                                mp.solutions.hands.HAND_CONNECTIONS,
                                mp_drawing_styles.get_default_hand_landmarks_style(),
                                mp_drawing_styles.get_default_hand_connections_style()
                            )

                        # Display gesture text
                        gesture_text = f"{handedness.category_name}: {gesture.category_name}"
                        cv2.putText(display_frame, gesture_text, (10, gesture_y_offset),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                        gesture_y_offset += 30

                # Update FPS
                self.update_fps()

                # Draw performance overlay (top-right)
                info_x = display_frame.shape[1] - 280
                cv2.putText(display_frame, f"FPS: {self.fps:.1f}",
                          (info_x, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                y_offset = 55
                for label, times, color in [
                    ('Gestures', model_times['gestures'], (0, 255, 255)),
                    ('Pose', model_times['pose'], (255, 0, 255)),
                    ('Objects', model_times['objects'], (0, 255, 0)),
                    ('Face Detect', model_times['face_detect'], (100, 100, 255)),
                    ('Face ID', model_times['face_id'], (255, 0, 0))
                ]:
                    if times:
                        avg_time = np.mean(times[-30:])
                        cv2.putText(display_frame, f"{label}: {avg_time:.1f}ms",
                                  (info_x, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                        y_offset += 25

                # Draw help overlay (bottom-left)
                if show_help:
                    legend_y = display_frame.shape[0] - 135
                    cv2.putText(display_frame, "Legend:",
                              (10, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    cv2.putText(display_frame, "Green: Objects (MediaPipe)",
                              (10, legend_y + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                    cv2.putText(display_frame, "Magenta: Body pose (MediaPipe)",
                              (10, legend_y + 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
                    cv2.putText(display_frame, "Blue: Person ID (face_recognition)",
                              (10, legend_y + 75), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
                    cv2.putText(display_frame, "Yellow: Hand gestures (MediaPipe)",
                              (10, legend_y + 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                # Show frame
                cv2.imshow('Combined MediaPipe Vision Pipeline', display_frame)

                # Handle keypresses
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('h'):
                    show_help = not show_help
                elif key == ord('1'):
                    show_gestures = not show_gestures
                    print(f"Gestures: {'ON' if show_gestures else 'OFF'}")
                elif key == ord('2'):
                    show_pose = not show_pose
                    print(f"Pose: {'ON' if show_pose else 'OFF'}")
                elif key == ord('3'):
                    show_objects = not show_objects
                    print(f"Objects: {'ON' if show_objects else 'OFF'}")
                elif key == ord('4'):
                    show_face_id = not show_face_id
                    print(f"Face ID: {'ON' if show_face_id else 'OFF'}")

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            gesture_recognizer.close()
            pose.close()
            object_detector.close()
            face_detector.close()
            self.close_camera()

        print("\n" + "="*60)
        print("Combined MediaPipe vision pipeline test complete!")
        print("\nPerformance Summary:")
        for label, times in model_times.items():
            if times:
                print(f"  {label.capitalize()}: {np.mean(times):.1f}ms avg")
        print(f"  Overall FPS: {self.fps:.1f}")
        print("="*60)

    def test_holistic(self):
        """
        Test MediaPipe Holistic - tracks face + hands + body pose simultaneously.

        Tracks 543 total landmarks: 33 pose, 468 face, 21 per hand.
        """
        print("\n" + "="*60)
        print("MEDIAPIPE HOLISTIC TEST")
        print("="*60)
        print("Testing MediaPipe Holistic (Face + Hands + Pose)")
        print("Press 'q' to quit, 'h' to toggle display options")
        print()

        if not self.open_camera():
            return

        # Initialize MediaPipe Holistic
        print("Loading MediaPipe Holistic model...")
        mp_holistic = mp.solutions.holistic
        mp_drawing = mp.solutions.drawing_utils
        mp_drawing_styles = mp.solutions.drawing_styles

        try:
            holistic = mp_holistic.Holistic(
                static_image_mode=False,
                model_complexity=1,  # 0=lite, 1=full, 2=heavy
                smooth_landmarks=True,
                enable_segmentation=False,  # Set to True for background removal
                smooth_segmentation=True,
                refine_face_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            print("✅ MediaPipe Holistic loaded")
            print("   Tracking 543 landmarks:")
            print("   - 33 pose landmarks (body skeleton)")
            print("   - 468 face landmarks (detailed facial features)")
            print("   - 21 landmarks per hand × 2 hands")
            print("   Model complexity: 1 (full)")
        except Exception as e:
            print(f"❌ Failed to load MediaPipe Holistic: {e}")
            self.close_camera()
            return

        print("\nStarting holistic tracking...")
        print()

        # Display options
        show_face = True
        show_pose = True
        show_hands = True
        show_help = False

        try:
            while True:
                ret, frame = self.camera.read()

                if not ret:
                    print("❌ Failed to read frame")
                    break

                # Convert BGR to RGB for MediaPipe
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Process with Holistic
                start_time = time.time()
                results = holistic.process(rgb_frame)
                processing_time = (time.time() - start_time) * 1000

                # Update FPS
                self.update_fps()

                # Draw results based on options
                drawing_spec = mp_drawing.DrawingSpec(thickness=1, circle_radius=1)

                # Draw pose landmarks (body skeleton)
                if show_pose and results.pose_landmarks:
                    mp_drawing.draw_landmarks(
                        frame,
                        results.pose_landmarks,
                        mp_holistic.POSE_CONNECTIONS,
                        landmark_drawing_spec=drawing_spec,
                        connection_drawing_spec=mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2)
                    )

                # Draw face landmarks
                if show_face and results.face_landmarks:
                    mp_drawing.draw_landmarks(
                        frame,
                        results.face_landmarks,
                        mp_holistic.FACEMESH_CONTOURS,
                        landmark_drawing_spec=drawing_spec,
                        connection_drawing_spec=mp_drawing.DrawingSpec(color=(255, 0, 0), thickness=1)
                    )

                # Draw hand landmarks
                if show_hands:
                    if results.left_hand_landmarks:
                        mp_drawing.draw_landmarks(
                            frame,
                            results.left_hand_landmarks,
                            mp_holistic.HAND_CONNECTIONS,
                            mp_drawing_styles.get_default_hand_landmarks_style(),
                            mp_drawing_styles.get_default_hand_connections_style()
                        )

                    if results.right_hand_landmarks:
                        mp_drawing.draw_landmarks(
                            frame,
                            results.right_hand_landmarks,
                            mp_holistic.HAND_CONNECTIONS,
                            mp_drawing_styles.get_default_hand_landmarks_style(),
                            mp_drawing_styles.get_default_hand_connections_style()
                        )

                # Display info overlay (top-left)
                y_offset = 30
                if results.pose_landmarks:
                    cv2.putText(frame, "✓ Pose detected (33 landmarks)",
                              (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    y_offset += 30

                if results.face_landmarks:
                    cv2.putText(frame, "✓ Face detected (468 landmarks)",
                              (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
                    y_offset += 30

                hand_count = 0
                if results.left_hand_landmarks:
                    hand_count += 1
                if results.right_hand_landmarks:
                    hand_count += 1

                if hand_count > 0:
                    cv2.putText(frame, f"✓ {hand_count} hand(s) detected (21 landmarks each)",
                              (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                # Display performance (top-right)
                info_x = frame.shape[1] - 300
                cv2.putText(frame, f"FPS: {self.fps:.1f}",
                          (info_x, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(frame, f"Processing: {processing_time:.1f}ms",
                          (info_x, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

                # Display help (bottom-left)
                if show_help:
                    help_y = frame.shape[0] - 150
                    cv2.putText(frame, "Controls:",
                              (10, help_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    cv2.putText(frame, "1: Toggle pose (green skeleton)",
                              (10, help_y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                    cv2.putText(frame, "2: Toggle face (red mesh)",
                              (10, help_y + 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
                    cv2.putText(frame, "3: Toggle hands (yellow skeleton)",
                              (10, help_y + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                    cv2.putText(frame, "h: Hide help | q: Quit",
                              (10, help_y + 105), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                else:
                    cv2.putText(frame, "Press 'h' for help",
                              (10, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

                # Show frame
                cv2.imshow('MediaPipe Holistic', frame)

                # Handle keypresses
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('h'):
                    show_help = not show_help
                elif key == ord('1'):
                    show_pose = not show_pose
                    print(f"Pose tracking: {'ON' if show_pose else 'OFF'}")
                elif key == ord('2'):
                    show_face = not show_face
                    print(f"Face tracking: {'ON' if show_face else 'OFF'}")
                elif key == ord('3'):
                    show_hands = not show_hands
                    print(f"Hand tracking: {'ON' if show_hands else 'OFF'}")

        except KeyboardInterrupt:
            print("\nStopped by user")

        finally:
            holistic.close()
            self.close_camera()

        print("\n" + "="*60)
        print("MediaPipe Holistic test complete!")
        print(f"Final FPS: {self.fps:.1f}")
        print("="*60)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Test vision functionality for DJ R3X',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available cameras
  python test_vision.py --list-cameras

  # Interactive camera selection (shows menu at startup)
  python test_vision.py --mode detect --select-camera

  # Test basic face detection with specific camera
  python test_vision.py --mode detect --camera 0

  # Collect training data for a person
  python test_vision.py --mode train --name "Brandon"

  # Train the recognition model
  python test_vision.py --train

  # Test face recognition
  python test_vision.py --mode recognize

  # Test hand gesture recognition
  python test_vision.py --mode gestures

  # Test combined vision pipeline (all models)
  python test_vision.py --mode combined

  # Test MediaPipe Holistic (face + hands + pose)
  python test_vision.py --mode holistic
        """
    )

    parser.add_argument(
        '--mode',
        choices=['detect', 'train', 'recognize', 'objects', 'gestures', 'combined', 'holistic'],
        help='Test mode: detect (face detection only), train (collect training data), recognize (face recognition), objects (YOLO object detection), gestures (hand gesture recognition), combined (all models together), holistic (MediaPipe Holistic - face+hands+pose)'
    )

    parser.add_argument(
        '--name',
        type=str,
        help='Person name for training mode'
    )

    parser.add_argument(
        '--images',
        type=int,
        default=20,
        help='Number of training images to collect (default: 20)'
    )

    parser.add_argument(
        '--camera',
        type=int,
        default=None,
        help='Camera device ID (default: interactive selection if not specified)'
    )

    parser.add_argument(
        '--select-camera',
        action='store_true',
        help='Show interactive camera selection menu'
    )

    parser.add_argument(
        '--list-cameras',
        action='store_true',
        help='List all available cameras and exit'
    )

    parser.add_argument(
        '--train',
        action='store_true',
        help='Train the recognition model from collected images'
    )

    parser.add_argument(
        '--validate',
        action='store_true',
        help='Validate the trained model accuracy using test split'
    )

    args = parser.parse_args()

    # Handle list cameras
    if args.list_cameras:
        available_cameras = VisionTester.list_available_cameras()
        if not available_cameras:
            print("❌ No cameras found!")
            return
        print("\n" + "="*60)
        print("AVAILABLE CAMERAS")
        print("="*60)
        for camera_id, camera_name, info in available_cameras:
            print(f"  [{camera_id}] {camera_name} - {info}")
        print("="*60)
        return

    # Run requested mode
    if args.train:
        # Training doesn't need camera
        tester = VisionTester(camera_id=0)  # Dummy camera ID
        tester.train_recognizer()
        return

    if args.validate:
        # Validation doesn't need camera
        tester = VisionTester(camera_id=0)  # Dummy camera ID
        tester.validate_model()
        return

    # Determine camera ID for modes that need camera
    camera_id = args.camera

    # If no camera specified or --select-camera flag used, show interactive selection
    if camera_id is None or args.select_camera:
        camera_id = VisionTester.select_camera_interactive()
        if camera_id is None:
            print("No camera selected. Exiting.")
            return

    # Create tester
    tester = VisionTester(camera_id=camera_id)

    if args.mode == 'detect':
        tester.test_detection()
    elif args.mode == 'train':
        if not args.name:
            print("❌ Error: --name required for training mode")
            print("   Example: python test_vision.py --mode train --name \"Brandon\"")
            return
        tester.collect_training_data(args.name, args.images)
    elif args.mode == 'recognize':
        tester.test_recognition()
    elif args.mode == 'objects':
        tester.test_object_detection()
    elif args.mode == 'gestures':
        tester.test_hand_gestures()
    elif args.mode == 'combined':
        tester.test_combined_vision()
    elif args.mode == 'holistic':
        tester.test_holistic()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
