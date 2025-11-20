# Quick Start: Webcam Selection

Having trouble with the webcam? Use this utility to select the correct camera.

## Run the webcam selector

```bash
cd cantina_os
./scripts/select-webcam.sh
```

## What you'll see

The utility will detect all cameras and let you:
- See camera names and resolutions
- Test each camera
- Save your selection to `.env`

## Example

You detected two cameras:
- **Camera 0**: Brandon's iPhone Camera (1920x1080) - Continuity Camera
- **Camera 1**: FaceTime HD Camera (1280x720) - Built-in Mac camera

**Recommendation**: Select Camera 1 (FaceTime HD) for best reliability.

## Save and restart

After selecting a camera, restart CantinaOS:

```bash
../venv/bin/python -m cantina_os.main
```

Check the logs to verify the correct camera is being used.

## Full Documentation

See `docs/WEBCAM_SELECTION.md` for complete details.
