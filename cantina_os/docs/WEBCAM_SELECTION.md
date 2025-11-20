# Webcam Selection Utility

## Overview

The webcam selection utility helps you choose the correct camera for VisionService on macOS. This is especially useful when multiple cameras are available (built-in FaceTime HD, Continuity Camera from iPhone/iPad, etc.).

## Quick Start

### Option 1: Run the standalone script

```bash
cd cantina_os
./scripts/select-webcam.sh
```

### Option 2: Run directly with Python

```bash
cd cantina_os
../venv/bin/python select_webcam.py
```

### Option 3: From CantinaOS CLI

```bash
# Start CantinaOS
cd cantina_os
../venv/bin/python -m cantina_os.main

# Then in the CLI:
> select webcam
```

## How It Works

The utility will:

1. **Detect all available cameras** (indices 0-5)
2. **Show camera properties**:
   - Name (from macOS system_profiler)
   - Resolution (width x height)
   - FPS (frames per second)
   - Backend (usually AVFoundation on macOS)
3. **Allow you to test each camera** by capturing a frame
4. **Save your selection** to `.env` as `VISION_CAMERA_INDEX`

## Example Output

```
======================================================================
CantinaOS Webcam Selection Utility
======================================================================

🔍 Detecting cameras...
  Testing camera 0... ✓ Found: Brandon's iPhone Camera (1920x1080)
  Testing camera 1... ✓ Found: FaceTime HD Camera (1280x720)
  Testing camera 2... ✗ Not available

======================================================================
Available Cameras
======================================================================

Camera 0:
  Name:       Brandon's iPhone Camera
  Resolution: 1920x1080
  FPS:        30.0
  Backend:    AVFOUNDATION

Camera 1:
  Name:       FaceTime HD Camera
  Resolution: 1280x720
  FPS:        30.0
  Backend:    AVFOUNDATION

======================================================================

Options:
  [0-5]  Select camera by index
  [t]    Test selected camera
  [q]    Quit without saving
======================================================================

Your choice: 1

📷 Testing camera 1...
  ✓ Successfully captured frame
  Frame shape: (720, 1280, 3)
  Frame size: 2700.0 KB

✓ Saved VISION_CAMERA_INDEX=1 to /path/to/.env

✅ Camera 1 selected and saved!
   (FaceTime HD Camera)

Restart CantinaOS for changes to take effect.
```

## Configuration

The selected camera index is saved to `.env`:

```bash
VISION_CAMERA_INDEX=1
```

VisionService reads this value on startup:

1. First checks `VISION_CAMERA_INDEX` environment variable
2. Falls back to config `camera_index` parameter
3. Falls back to auto-detection (prefers non-iPhone cameras)

## Troubleshooting

### No cameras detected

```
❌ No cameras detected!

Troubleshooting:
  1. Check that your camera is connected
  2. Grant camera permissions to Terminal.app in System Settings
  3. Try running: sudo killall VDCAssistant
```

**Solution**:
- Go to **System Settings > Privacy & Security > Camera**
- Enable camera access for **Terminal** (or iTerm, etc.)
- Restart the utility

### Camera permissions on macOS

If you see permission errors:

1. Open **System Settings**
2. Go to **Privacy & Security > Camera**
3. Enable access for your terminal app (Terminal.app, iTerm2, etc.)
4. Restart your terminal

### Continuity Camera (iPhone) not working

If your iPhone camera is detected but fails to capture frames:

1. Ensure your iPhone is unlocked
2. Check that iPhone and Mac are on the same Apple ID
3. Check that Bluetooth and WiFi are enabled on both devices
4. Try disconnecting and reconnecting

### VDCAssistant issues

If cameras stop working after macOS updates:

```bash
sudo killall VDCAssistant
```

This resets the Video Device Controller assistant.

## Architecture Integration

### VisionService Integration

The `VisionService` reads `VISION_CAMERA_INDEX` during initialization:

```python
# cantina_os/services/vision_service.py:51-67
env_camera_index = os.getenv("VISION_CAMERA_INDEX")
if env_camera_index is not None:
    try:
        preferred_index = int(env_camera_index)
        self.logger.info(f"Using VISION_CAMERA_INDEX from .env: {preferred_index}")
    except ValueError:
        preferred_index = self.config.get("camera_index")
else:
    preferred_index = self.config.get("camera_index")

self.camera_index = self._find_best_camera(preferred_index)
```

### Auto-Detection Behavior

If `VISION_CAMERA_INDEX` is not set, VisionService will:

1. Query macOS `system_profiler` for camera names
2. Test OpenCV camera indices 0-5
3. Skip cameras with "iPhone" in the name (Continuity Cameras)
4. Select the first non-iPhone camera
5. Fall back to camera 0 if no suitable camera found

This ensures the built-in FaceTime HD camera is preferred over Continuity Cameras (which can be unreliable).

## Best Practices

1. **Always test the camera** before saving selection
2. **Use built-in FaceTime HD camera** for best reliability (usually index 1)
3. **Avoid Continuity Cameras** unless necessary (they can disconnect randomly)
4. **Restart CantinaOS** after changing camera selection
5. **Check logs** on startup to verify correct camera is being used

## Related Files

- `cantina_os/select_webcam.py` - Main utility script
- `cantina_os/scripts/select-webcam.sh` - Launcher script
- `cantina_os/services/vision_service.py` - VisionService that uses the camera
- `.env` - Configuration file where `VISION_CAMERA_INDEX` is stored

## Future Improvements

- [ ] Add GUI preview window for each camera
- [ ] Support saving multiple camera profiles
- [ ] Add camera benchmark (FPS, latency tests)
- [ ] Auto-detect camera disconnections and switch to fallback
- [ ] CLI command integration (`select webcam` command)
