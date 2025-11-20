#!/usr/bin/env python3
"""
Webcam Selection Utility for CantinaOS

Interactive CLI tool to detect and select the correct webcam on macOS.
Saves the selected camera index to .env file for use by VisionService.

Usage:
    python select_webcam.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import cv2


class WebcamSelector:
    """
    Interactive webcam selection utility.

    Detects all available cameras, displays their properties,
    and allows user to test and select the correct one.
    """

    def __init__(self):
        self.cameras: Dict[int, dict] = {}
        self.env_path = Path(__file__).parent.parent / ".env"

    def detect_cameras(self) -> Dict[int, dict]:
        """
        Detect all available cameras and their properties.

        Returns:
            Dict mapping camera index to camera info:
            {
                0: {
                    "name": "FaceTime HD Camera",
                    "width": 1280,
                    "height": 720,
                    "fps": 30.0,
                    "backend": "AVFoundation"
                }
            }
        """
        print("\n🔍 Detecting cameras...")
        cameras = {}

        # Get camera names from macOS system_profiler
        system_cameras = self._get_system_camera_names()

        # Test OpenCV camera indices (0-5 should be sufficient)
        for idx in range(6):
            try:
                print(f"  Testing camera {idx}...", end=" ", flush=True)
                camera = cv2.VideoCapture(idx)

                if camera.isOpened():
                    # Get camera properties
                    width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = camera.get(cv2.CAP_PROP_FPS)
                    backend = camera.getBackendName()

                    # Try to match with system camera name
                    camera_name = self._match_camera_name(idx, width, height, system_cameras)

                    cameras[idx] = {
                        "name": camera_name,
                        "width": width,
                        "height": height,
                        "fps": fps if fps > 0 else "unknown",
                        "backend": backend
                    }

                    print(f"✓ Found: {camera_name} ({width}x{height})")
                    camera.release()
                else:
                    print("✗ Not available")

            except Exception as e:
                print(f"✗ Error: {e}")

        return cameras

    def _get_system_camera_names(self) -> list:
        """
        Get camera names from macOS system_profiler.

        Returns:
            List of camera info dicts from system profiler
        """
        try:
            result = subprocess.run(
                ['system_profiler', 'SPCameraDataType', '-json'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get('SPCameraDataType', [])
        except Exception as e:
            print(f"  Warning: Could not get system camera info: {e}")

        return []

    def _match_camera_name(self, idx: int, width: int, height: int,
                          system_cameras: list) -> str:
        """
        Match OpenCV camera index to system camera name based on resolution.

        Args:
            idx: OpenCV camera index
            width: Camera width
            height: Camera height
            system_cameras: List of system camera info from system_profiler

        Returns:
            Matched camera name or generic name
        """
        # Try to match by resolution patterns
        for cam_info in system_cameras:
            cam_name = cam_info.get('_name', '')

            # FaceTime HD is typically 1280x720
            if 'FaceTime' in cam_name and width == 1280 and height == 720:
                return cam_name

            # iPhone/iPad (Continuity Camera) is typically 1920x1080
            if ('iPhone' in cam_name or 'iPad' in cam_name) and width == 1920 and height == 1080:
                return cam_name

        # Generic name if no match
        return f"Camera {idx} ({width}x{height})"

    def test_camera(self, idx: int) -> bool:
        """
        Test a camera by capturing a frame and showing basic info.

        Args:
            idx: Camera index to test

        Returns:
            True if test successful, False otherwise
        """
        try:
            print(f"\n📷 Testing camera {idx}...")
            camera = cv2.VideoCapture(idx)

            if not camera.isOpened():
                print("  ✗ Failed to open camera")
                return False

            # Wait for camera to stabilize
            time.sleep(0.5)

            # Try to capture a frame
            ret, frame = camera.read()
            camera.release()

            if not ret or frame is None:
                print("  ✗ Failed to capture frame")
                return False

            # Show frame info
            print(f"  ✓ Successfully captured frame")
            print(f"  Frame shape: {frame.shape}")
            print(f"  Frame size: {frame.nbytes / 1024:.1f} KB")

            return True

        except Exception as e:
            print(f"  ✗ Test failed: {e}")
            return False

    def display_cameras(self):
        """Display all detected cameras in a formatted table."""
        if not self.cameras:
            print("\n❌ No cameras detected!")
            return

        print("\n" + "="*70)
        print("Available Cameras")
        print("="*70)

        for idx, info in self.cameras.items():
            print(f"\nCamera {idx}:")
            print(f"  Name:       {info['name']}")
            print(f"  Resolution: {info['width']}x{info['height']}")
            print(f"  FPS:        {info['fps']}")
            print(f"  Backend:    {info['backend']}")

        print("\n" + "="*70)

    def get_current_camera_index(self) -> Optional[int]:
        """
        Read the current VISION_CAMERA_INDEX from .env file.

        Returns:
            Current camera index or None if not set
        """
        if not self.env_path.exists():
            return None

        try:
            with open(self.env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('VISION_CAMERA_INDEX='):
                        value = line.split('=', 1)[1].strip()
                        return int(value)
        except Exception as e:
            print(f"Warning: Could not read .env file: {e}")

        return None

    def save_camera_index(self, idx: int) -> bool:
        """
        Save selected camera index to .env file.

        Args:
            idx: Camera index to save

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Read existing .env file
            lines = []
            if self.env_path.exists():
                with open(self.env_path, 'r') as f:
                    lines = f.readlines()

            # Update or append VISION_CAMERA_INDEX
            updated = False
            for i, line in enumerate(lines):
                if line.strip().startswith('VISION_CAMERA_INDEX='):
                    lines[i] = f'VISION_CAMERA_INDEX={idx}\n'
                    updated = True
                    break

            if not updated:
                # Add to end of file
                if lines and not lines[-1].endswith('\n'):
                    lines.append('\n')
                lines.append(f'VISION_CAMERA_INDEX={idx}\n')

            # Write back to .env
            with open(self.env_path, 'w') as f:
                f.writelines(lines)

            print(f"\n✓ Saved VISION_CAMERA_INDEX={idx} to {self.env_path}")
            return True

        except Exception as e:
            print(f"\n✗ Failed to save to .env: {e}")
            return False

    def run(self):
        """Run the interactive webcam selection process."""
        print("="*70)
        print("CantinaOS Webcam Selection Utility")
        print("="*70)

        # Detect cameras
        self.cameras = self.detect_cameras()

        if not self.cameras:
            print("\n❌ No cameras detected!")
            print("\nTroubleshooting:")
            print("  1. Check that your camera is connected")
            print("  2. Grant camera permissions to Terminal.app in System Settings")
            print("  3. Try running: sudo killall VDCAssistant")
            sys.exit(1)

        # Display detected cameras
        self.display_cameras()

        # Show current selection
        current_idx = self.get_current_camera_index()
        if current_idx is not None:
            print(f"\n📌 Current selection: Camera {current_idx}")
            if current_idx in self.cameras:
                print(f"   ({self.cameras[current_idx]['name']})")
        else:
            print("\n📌 No camera currently configured in .env")

        # Interactive selection
        while True:
            print("\n" + "="*70)
            print("Options:")
            print("  [0-5]  Select camera by index")
            print("  [t]    Test selected camera")
            print("  [q]    Quit without saving")
            print("="*70)

            choice = input("\nYour choice: ").strip().lower()

            if choice == 'q':
                print("\n👋 Exiting without changes")
                sys.exit(0)

            if choice == 't':
                # Test camera
                if current_idx is None:
                    print("\n⚠️  No camera selected yet")
                    continue

                self.test_camera(current_idx)
                continue

            # Try to parse as camera index
            try:
                idx = int(choice)

                if idx not in self.cameras:
                    print(f"\n❌ Camera {idx} not available")
                    print(f"   Available cameras: {list(self.cameras.keys())}")
                    continue

                # Test the camera
                if not self.test_camera(idx):
                    print(f"\n⚠️  Camera {idx} test failed")
                    retry = input("Save anyway? [y/N]: ").strip().lower()
                    if retry != 'y':
                        continue

                # Save selection
                if self.save_camera_index(idx):
                    current_idx = idx
                    print(f"\n✅ Camera {idx} selected and saved!")
                    print(f"   ({self.cameras[idx]['name']})")
                    print("\nRestart CantinaOS for changes to take effect.")

                    # Ask if done
                    done = input("\nDone? [Y/n]: ").strip().lower()
                    if done != 'n':
                        sys.exit(0)

            except ValueError:
                print(f"\n❌ Invalid choice: {choice}")
                continue


def main():
    """Main entry point."""
    try:
        selector = WebcamSelector()
        selector.run()
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
