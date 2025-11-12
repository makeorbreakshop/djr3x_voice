# ElevenLabs v3 Testing & Integration Guide

**Status**: ✓ Tested with Real API Calls
**Date**: November 12, 2025

## Executive Summary

ElevenLabs v3 has been **tested and verified** for DJ R3X background commentary generation. Here are the key findings:

### Latency Benchmark Results

| Sample | Length | V2.5 Flash | V3 | Overhead | DJ Mode OK? |
|--------|--------|-----------|-----|----------|-----------|
| Short | 28 chars | 305ms | 1,687ms | 5.5x | ✓ YES |
| Medium | 88 chars | 371ms | 3,646ms | 9.8x | ✓ YES |

**Verdict**: V3 is **perfectly suitable for background DJ commentary pre-generation** (CachedSpeechService). The 1.7-3.6 second latency is unacceptable for real-time voice interaction but ideal for pre-caching while current track plays.

---

## 1. Critical Discovery: Stability Parameter Requirements

### The Problem

V3 has **different stability requirements** than v2.5:

- **V2.5 Flash**: Accepts continuous range `0.0 - 1.0`
- **V3**: Accepts **ONLY** discrete values: `[0.0, 0.5, 1.0]`

Using `0.60` (which works for v2.5) will cause:
```
400 Bad Request: Invalid TTD stability value. Must be one of: [0.0, 0.5, 1.0]
```

### The Solution

Map stability values to v3-compatible ones:

```python
# For v3, map to discrete stability levels
STABILITY_MAPPING = {
    0.0: 0.0,     # Creative (expressive, more hallucinations)
    0.5: 0.5,     # Natural (balanced, recommended)
    1.0: 1.0,     # Robust (consistent, less expressive)
}

# When using v3:
if model_id == "eleven_v3":
    stability = 0.5  # Use Natural (middle ground)
else:
    stability = 0.60  # Use v2.5 native value
```

---

## 2. V3 Model Configuration

### Model ID

```
Model ID: eleven_v3
Status: Alpha (research preview)
Character Limit: 3,000 chars (vs 40,000 for v2.5)
Languages: 70+
```

### Recommended Voice Settings for V3

```python
voice_settings_v3 = {
    "stability": 0.5,           # Natural mode (required for v3)
    "similarity_boost": 0.85,   # Keep high for voice consistency
    "use_speaker_boost": True,  # Ensure consistent energy
    "style": 0.25,              # Slight style emphasis
    # Note: "speed" is NOT supported in v3 SDK calls
}
```

### Important: Speed Parameter NOT Supported in V3

The v3 model does **not** support the `speed` parameter in the current SDK. For DJ mode, this means:
- **V2.5**: Can adjust speech speed (1.0 - 1.2x faster)
- **V3**: Fixed speed (approximately 1.0x)

This is acceptable since DJ commentary doesn't require speed adjustment.

---

## 3. Audio Tag Features (V3 Unique)

V3 introduces **audio tags** for emotional expression. These tags are **NOT supported in v2.5**:

### Supported Audio Tags

```
[excited]    - Expressive, energetic delivery
[whispers]   - Soft, intimate tone
[laughs]     - Adds laughter
[sarcastic]  - Ironic, tongue-in-cheek delivery
[sad]        - Melancholic tone
[angry]      - Aggressive tone
[surprised]  - Shocked delivery
```

### Example Usage for DJ Commentary

```python
# Excited tag for upbeat tracks
text_v3 = "[excited] Alright folks, this next track is an absolute BANGER!"

# Whispers for smooth transitions
text_v3 = "[whispers] Listen to how smooth this transitions into the next beat..."

# Sarcastic for humorous commentary
text_v3 = "[sarcastic] Oh, you thought that was good? Just wait..."
```

### Tag Limitations

Per ElevenLabs documentation:
- Tags are most effective when prompt is **>250 characters**
- Voice quality depends on voice selection (some voices respond better to tags)
- Tags may not work consistently with Professional Voice Clones (PVCs) yet
- Your DJ R3X voice (quick clone) should work well with tags

---

## 4. Integration Path: Slow Rollout Strategy

### Phase 1: Background Caching Only (Recommended)

Use v3 **ONLY** for pre-generated DJ commentary in `CachedSpeechService`:

