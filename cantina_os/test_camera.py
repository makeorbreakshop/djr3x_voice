#!/usr/bin/env python3
"""
Quick camera test script to verify webcam functionality
"""

import cv2
import sys

def test_camera(camera_index=0):
    """Test if camera can capture frames."""
    print(f"Testing camera index {camera_index}...")

    # Open camera
    camera = cv2.VideoCapture(camera_index)

    if not camera.isOpened():
        print(f"❌ Failed to open camera {camera_index}")
        return False

    print(f"✓ Camera {camera_index} opened successfully")

    # Try to read a frame
    ret, frame = camera.read()

    if not ret or frame is None:
        print(f"❌ Failed to capture frame from camera {camera_index}")
        camera.release()
        return False

    print(f"✓ Frame captured successfully")
    print(f"  - Frame shape: {frame.shape}")
    print(f"  - Frame dtype: {frame.dtype}")
    print(f"  - Min pixel value: {frame.min()}")
    print(f"  - Max pixel value: {frame.max()}")
    print(f"  - Mean pixel value: {frame.mean():.2f}")

    # Check if frame is too dark
    if frame.mean() < 10:
        print("⚠️  WARNING: Frame is very dark (mean < 10)")
        print("   This could indicate:")
        print("   - Camera lens cover is on")
        print("   - Room is too dark")
        print("   - Camera permission issue")

    # Show live preview
    print("\n📷 Opening live preview window...")
    print("   Press 'q' to quit")
    print("   Press 's' to save a test frame")

    frame_count = 0
    while True:
        ret, frame = camera.read()
        if not ret:
            print("❌ Failed to read frame")
            break

        frame_count += 1

        # Add frame info overlay
        cv2.putText(frame, f"Frame: {frame_count}", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, f"Mean: {frame.mean():.1f}", (10, 70),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, "Press 'q' to quit", (10, 110),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.imshow('Camera Test', frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("Quitting...")
            break
        elif key == ord('s'):
            filename = f"test_frame_{frame_count}.jpg"
            cv2.imwrite(filename, frame)
            print(f"✓ Saved frame to {filename}")

    camera.release()
    cv2.destroyAllWindows()

    print(f"\n✓ Camera test complete. Captured {frame_count} frames.")
    return True

if __name__ == "__main__":
    camera_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    print("=" * 60)
    print("DJ R3X Camera Test")
    print("=" * 60)

    success = test_camera(camera_idx)

    if not success:
        print("\n❌ Camera test failed")
        print("\nTroubleshooting:")
        print("1. Check if camera is being used by another application")
        print("2. Grant camera permissions to Terminal.app in System Settings")
        print("3. Try a different camera index: python test_camera.py 1")
        sys.exit(1)

    print("\n✓ Camera is working properly!")
