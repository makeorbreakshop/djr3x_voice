#!/usr/bin/env python3
"""
List all available cameras on the system
"""

import cv2

def list_cameras():
    """Test multiple camera indices to find available cameras."""
    print("Scanning for available cameras...\n")

    available_cameras = []

    # Test camera indices 0-10
    for i in range(11):
        camera = cv2.VideoCapture(i)
        if camera.isOpened():
            ret, frame = camera.read()
            if ret and frame is not None:
                # Get camera properties
                width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = camera.get(cv2.CAP_PROP_FPS)
                backend = camera.getBackendName()

                available_cameras.append(i)
                print(f"✓ Camera {i}: Available")
                print(f"  - Resolution: {width}x{height}")
                print(f"  - FPS: {fps}")
                print(f"  - Backend: {backend}")
                print(f"  - Frame mean brightness: {frame.mean():.2f}")
                print()
            camera.release()

    if not available_cameras:
        print("❌ No cameras found")
        print("\nTroubleshooting:")
        print("1. Check camera permissions in System Settings → Privacy & Security → Camera")
        print("2. Make sure no other app is using the camera")
        print("3. Try disconnecting/reconnecting external cameras")
    else:
        print(f"\n{'='*60}")
        print(f"Found {len(available_cameras)} camera(s): {available_cameras}")
        print(f"{'='*60}")
        print("\nTo use a specific camera in DJ R3X:")
        print("Edit cantina_os/cantina_os/main.py")
        print("Find the VisionService initialization and add:")
        print(f"  vision_service = VisionService(event_bus, config={{'camera_index': <INDEX>}})")
        print(f"\nRecommended: Camera 0 is usually the built-in MacBook camera")
        print(f"            Camera 1+ are usually external/continuity cameras")
        print(f"\nTo configure DJ R3X to use a specific camera:")
        print(f"  1. Edit .env file in project root")
        print(f"  2. Set VISION_CAMERA_INDEX=<INDEX>")
        print(f"  3. Example: VISION_CAMERA_INDEX=1")
        print(f"\nCurrent .env setting:")
        try:
            from dotenv import load_dotenv
            import os
            load_dotenv()
            current_index = os.getenv("VISION_CAMERA_INDEX", "0 (default)")
            print(f"  VISION_CAMERA_INDEX={current_index}")
        except:
            print(f"  (Could not read .env file)")

if __name__ == "__main__":
    print("="*60)
    print("DJ R3X Camera Detection")
    print("="*60)
    print()
    list_cameras()