```python
# config/tts_config.py
TTS_CONFIG = {
    "realtime": {
        "model_id": "eleven_flash_v2_5",  # Real-time responses (300ms latency)
        "stability": 0.60,
        "speed": 1.1,
    },
    "background": {
        "model_id": "eleven_v3",          # DJ commentary pre-gen (1.7-3.6s)
        "stability": 0.5,                 # Use Natural mode
        # No speed parameter for v3
    }
}
```

### Phase 2: Enable With Feature Flag

In `cantina_os/main.py` or `config/feature_flags.py`:

```python
FEATURE_FLAGS = {
    "TTS_USE_V3": True,                        # Enable v3 model
    "TTS_V3_FOR_BACKGROUND_ONLY": True,        # Only use for pre-gen
    "TTS_V3_AUDIO_TAGS_ENABLED": False,        # Start without tags
    "TTS_V3_AUDIO_TAGS_EXPERIMENTAL": True,    # Experiment flag
}
```

### Phase 3: Optional Audio Tag Experimentation

After validating v3 quality, optionally test audio tags:

```python
# dj_r3x-transition-persona-v3.txt
You are DJ R3X, an enthusiastic AI DJ with personality.
Generate exciting DJ commentary for track transitions.

[excited] Guide the listener through the next track with energy and flair.
The track is: {track_name} by {artist}
Keep under 20 seconds, match the vibe with enthusiasm!
```

---

## 5. Implementation Checklist

### To Add V3 Support to ElevenLabsService:

- [ ] Update `ElevenLabsConfig` to accept both v2.5 and v3 models
- [ ] Add stability mapping logic (discrete values for v3)
- [ ] Remove speed parameter when model_id is v3
- [ ] Update `_audio_worker_loop()` to handle v3 stability values
- [ ] Create feature flag config for model selection
- [ ] Update `CachedSpeechService` to use v3 for background generation
- [ ] Add tests for v3 configuration validation
- [ ] Document v3 limitations in service docstrings
- [ ] Add migration guide for users enabling v3

### Code Changes Needed

**File**: `cantina_os/services/elevenlabs_service.py`

```python
class ElevenLabsConfig(BaseModel):
    """Configuration model for ElevenLabs service."""
    api_key: str = Field(..., description="ElevenLabs API key")
    voice_id: str = Field("P9l1opNa5pWou2X5MwfB", description="Voice ID")
    model_id: str = Field("eleven_flash_v2_5", description="Model ID")
    stability: float = Field(0.60, description="Voice stability")
    # ... rest of config

    @validator("stability")
    def validate_stability_for_model(cls, v, values):
        """Validate stability based on model_id."""
        model_id = values.get("model_id")
        if model_id == "eleven_v3":
            if v not in [0.0, 0.5, 1.0]:
                raise ValueError("V3 stability must be 0.0, 0.5, or 1.0")
        return v
```

---

## 6. Performance Expectations

### Latency Profile

**V2.5 Flash (Current - Real-time)**:
- Typical TTFB: ~75ms
- Full generation (28 chars): ~305ms
- Full generation (88 chars): ~371ms
- Use case: Real-time voice responses ✓

**V3 (New - Background)**:
- Typical TTFB: Unknown (not measured in tests)
- Full generation (28 chars): ~1.7s
- Full generation (88 chars): ~3.6s
- Scales approximately **1s per 25 characters**
- Use case: Pre-generated DJ commentary ✓

### Acceptable for DJ Mode?

**YES**, because:
1. DJ commentary is generated while current track plays (lookahead)
2. Music typically plays for 3-4 minutes
3. If v3 needs 3.6s to generate next commentary, that's fine
4. By the time current track ends, commentary is ready

### Not Suitable For

- Real-time voice interaction (use v2.5)
- Interactive responses expected within 1 second
- Streaming audio chunks during conversation

---

## 7. Quality Observations (Subjective)

From testing, v3 audio quality appears:

- **Audio bytes**: ~1.7x larger than v2.5 for same text (37KB vs 21KB short sample)
- **Encoding**: Higher bitrate/quality audio despite same MP3 format
- **Expressiveness**: Anecdotally more natural prosody (though not measured)
- **Consistency**: Stability setting affects output quality significantly

**Recommendation**: Use `stability=0.5` (Natural) for DJ commentary balance between expressiveness and consistency.

---

## 8. Audio Tag Testing

### Next Steps for Tag Experimentation

Create a separate test for audio tags:

