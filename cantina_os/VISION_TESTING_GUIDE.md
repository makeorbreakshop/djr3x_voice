# Vision Testing Guide for DJ R3X

This guide walks you through testing the vision functionality before integrating into CantinaOS.

## Quick Start

### 1. Test Face Detection (No Training Required)

This tests if your webcam works and can detect faces:

```bash
cd "/Users/brandoncullum/DJ-R3X Voice/cantina_os"
../venv/bin/python test_vision.py --mode detect
```

**What you'll see:**
- Live video feed with green boxes around detected faces
- FPS counter showing ~20-40 FPS
- Detection latency in milliseconds

**Press 'q' to quit**

---

### 2. Collect Training Data

Collect 20 photos of yourself (or anyone you want R3X to recognize):

```bash
../venv/bin/python test_vision.py --mode train --name "Brandon"
```

**Instructions:**
- Position your face in the frame (you'll see a green box)
- Press **SPACE** to capture an image
- Move your head to different angles, make different expressions
- Collect 20 images (the script will count for you)
- Press 'q' to quit early if needed

**Try to vary:**
- Angles (straight on, left, right, up, down)
- Expressions (neutral, smiling, talking)
- Distance (close, far)
- Lighting (if possible)

**Where images are saved:**
`/Users/brandoncullum/DJ-R3X Voice/cantina_os/vision_data/training/Brandon/`

---

### 3. Train the Recognition Model

After collecting images for everyone you want to recognize, train the model:

```bash
../venv/bin/python test_vision.py --train
```

**What happens:**
- Processes all collected images
- Generates face "encodings" (128-dimensional vectors)
- Saves trained model to: `vision_data/face_encodings.pkl`

**Output example:**
```
Found 2 people:
  - Brandon: 20 images
  - Guest: 15 images

Processing Brandon (20 images)...
  ✅ Processed 20/20

Processing Guest (15 images)...
  ✅ Processed 15/15

✅ Training complete!
   Total encodings: 35
   People: 2
```

---

### 4. Test Face Recognition

Now test real-time face recognition:

```bash
../venv/bin/python test_vision.py --mode recognize
```

**What you'll see:**
- Live video feed
- Green boxes around recognized faces with names and confidence
- Red boxes around unknown faces
- FPS counter (~5-10 FPS for recognition)

**Confidence scores:**
- 0.90+ = Excellent match
- 0.70-0.90 = Good match
- 0.50-0.70 = Uncertain
- <0.50 = Unknown

**Press 'q' to quit**

---

## Full Workflow Example

```bash
# 1. Test camera and face detection
../venv/bin/python test_vision.py --mode detect

# 2. Collect training data for yourself
../venv/bin/python test_vision.py --mode train --name "Brandon"

# 3. Collect data for another person (optional)
../venv/bin/python test_vision.py --mode train --name "Sarah"

# 4. Train the model
../venv/bin/python test_vision.py --train

# 5. Test recognition
../venv/bin/python test_vision.py --mode recognize
```

---

## Advanced Options

### Collect More/Fewer Images

Default is 20 images, but you can adjust:

```bash
# Collect 30 images for better accuracy
../venv/bin/python test_vision.py --mode train --name "Brandon" --images 30

# Quick test with just 10 images
../venv/bin/python test_vision.py --mode train --name "Test" --images 10
```

### Use Different Camera

If you have multiple cameras:

```bash
# Use camera 1 instead of default (0)
../venv/bin/python test_vision.py --mode detect --camera 1
```

---

## Expected Performance

Based on your hardware (Apple Silicon Mac):

| Operation | Expected FPS | Latency |
|-----------|-------------|---------|
| Face Detection (Haar Cascade) | 20-40 FPS | 25-50ms |
| Face Recognition | 5-10 FPS | 100-200ms |
| Recognition (every 3rd frame) | 15-20 FPS display | 300ms effective |

**Note:** The recognition mode processes every 3rd frame to maintain smooth video display while doing CPU-intensive face matching.

---

## Troubleshooting

### Camera Won't Open

```
❌ Failed to open camera 0
```

**Solutions:**
1. Make sure no other app is using the webcam (close Zoom, FaceTime, etc.)
2. Try a different camera ID: `--camera 1`
3. Check camera permissions in System Settings > Privacy & Security > Camera
4. Try unplugging and replugging USB webcam

---

### No Face Detected

```
⚠ Ensure exactly ONE face in frame
```

**Solutions:**
1. Move closer to the camera
2. Ensure good lighting (face should be well-lit)
3. Face the camera directly (not at extreme angles)
4. Remove obstructions (hats, sunglasses, masks)

---

### Poor Recognition Accuracy

```
Always shows "Unknown" or wrong person
```

**Solutions:**
1. Collect more training images (30-50 instead of 20)
2. Vary training images more (different angles, expressions, lighting)
3. Ensure good lighting during both training and recognition
4. Re-train the model: `--train`
5. Try lowering the tolerance (edit `test_vision.py` line 407: change `0.5` to `0.6` for stricter matching)

---

### Low FPS

```
FPS: 2-3 instead of expected 5-10
```

**Solutions:**
1. Close other CPU-intensive apps
2. The script already processes every 3rd frame - this is normal
3. Reduce camera resolution (edit line 85-86 in `test_vision.py`)
4. Use detection mode instead of recognition for faster performance

---

## File Structure

After running the tests, you'll have:

```
cantina_os/
├── test_vision.py                    # Test script
├── VISION_TESTING_GUIDE.md          # This guide
└── vision_data/                     # Created automatically
    ├── training/                    # Training images
    │   ├── Brandon/
    │   │   ├── Brandon_000.jpg
    │   │   ├── Brandon_001.jpg
    │   │   └── ...
    │   └── Sarah/
    │       ├── Sarah_000.jpg
    │       └── ...
    └── face_encodings.pkl           # Trained model
```

---

## What's Next?

Once you've validated that face detection and recognition work:

1. **Integration into CantinaOS**: I'll create a `VisionService` that follows the same architecture as other services
2. **Event-driven**: Vision will emit events like `VISION_PERSON_IDENTIFIED` on the event bus
3. **Mode-aware**: Vision will auto-enable when in INTERACTIVE mode
4. **Auto-engage**: R3X will automatically greet recognized people by name

Let me know when you're ready to integrate, or if you want to test anything else first!

---

## Tips for Best Results

### Training Phase

- **Lighting:** Collect images in similar lighting to where R3X will be used
- **Variety:** Different angles, expressions, and distances improve robustness
- **Quantity:** 20 images is minimum, 30-50 is better for accuracy
- **Background:** Vary background if possible (R3X will see you in different locations)

### Recognition Phase

- **Consistent lighting:** Recognition works best in similar lighting to training
- **Face the camera:** Works best when facing forward (±30 degrees)
- **Distance:** 2-6 feet from camera is optimal
- **Patience:** May take 1-2 seconds for initial recognition (then updates in real-time)

---

## Privacy Note

All data stays local:
- Training images saved to `vision_data/training/`
- Face encodings (not images) saved to `face_encodings.pkl`
- No cloud uploads, no external services
- Delete `vision_data/` folder to remove all data

---

## Questions?

Common questions:

**Q: Can I delete training images after training?**
A: Yes, once you've run `--train`, the encodings are saved and you can delete the raw images.

**Q: Can I add more people later?**
A: Yes! Collect new training data, then re-run `--train`. It will include everyone.

**Q: How accurate is this?**
A: 99.38% on the LFW benchmark. With good training data, expect 95%+ accuracy.

**Q: Does it work with glasses? Beards?**
A: Yes, but collect training data with/without them for best results.

**Q: Multiple people at once?**
A: Yes! The recognition mode can identify multiple faces simultaneously.
