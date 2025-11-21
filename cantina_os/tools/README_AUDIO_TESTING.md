# Audio Amplitude Testing Tools

Tools for analyzing and optimizing DJ R3X mouth LED amplitude normalization.

## Quick Start

### Step 1: Capture Audio Sample

```bash
cd /home/user/djr3x_voice/cantina_os

# Capture a test phrase
python tools/capture_tts_audio.py \
  "Hey there! Welcome to Star Tours. Get ready for an amazing adventure across the galaxy!" \
  tools/test_audio.pcm
```

### Step 2: Analyze Amplitude Distribution

```bash
# Install matplotlib if needed
pip install matplotlib

# Run analyzer
python tools/audio_amplitude_analyzer.py tools/test_audio.pcm
```

This will:
- Calculate RMS amplitude timeline (same as production code)
- Test 4 different normalization pipelines
- Show statistics for each (min/max/percentiles/clipping%)
- Display comparison graphs

### Step 3: Interpret Results

**Good LED distribution should have:**
- **Wide range**: Uses 50-255 brightness (not just 0-100)
- **Low clipping**: <5% of samples at max brightness (255)
- **Good contrast**: 75th percentile around 150-200 (visible variety)
- **Minimal silence**: <10% at near-zero brightness

**Bad distribution signs:**
- **Clipped**: >20% at max brightness = over-compressed, boring
- **Narrow range**: 90% percentile <150 = not using full LED capacity
- **Too quiet**: Mean <80 = hard to see mouth movement

## Pipelines Tested

### Current Production
- **AGC Boost**: 2x
- **Python Boost**: 8x
- **Arduino Compression**: double_sqrt (x^0.25)
- **Max Brightness**: 180

**Problem**: Capped at 180, over-boosted (8x clips early)

### Recommended 1 (Balanced)
- **AGC Boost**: 2x
- **Python Boost**: 4x (reduced from 8x)
- **Arduino Compression**: sqrt (x^0.5, less aggressive)
- **Max Brightness**: 255 (full range)

**Improvement**: Better dynamic range, less clipping

### Aggressive (Maximum Contrast)
- **AGC Boost**: 3x
- **Python Boost**: 3x
- **Arduino Compression**: sqrt
- **Max Brightness**: 255

**Use case**: Very dynamic speech, prevents quiet speech from being invisible

### Gentle (Minimal Compression)
- **AGC Boost**: 2x
- **Python Boost**: 6x
- **Arduino Compression**: none (linear)
- **Max Brightness**: 255

**Use case**: Natural dynamics, preserves original amplitude variations

## Understanding the Graphs

### Histogram (Top)
Shows how often each brightness level appears:
- **Left side (0-50)**: Silence and quiet speech
- **Middle (50-150)**: Normal speech
- **Right side (150-255)**: Loud speech
- **Spike at 255**: Clipping (too much!)

**Ideal**: Bell curve centered around 120-160 with long tail to 255

### Timeline Plots
Shows LED brightness over time:
- **Red dashed line**: Max possible (255)
- **Orange dashed line**: Current production cap (180)
- **Filled area**: Actual brightness

**Look for**:
- Smooth curves (good compression)
- Variety in peaks (dynamic)
- Reaching near 255 occasionally (using full range)

## Testing Different Text

Try these test cases:

**Dynamic speech** (varies quiet to loud):
```bash
python tools/capture_tts_audio.py \
  "HELLO EVERYONE! ...and welcome. I'm DJ R3X, your pilot today. GET READY for an incredible journey!" \
  tools/dynamic.pcm
```

**Consistent volume**:
```bash
python tools/capture_tts_audio.py \
  "This is a test of consistent speech volume for LED calibration." \
  tools/consistent.pcm
```

**Quiet speech**:
```bash
python tools/capture_tts_audio.py \
  "Shh... listen carefully. This is a very quiet test." \
  tools/quiet.pcm
```

## Applying Results

Once you identify the best pipeline:

### Update Python Service
Edit `cantina_os/services/eye_light_controller_service_v3_patch.py`:

```python
# Line 130: Change boost value
amplitude = payload.amplitude * 4.0  # Changed from 8.0
```

### Update ElevenLabs AGC
Edit `cantina_os/services/elevenlabs_service.py`:

```python
# Line 524: Change boost value
normalized_amplitude = min(1.0, normalized_amplitude * 3.0)  # Changed from 2.0
```

### Update Arduino Compression
Edit `cantina_os/arduino/rex_face_v3_clean/rex_face_v3_clean.ino`:

**Option 1: Single sqrt** (line 634):
```cpp
float scaledAmp = sqrt(normalizedAmp);  // Single sqrt
// DELETE line 638 (second sqrt)
```

**Option 2: No compression** (line 632):
```cpp
float scaledAmp = normalizedAmp;  // Linear, no compression
// DELETE lines 634 and 638
```

**Update brightness caps** (lines 649, 670, 681, 692):
```cpp
int middleBright = stage1Scale * stage1Scale * 255;  // Changed from 180
int sideBright = stage2Scale * stage2Scale * 240;    // Changed from 160
int cornerBright = stage3Scale * stage3Scale * 220;  // Changed from 140
int bottomBright = stage4Scale * stage4Scale * 255;  // Changed from 200
```

## Advanced: Custom Pipeline Testing

Modify `audio_amplitude_analyzer.py` to test your own settings:

```python
# Add custom pipeline
custom, _ = analyzer.test_pipeline(
    name="My Custom Pipeline",
    agc_boost=2.5,           # Your AGC boost
    python_boost=5.0,        # Your Python boost
    arduino_compression="sqrt",  # "none", "sqrt", "double_sqrt", "log"
    max_brightness=255       # Max LED value
)
pipelines["Custom"] = custom
```

## Troubleshooting

**ImportError: No module named 'matplotlib'**
```bash
pip install matplotlib numpy
```

**ELEVENLABS_API_KEY not found**
```bash
# Make sure .env file exists in project root
cd /home/user/djr3x_voice
cat .env | grep ELEVENLABS_API_KEY
```

**Audio sounds wrong**
```bash
# Play captured PCM to verify it's correct
ffplay -f s16le -ar 24000 -ac 1 tools/test_audio.pcm
```