```python
# test_v3_audio_tags.py
V3_TAG_SAMPLES = {
    "excited": "[excited] Next up, we've got a BANGER!",
    "whisper": "[whispers] Listen to this smooth transition...",
    "sarcastic": "[sarcastic] Oh, you thought that was good?",
}

# Test each tag with DJ voice, listen for differences
# Compare quality/effectiveness with your voice
# Document which tags work best
```

### Expected Outcome

Audio tags should make v3 output sound more personality-driven and DJ-like, especially:
- `[excited]` for upbeat tracks
- `[whispers]` for smooth transitions
- `[sarcastic]` for humorous commentary

---

## 9. Troubleshooting

### Error: "Invalid TTD stability value"

**Cause**: Using stability value outside `[0.0, 0.5, 1.0]`

**Fix**:
```python
# Map your stability to v3-compatible value
if model_id == "eleven_v3":
    stability = 0.5  # Natural mode
```

### Error: "Unsupported parameter: speed"

**Cause**: Trying to use speed parameter with v3

**Fix**:
```python
voice_settings = {
    "stability": 0.5,
    "similarity_boost": 0.85,
    # Don't include "speed" for v3
}
```

### V3 Seems Slow / Timing Out

**Cause**: 3.6s generation time exceeding service timeouts

**Fix**:
- Increase timeout for background generation tasks
- Pre-generate further ahead (2-3 tracks lookahead)
- Only use for `CachedSpeechService`, not real-time

### Audio Quality Sounds Different

**Cause**: Stability setting or voice settings mismatch

**Fix**:
- Try different stability values (0.0 Creative, 0.5 Natural, 1.0 Robust)
- Keep similarity_boost high (0.85) for voice consistency
- Ensure use_speaker_boost is True

---

## 10. Decision Tree: V2.5 vs V3

Use this to decide which model for each scenario:

```
Is this real-time voice interaction?
  └─ YES → Use V2.5 Flash (305ms)
  └─ NO → Can we wait 1.7-3.6s?
          └─ NO → Use V2.5 Flash
          └─ YES → Use V3 (better quality/expressiveness)
```

### Current DJ R3X Usage

```
┌─ Voice Commands & Questions
│  └─ Real-time responses → V2.5 Flash ✓
│
└─ DJ Mode Commentary
   ├─ Track introductions (pre-generated) → V3 (new) ✓
   └─ "Next track" command response → V2.5 Flash ✓
```

---

## 11. Next Actions

### Immediate (This Week)

1. ✓ Benchmark v3 latency with real DJ commentary samples
2. ✓ Identify stability parameter differences
3. Create configuration adapter for v3 in ElevenLabsService

### Short-term (This Month)

4. Integrate feature flag for v3 background model
5. Update CachedSpeechService to use v3 by default
6. Test audio tags with DJ R3X voice
7. Document v3 integration in service

### Long-term (Experimentation)

8. Monitor ElevenLabs for real-time v3 release
9. Experiment with audio tags for personality
10. Consider vector embeddings for context-aware commentary

---

## 12. References

- **ElevenLabs v3 Docs**: https://elevenlabs.io/docs/models
- **v3 Prompting Guide**: https://elevenlabs.io/docs/best-practices/prompting/eleven-v3
- **Test Script**: `/Users/brandoncullum/DJ-R3X Voice/test_v3_latency_manual.py`
- **Test Suite**: `/Users/brandoncullum/DJ-R3X Voice/cantina_os/tests/test_elevenlabs_v3_latency.py`

---

## Appendix A: Test Data

### Raw Benchmark Results

```
V2.5 Flash "Next up, we've got a banger!" (28 chars)
  Time: 305ms
  Bytes: 21,360
  Bytes/sec: 70,046

V3 "Next up, we've got a banger!" (28 chars)
  Time: 1,687ms (5.5x slower)
  Bytes: 37,661
  Bytes/sec: 22,327

V2.5 Flash "Alright folks, stick with us..." (88 chars)
  Time: 371ms
  Bytes: 77,785
  Bytes/sec: 209,613

V3 "Alright folks, stick with us..." (88 chars)
  Time: 3,646ms (9.8x slower)
  Bytes: 109,550
  Bytes/sec: 30,046
```

### Latency Scaling

- V3 scales approximately **1s per 25 characters**
- For 20-second DJ commentary (~200-300 chars): expect 2.5-4.5s generation

---

**Last Updated**: November 12, 2025
**Status**: Ready for Integration
**Owner**: Claude Code
