# Voice Biometrics Research: Local Speaker Recognition Libraries

**Research Date:** 2025-11-14
**Objective:** Evaluate open-source Python libraries for local voice biometrics and speaker recognition for DJ R3X

---

## Executive Summary

Three leading open-source libraries provide local speaker recognition without cloud dependencies:

| Library | Best For | Embedding Size | Performance | Model Size | Complexity |
|---------|----------|----------------|-------------|------------|------------|
| **Resemblyzer** | Quick prototyping, simple API | 256-dim | 1000x real-time (GPU) | Small (~17MB) | Low |
| **SpeechBrain** | Production, accuracy, flexibility | 192/768-dim | Fast (ECAPA-TDNN) | Medium (~50MB) | Medium |
| **Pyannote.audio** | Research, advanced features | Variable | 69ms inference | Medium-Large | High |

**Recommendation for DJ R3X:** Start with **Resemblyzer** for MVP (simplest integration), migrate to **SpeechBrain ECAPA-TDNN** for production (best accuracy/performance balance).

---

## 1. Resemblyzer

### Overview
- **Repository:** https://github.com/resemble-ai/Resemblyzer
- **Model:** GE2E (Generalized End-to-End) loss-based voice encoder
- **Embedding Size:** 256 dimensions
- **Performance:** ~1000x real-time on GTX 1080, runs on CPU/GPU
- **Model Architecture:** PyTorch-based, pre-trained on speaker verification
- **Accuracy:** Not published, but suitable for general speaker verification tasks

### Installation

```bash
pip install resemblyzer
```

**Dependencies:**
- Python 3.5+
- PyTorch
- NumPy
- SciPy (for similarity calculations)
- librosa/soundfile (for audio loading)

### Basic Usage: Extract Embeddings

```python
from resemblyzer import VoiceEncoder, preprocess_wav
from pathlib import Path
import numpy as np

# Initialize encoder (loads model, takes a few seconds on first run)
encoder = VoiceEncoder(device="cpu")  # or "cuda" for GPU

# Load and preprocess audio
audio_path = Path("speaker1.wav")
wav = preprocess_wav(audio_path)

# Extract single utterance embedding
embedding = encoder.embed_utterance(wav)
# Returns: (256,) numpy array
```

### Speaker Verification: Compare Two Speakers

```python
from resemblyzer import VoiceEncoder, preprocess_wav
from pathlib import Path
import numpy as np

encoder = VoiceEncoder()

# Load two audio samples
wav1 = preprocess_wav(Path("speaker1.wav"))
wav2 = preprocess_wav(Path("speaker2.wav"))

# Extract embeddings
embed1 = encoder.embed_utterance(wav1)
embed2 = encoder.embed_utterance(wav2)

# Compute cosine similarity (embeddings are already L2-normed)
similarity = np.dot(embed1, embed2)  # Range: [-1, 1]

# Apply threshold for verification
threshold = 0.75  # Tune based on your data
if similarity >= threshold:
    print(f"Same speaker (similarity: {similarity:.3f})")
else:
    print(f"Different speakers (similarity: {similarity:.3f})")
```

**Alternative with SciPy:**
```python
from scipy.spatial.distance import cosine

# Cosine distance (0 = identical, 2 = opposite)
distance = cosine(embed1, embed2)
similarity = 1 - distance

print(f"Cosine similarity: {similarity:.3f}")
```

### Robust Speaker Profiles: Multiple Samples

For better accuracy, use multiple audio samples per speaker:

```python
from resemblyzer import VoiceEncoder, preprocess_wav
from pathlib import Path
import numpy as np

encoder = VoiceEncoder()

# Load multiple samples from the same speaker
speaker_samples = [
    preprocess_wav(Path("speaker1_sample1.wav")),
    preprocess_wav(Path("speaker1_sample2.wav")),
    preprocess_wav(Path("speaker1_sample3.wav")),
]

# Method 1: Average embeddings manually
embeddings = [encoder.embed_utterance(wav) for wav in speaker_samples]
speaker_profile = np.mean(embeddings, axis=0)

# Method 2: Use embed_speaker (does averaging internally)
speaker_profile = encoder.embed_speaker(speaker_samples)

# Now compare against new audio
test_wav = preprocess_wav(Path("test_audio.wav"))
test_embed = encoder.embed_utterance(test_wav)

similarity = np.dot(speaker_profile, test_embed)
print(f"Similarity to speaker profile: {similarity:.3f}")
```

### Real-Time Processing Example

```python
from resemblyzer import VoiceEncoder
import numpy as np
import sounddevice as sd
import queue

# Setup
encoder = VoiceEncoder(device="cuda")  # Use GPU for faster inference
audio_queue = queue.Queue()
sample_rate = 16000  # Resemblyzer expects 16kHz audio
chunk_duration = 3.0  # Minimum 3 seconds recommended

def audio_callback(indata, frames, time, status):
    """Called by sounddevice for each audio chunk"""
    audio_queue.put(indata.copy())

# Start streaming
with sd.InputStream(samplerate=sample_rate, channels=1,
                    callback=audio_callback, blocksize=int(sample_rate * chunk_duration)):
    print("Recording... speak for at least 3 seconds")

    # Collect audio chunk
    audio_chunk = audio_queue.get()

    # Flatten and normalize
    audio_flat = audio_chunk.flatten()

    # Extract embedding
    embedding = encoder.embed_utterance(audio_flat)

    # Compare with stored speaker profile
    similarity = np.dot(stored_speaker_profile, embedding)
    print(f"Real-time similarity: {similarity:.3f}")
```

### Minimum Audio Duration

- **Recommended:** 5-30 seconds for speaker profiles
- **Absolute Minimum:** ~1-2 seconds (but accuracy degrades)
- **Optimal:** 10-15 seconds from varied speech content

**Note:** Longer samples capture more voice variation (pitch, tone, speaking styles), improving robustness.

### Performance Characteristics

| Metric | Value |
|--------|-------|
| Inference Speed | ~1000x real-time (GTX 1080) |
| CPU Processing | ~100-200x real-time (modern CPU) |
| I/O Overhead | Minimum 10ms |
| GPU Memory | ~500MB |
| Model Load Time | 2-5 seconds (first time) |
| Embedding Size | 256 floats = 1KB per speaker |

### Pros & Cons

**Pros:**
- Simple, intuitive API (3-4 lines for basic verification)
- Fast inference (real-time capable)
- Small model size (~17MB)
- CPU-friendly (no GPU required)
- Pre-trained, no fine-tuning needed
- Good documentation and examples

**Cons:**
- No official accuracy metrics published
- Limited customization options
- Not actively maintained (last update 2020)
- No streaming API (need to buffer audio chunks)
- Fixed 16kHz sample rate requirement

### Integration with DJ R3X

**Proposed Service Architecture:**
```python
# cantina_os/services/speaker_recognition_service.py

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.core.event_payloads import (
    TranscriptionTextPayload,
    SpeakerVerificationPayload
)
from resemblyzer import VoiceEncoder, preprocess_wav
import numpy as np
from pathlib import Path

class SpeakerRecognitionService(BaseService):
    def __init__(self, event_bus, config=None):
        super().__init__(service_name="speaker_recognition", event_bus=event_bus)
        self._encoder = None
        self._speaker_profiles = {}  # speaker_id -> embedding
        self._config = config or {}
        self._threshold = self._config.get("similarity_threshold", 0.75)

    async def _start(self):
        # Initialize voice encoder
        self._encoder = VoiceEncoder(device=self._config.get("device", "cpu"))

        # Load known speaker profiles from disk
        await self._load_speaker_profiles()

        # Subscribe to audio events
        self._event_bus.on(EventTopics.AUDIO_CHUNK_READY, self._handle_audio_chunk)

    async def _load_speaker_profiles(self):
        """Load stored speaker embeddings from disk"""
        profiles_dir = Path(self._config.get("profiles_dir", "./speaker_profiles"))
        if profiles_dir.exists():
            for profile_file in profiles_dir.glob("*.npy"):
                speaker_id = profile_file.stem
                embedding = np.load(profile_file)
                self._speaker_profiles[speaker_id] = embedding
                self.logger.info(f"Loaded speaker profile: {speaker_id}")

    async def _handle_audio_chunk(self, payload):
        """Process audio chunk for speaker identification"""
        audio_data = payload.audio_data  # numpy array

        # Extract embedding
        embedding = self._encoder.embed_utterance(audio_data)

        # Compare against known speakers
        best_match = None
        best_similarity = -1

        for speaker_id, profile in self._speaker_profiles.items():
            similarity = np.dot(profile, embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = speaker_id

        # Emit verification result
        verified = best_similarity >= self._threshold if best_match else False

        self._event_bus.emit(
            EventTopics.SPEAKER_VERIFIED,
            SpeakerVerificationPayload(
                speaker_id=best_match if verified else "unknown",
                similarity_score=float(best_similarity),
                verified=verified,
                threshold=self._threshold
            )
        )

    async def enroll_speaker(self, speaker_id: str, audio_samples: list):
        """Enroll a new speaker from multiple audio samples"""
        # Extract embeddings from all samples
        embeddings = [self._encoder.embed_utterance(sample) for sample in audio_samples]

        # Average to create robust profile
        speaker_profile = np.mean(embeddings, axis=0)

        # Store in memory and disk
        self._speaker_profiles[speaker_id] = speaker_profile

        profiles_dir = Path(self._config.get("profiles_dir", "./speaker_profiles"))
        profiles_dir.mkdir(exist_ok=True)
        np.save(profiles_dir / f"{speaker_id}.npy", speaker_profile)

        self.logger.info(f"Enrolled speaker: {speaker_id}")
```

---

## 2. SpeechBrain ECAPA-TDNN

### Overview
- **Repository:** https://github.com/speechbrain/speechbrain
- **Model:** ECAPA-TDNN (Emphasized Channel Attention, Propagation and Aggregation)
- **Embedding Size:** 192 dimensions (default) or 768 dimensions (alternative)
- **Performance:** EER 0.69-0.80% on VoxCeleb (state-of-the-art)
- **Inference:** Fast, optimized for production
- **Model Size:** ~50MB for ECAPA-TDNN

### Installation

```bash
pip install speechbrain
```

**Dependencies:**
- PyTorch
- torchaudio
- HuggingFace Hub (for model downloads)

### Basic Usage: Speaker Verification

```python
from speechbrain.inference.speaker import SpeakerRecognition
import torchaudio

# Initialize pre-trained model
verification = SpeakerRecognition.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir="pretrained_models/spkrec-ecapa-voxceleb"
)

# Verify two audio files
score, prediction = verification.verify_files(
    "speaker1.wav",
    "speaker2.wav"
)

print(f"Similarity score: {score:.4f}")
print(f"Same speaker: {bool(prediction)}")  # 1 = same, 0 = different
```

### Extract Speaker Embeddings

```python
from speechbrain.inference.speaker import EncoderClassifier
import torchaudio

# Initialize encoder
classifier = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir="pretrained_models/ecapa"
)

# Load audio
signal, fs = torchaudio.load("speaker.wav")

# Extract embedding
embedding = classifier.encode_batch(signal)
# Returns: torch.Tensor of shape (1, 192) or (1, 768) depending on model variant

# Convert to numpy for storage
embedding_np = embedding.squeeze().cpu().numpy()
```

### Batch Processing: Compare Multiple Speakers

```python
from speechbrain.inference.speaker import EncoderClassifier
import torchaudio
import torch

classifier = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb"
)

# Load multiple audio files
audio_files = ["speaker1.wav", "speaker2.wav", "speaker3.wav"]
embeddings = []

for file in audio_files:
    signal, fs = torchaudio.load(file)
    emb = classifier.encode_batch(signal)
    embeddings.append(emb)

# Stack embeddings
embeddings_tensor = torch.cat(embeddings, dim=0)  # Shape: (3, 192)

# Compute pairwise similarities using cosine similarity
from torch.nn.functional import cosine_similarity

similarity_matrix = torch.zeros((len(embeddings), len(embeddings)))
for i in range(len(embeddings)):
    for j in range(len(embeddings)):
        sim = cosine_similarity(embeddings[i], embeddings[j])
        similarity_matrix[i, j] = sim

print(similarity_matrix)
```

### Real-Time Streaming Example

```python
from speechbrain.inference.speaker import EncoderClassifier
import torchaudio
import torch
import sounddevice as sd
import numpy as np

# Initialize model
classifier = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    run_opts={"device": "cuda"}  # Use GPU for speed
)

# Audio parameters
sample_rate = 16000
chunk_duration = 3  # seconds
chunk_size = sample_rate * chunk_duration

def record_and_verify(stored_embedding, threshold=0.8):
    """Record audio and verify against stored speaker"""
    print(f"Recording {chunk_duration} seconds...")

    # Record audio
    audio = sd.rec(int(chunk_size), samplerate=sample_rate, channels=1, dtype='float32')
    sd.wait()

    # Convert to tensor
    audio_tensor = torch.from_numpy(audio.T)  # Shape: (1, samples)

    # Extract embedding
    new_embedding = classifier.encode_batch(audio_tensor)

    # Compare
    similarity = torch.nn.functional.cosine_similarity(
        stored_embedding, new_embedding
    ).item()

    verified = similarity >= threshold
    print(f"Similarity: {similarity:.3f} - Verified: {verified}")

    return verified, similarity

# Example: Enroll speaker
print("Enrolling speaker - please speak for 3 seconds")
enrollment_audio = sd.rec(int(chunk_size), samplerate=sample_rate, channels=1, dtype='float32')
sd.wait()
enrollment_tensor = torch.from_numpy(enrollment_audio.T)
stored_embedding = classifier.encode_batch(enrollment_tensor)

# Verify
print("\nVerification attempt - please speak for 3 seconds")
verified, score = record_and_verify(stored_embedding)
```

### Advanced: Custom Threshold Tuning

```python
from speechbrain.inference.speaker import SpeakerRecognition
import numpy as np
from sklearn.metrics import roc_curve

# Load verification model
verification = SpeakerRecognition.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb"
)

# Collect verification scores from test set
scores = []
labels = []  # 1 = same speaker, 0 = different

# ... populate scores and labels from your test data ...

# Compute ROC curve
fpr, tpr, thresholds = roc_curve(labels, scores)

# Find Equal Error Rate (EER) point
fnr = 1 - tpr
eer_threshold = thresholds[np.nanargmin(np.absolute((fnr - fpr)))]
eer_value = fpr[np.nanargmin(np.absolute((fnr - fpr)))]

print(f"Equal Error Rate: {eer_value:.4f}")
print(f"Optimal threshold: {eer_threshold:.4f}")
```

### Minimum Audio Duration

- **Recommended:** 3-5 seconds for reliable verification
- **Absolute Minimum:** 1 second (degraded accuracy)
- **Training Duration:** Model trained on variable-length utterances (1-20 seconds)

### Performance Characteristics

| Metric | Value |
|--------|-------|
| EER (VoxCeleb1-Test) | 0.69-0.80% |
| Embedding Dimension | 192 (default) / 768 (large) |
| Inference Time | ~50-100ms per utterance (GPU) |
| Model Parameters | ~6.5M (ECAPA-TDNN-512) |
| Sample Rate | 16kHz (auto-resampled) |
| GPU Memory | ~1-2GB |

### Pros & Cons

**Pros:**
- State-of-the-art accuracy (0.69% EER)
- Well-maintained, active development
- Comprehensive toolkit (ASR, TTS, speaker ID all in one)
- GPU-optimized for production
- Automatic audio normalization (resampling, mono conversion)
- Pre-trained on large VoxCeleb dataset
- Easy fine-tuning on custom data

**Cons:**
- Heavier dependencies (full SpeechBrain toolkit)
- Larger model size (~50MB vs Resemblyzer's ~17MB)
- More complex API (steeper learning curve)
- Requires PyTorch familiarity for advanced use

### Integration with DJ R3X

**Production-Ready Service:**
```python
# cantina_os/services/speechbrain_speaker_service.py

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.core.event_payloads import SpeakerVerificationPayload
from speechbrain.inference.speaker import EncoderClassifier
import torch
import torchaudio
from pathlib import Path
import json

class SpeechBrainSpeakerService(BaseService):
    def __init__(self, event_bus, config=None):
        super().__init__(service_name="speechbrain_speaker", event_bus=event_bus)
        self._classifier = None
        self._speaker_profiles = {}
        self._config = config or {}
        self._threshold = self._config.get("threshold", 0.8)

    async def _start(self):
        # Initialize ECAPA-TDNN model
        self._classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=self._config.get("model_dir", "./models/ecapa"),
            run_opts={"device": self._config.get("device", "cuda")}
        )

        # Load speaker profiles
        await self._load_profiles()

        # Subscribe to events
        self._event_bus.on(EventTopics.AUDIO_BUFFER_READY, self._verify_speaker)

    async def _load_profiles(self):
        """Load speaker embeddings from disk"""
        profiles_dir = Path(self._config.get("profiles_dir", "./speaker_profiles"))
        metadata_file = profiles_dir / "metadata.json"

        if metadata_file.exists():
            with open(metadata_file) as f:
                metadata = json.load(f)

            for speaker_id, info in metadata.items():
                embedding_file = profiles_dir / info["embedding_file"]
                embedding = torch.load(embedding_file)
                self._speaker_profiles[speaker_id] = {
                    "embedding": embedding,
                    "name": info.get("name", speaker_id),
                    "enrolled_date": info.get("enrolled_date")
                }
                self.logger.info(f"Loaded profile: {speaker_id}")

    async def _verify_speaker(self, payload):
        """Verify speaker from audio buffer"""
        # Convert audio to tensor
        audio_tensor = torch.from_numpy(payload.audio_data).unsqueeze(0)

        # Extract embedding
        query_embedding = self._classifier.encode_batch(audio_tensor)

        # Compare against all known speakers
        best_match = None
        best_score = -1.0

        for speaker_id, profile in self._speaker_profiles.items():
            stored_embedding = profile["embedding"]

            # Compute cosine similarity
            similarity = torch.nn.functional.cosine_similarity(
                query_embedding, stored_embedding, dim=1
            ).item()

            if similarity > best_score:
                best_score = similarity
                best_match = speaker_id

        # Emit verification result
        verified = best_score >= self._threshold if best_match else False

        self._event_bus.emit(
            EventTopics.SPEAKER_VERIFIED,
            SpeakerVerificationPayload(
                speaker_id=best_match if verified else "unknown",
                speaker_name=self._speaker_profiles[best_match]["name"] if verified else None,
                similarity_score=float(best_score),
                verified=verified,
                threshold=self._threshold,
                conversation_id=payload.conversation_id
            )
        )

    async def enroll_speaker(self, speaker_id: str, speaker_name: str,
                            audio_samples: list[torch.Tensor]):
        """Enroll new speaker with multiple samples"""
        # Extract embeddings from all samples
        embeddings = []
        for sample in audio_samples:
            emb = self._classifier.encode_batch(sample)
            embeddings.append(emb)

        # Average embeddings for robust profile
        avg_embedding = torch.mean(torch.stack(embeddings), dim=0)

        # Save to disk
        profiles_dir = Path(self._config.get("profiles_dir", "./speaker_profiles"))
        profiles_dir.mkdir(exist_ok=True)

        embedding_file = f"{speaker_id}_embedding.pt"
        torch.save(avg_embedding, profiles_dir / embedding_file)

        # Update metadata
        metadata_file = profiles_dir / "metadata.json"
        metadata = {}
        if metadata_file.exists():
            with open(metadata_file) as f:
                metadata = json.load(f)

        from datetime import datetime
        metadata[speaker_id] = {
            "name": speaker_name,
            "embedding_file": embedding_file,
            "enrolled_date": datetime.now().isoformat(),
            "num_samples": len(audio_samples)
        }

        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

        # Store in memory
        self._speaker_profiles[speaker_id] = {
            "embedding": avg_embedding,
            "name": speaker_name,
            "enrolled_date": metadata[speaker_id]["enrolled_date"]
        }

        self.logger.info(f"Enrolled speaker: {speaker_name} ({speaker_id})")
```

---

## 3. Pyannote.audio

### Overview
- **Repository:** https://github.com/pyannote/pyannote-audio
- **Model:** TDNN-based x-vector with SincNet features
- **Embedding Size:** Variable (typically 512 dimensions)
- **Performance:** 2.8% EER on VoxCeleb1 (without VAD/PLDA)
- **Inference:** 69ms per utterance (ECAPA variant)
- **Focus:** Research-grade toolkit with advanced features

### Installation

```bash
pip install pyannote.audio

# For latest features
pip install --upgrade pyannote.audio
```

**Note:** Requires HuggingFace authentication for some models.

### HuggingFace Authentication

```python
# Create access token at hf.co/settings/tokens
# Accept model conditions at huggingface.co/pyannote/embedding

from huggingface_hub import login
login(token="YOUR_HF_TOKEN")
```

### Basic Usage: Extract Embeddings

```python
from pyannote.audio import Model, Inference

# Load pre-trained embedding model
model = Model.from_pretrained(
    "pyannote/embedding",
    use_auth_token="YOUR_HF_TOKEN"
)

# Create inference pipeline
inference = Inference(model, window="whole")

# Extract embedding from entire audio file
embedding = inference("speaker.wav")
# Returns: (1, D) numpy array where D is embedding dimension
```

### Speaker Verification

```python
from pyannote.audio import Model, Inference
from scipy.spatial.distance import cdist

model = Model.from_pretrained("pyannote/embedding", use_auth_token="YOUR_TOKEN")
inference = Inference(model, window="whole")

# Extract embeddings
embedding1 = inference("speaker1.wav")
embedding2 = inference("speaker2.wav")

# Compute cosine distance (0 = same, 2 = very different)
distance = cdist(embedding1, embedding2, metric="cosine")[0, 0]
similarity = 1 - (distance / 2)  # Convert to [0, 1] range

print(f"Cosine distance: {distance:.4f}")
print(f"Similarity: {similarity:.4f}")

# Threshold-based verification
threshold_distance = 0.6  # Tune on validation set
verified = distance < threshold_distance
print(f"Same speaker: {verified}")
```

### GPU Acceleration

```python
import torch
from pyannote.audio import Model, Inference

model = Model.from_pretrained("pyannote/embedding", use_auth_token="YOUR_TOKEN")
inference = Inference(model, window="whole")

# Move to GPU
inference.to(torch.device("cuda"))

# Extract embedding (now on GPU)
embedding = inference("audio.wav")
```

### Segment-Based Extraction

Extract embedding from specific time segment:

```python
from pyannote.audio import Model, Inference
from pyannote.core import Segment

model = Model.from_pretrained("pyannote/embedding", use_auth_token="YOUR_TOKEN")
inference = Inference(model, window="whole")

# Extract from specific time range (13.37s to 19.81s)
excerpt = Segment(13.37, 19.81)
embedding = inference.crop("audio.wav", excerpt)
```

### Sliding Window Analysis

Useful for detecting speaker changes over time:

```python
from pyannote.audio import Model, Inference

model = Model.from_pretrained("pyannote/embedding", use_auth_token="YOUR_TOKEN")

# Extract embeddings using sliding window
inference = Inference(model, window="sliding", duration=3.0, step=1.0)
embeddings = inference("long_audio.wav")

# Returns: SlidingWindowFeature (N x D) where N = number of windows
print(f"Extracted {len(embeddings)} embeddings")
print(f"Embedding dimension: {embeddings.data.shape[1]}")

# Access individual window embeddings
for i, (timestamp, embedding) in enumerate(embeddings):
    print(f"Window {i}: {timestamp} -> embedding shape {embedding.shape}")
```

### WeSpeaker Model (Alternative)

Pyannote also supports WeSpeaker models:

```python
from pyannote.audio import Model, Inference

# Use WeSpeaker ResNet34 model
model = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
inference = Inference(model, window="whole")

embedding = inference("speaker.wav")
```

### Minimum Audio Duration

- **Recommended:** 3+ seconds for reliable embeddings
- **Model Training:** 500ms chunks (can handle very short segments)
- **Practical Minimum:** 1-2 seconds

### Performance Characteristics

| Metric | Value |
|--------|-------|
| EER (VoxCeleb1) | 2.8% (without VAD/PLDA) |
| Inference Time | 69ms (ECAPA variant) |
| Embedding Dimension | 512 (x-vector model) |
| Sample Rate | Flexible (auto-handled) |
| Model Architecture | TDNN + SincNet |

### Pros & Cons

**Pros:**
- Research-grade quality
- Flexible model selection (x-vector, ECAPA, WeSpeaker)
- Advanced features (diarization, VAD, overlapped speech detection)
- Sliding window support for temporal analysis
- Active development and maintenance
- Integration with HuggingFace ecosystem

**Cons:**
- Requires HuggingFace authentication
- More complex setup than Resemblyzer
- Higher EER than SpeechBrain ECAPA-TDNN (2.8% vs 0.69%)
- Primarily designed for diarization (speaker ID less emphasized)
- Heavier dependencies

### Integration with DJ R3X

**Research/Advanced Service:**
```python
# cantina_os/services/pyannote_speaker_service.py

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from pyannote.audio import Model, Inference
from scipy.spatial.distance import cdist
import torch
from pathlib import Path
import json

class PyannoteSpeakerService(BaseService):
    def __init__(self, event_bus, config=None):
        super().__init__(service_name="pyannote_speaker", event_bus=event_bus)
        self._model = None
        self._inference = None
        self._speaker_profiles = {}
        self._config = config or {}

    async def _start(self):
        # Initialize model
        model_name = self._config.get("model", "pyannote/embedding")
        hf_token = self._config.get("hf_token")

        self._model = Model.from_pretrained(model_name, use_auth_token=hf_token)
        self._inference = Inference(self._model, window="whole")

        # GPU support
        if self._config.get("use_gpu", True) and torch.cuda.is_available():
            self._inference.to(torch.device("cuda"))

        # Load profiles
        await self._load_profiles()

        # Subscribe to events
        self._event_bus.on(EventTopics.AUDIO_SEGMENT_READY, self._verify_speaker)

    async def _verify_speaker(self, payload):
        """Verify speaker from audio segment"""
        # Save audio temporarily (pyannote works with files)
        import tempfile
        import soundfile as sf

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            sf.write(tmp.name, payload.audio_data, payload.sample_rate)
            tmp_path = tmp.name

        try:
            # Extract embedding
            query_embedding = self._inference(tmp_path)

            # Compare against known speakers
            best_match = None
            best_distance = float('inf')

            for speaker_id, profile in self._speaker_profiles.items():
                stored_embedding = profile["embedding"]
                distance = cdist(query_embedding, stored_embedding, metric="cosine")[0, 0]

                if distance < best_distance:
                    best_distance = distance
                    best_match = speaker_id

            # Convert distance to similarity and apply threshold
            threshold = self._config.get("distance_threshold", 0.6)
            verified = best_distance < threshold if best_match else False

            self._event_bus.emit(
                EventTopics.SPEAKER_VERIFIED,
                {
                    "speaker_id": best_match if verified else "unknown",
                    "distance": float(best_distance),
                    "verified": verified
                }
            )
        finally:
            # Cleanup temp file
            Path(tmp_path).unlink(missing_ok=True)
```

---

## 4. Comparison Matrix

### Accuracy Comparison

| Library | Model | EER (VoxCeleb1) | Metric |
|---------|-------|-----------------|--------|
| **SpeechBrain** | ECAPA-TDNN | **0.69-0.80%** | Best |
| **Pyannote** | x-vector + SincNet | 2.8% | Good |
| **Resemblyzer** | GE2E | Not published | Adequate |

### Speed Comparison (Inference Time)

| Library | GPU (RTX 3080) | CPU (i7) | Real-Time Factor |
|---------|----------------|----------|------------------|
| **Resemblyzer** | ~1ms | ~10ms | 1000x (GPU) |
| **SpeechBrain** | ~50ms | ~200ms | 60x (GPU) |
| **Pyannote** | ~69ms | ~300ms | 43x (GPU) |

### Resource Requirements

| Library | Model Size | GPU Memory | CPU Memory | Dependencies |
|---------|------------|------------|------------|--------------|
| **Resemblyzer** | ~17MB | ~500MB | ~200MB | Minimal |
| **SpeechBrain** | ~50MB | ~1-2GB | ~500MB | Medium |
| **Pyannote** | ~100MB | ~2GB | ~800MB | Heavy |

### Feature Comparison

| Feature | Resemblyzer | SpeechBrain | Pyannote |
|---------|-------------|-------------|----------|
| Speaker Verification | ✅ | ✅ | ✅ |
| Speaker Embedding | ✅ | ✅ | ✅ |
| Multi-Sample Averaging | ✅ | ⚠️ Manual | ⚠️ Manual |
| Real-Time Capable | ✅ | ✅ | ⚠️ Limited |
| GPU Acceleration | ✅ | ✅ | ✅ |
| Pre-trained Models | ✅ (1) | ✅ (Many) | ✅ (Many) |
| Fine-tuning Support | ❌ | ✅ | ✅ |
| Speaker Diarization | ❌ | ✅ | ✅ |
| Sliding Window | ❌ | ⚠️ Manual | ✅ |
| Active Maintenance | ❌ (2020) | ✅ | ✅ |

---

## 5. Threshold Tuning Best Practices

### Understanding Metrics

- **False Acceptance Rate (FAR):** Imposter accepted as genuine speaker
- **False Rejection Rate (FRR):** Genuine speaker rejected
- **Equal Error Rate (EER):** Point where FAR = FRR (lower = better)

### Threshold Selection Strategy

```python
import numpy as np
from sklearn.metrics import roc_curve

def find_optimal_threshold(genuine_scores, impostor_scores,
                          security_priority="balanced"):
    """
    Find optimal threshold based on application requirements

    Args:
        genuine_scores: Similarity scores for same-speaker pairs
        impostor_scores: Similarity scores for different-speaker pairs
        security_priority: "balanced", "security", "convenience"
    """
    # Combine scores and create labels
    scores = np.concatenate([genuine_scores, impostor_scores])
    labels = np.concatenate([
        np.ones(len(genuine_scores)),   # 1 = same speaker
        np.zeros(len(impostor_scores))  # 0 = different speaker
    ])

    # Compute ROC curve
    fpr, tpr, thresholds = roc_curve(labels, scores)
    fnr = 1 - tpr  # False Negative Rate = FRR

    if security_priority == "balanced":
        # EER: Equal Error Rate
        eer_idx = np.nanargmin(np.abs(fnr - fpr))
        optimal_threshold = thresholds[eer_idx]
        eer = fpr[eer_idx]
        print(f"EER: {eer:.4f} at threshold {optimal_threshold:.4f}")

    elif security_priority == "security":
        # Minimize FAR (false acceptance) at cost of higher FRR
        # Target: FAR < 0.1% (very secure)
        target_far = 0.001
        idx = np.where(fpr <= target_far)[0][-1]
        optimal_threshold = thresholds[idx]
        print(f"Security mode: FAR={fpr[idx]:.4f}, FRR={fnr[idx]:.4f}")

    elif security_priority == "convenience":
        # Minimize FRR (false rejection) at cost of higher FAR
        # Target: FRR < 1% (very convenient)
        target_frr = 0.01
        idx = np.where(fnr <= target_frr)[0][0]
        optimal_threshold = thresholds[idx]
        print(f"Convenience mode: FAR={fpr[idx]:.4f}, FRR={fnr[idx]:.4f}")

    return optimal_threshold

# Example usage with Resemblyzer
from resemblyzer import VoiceEncoder, preprocess_wav
from pathlib import Path

encoder = VoiceEncoder()

# Collect genuine pairs (same speaker)
genuine_scores = []
for speaker_dir in Path("train_data/").glob("speaker_*"):
    samples = list(speaker_dir.glob("*.wav"))
    for i in range(len(samples)):
        for j in range(i+1, len(samples)):
            wav1 = preprocess_wav(samples[i])
            wav2 = preprocess_wav(samples[j])
            emb1 = encoder.embed_utterance(wav1)
            emb2 = encoder.embed_utterance(wav2)
            score = np.dot(emb1, emb2)
            genuine_scores.append(score)

# Collect impostor pairs (different speakers)
impostor_scores = []
speaker_dirs = list(Path("train_data/").glob("speaker_*"))
for i in range(len(speaker_dirs)):
    for j in range(i+1, len(speaker_dirs)):
        sample1 = list(speaker_dirs[i].glob("*.wav"))[0]
        sample2 = list(speaker_dirs[j].glob("*.wav"))[0]
        wav1 = preprocess_wav(sample1)
        wav2 = preprocess_wav(sample2)
        emb1 = encoder.embed_utterance(wav1)
        emb2 = encoder.embed_utterance(wav2)
        score = np.dot(emb1, emb2)
        impostor_scores.append(score)

# Find optimal threshold
threshold = find_optimal_threshold(
    np.array(genuine_scores),
    np.array(impostor_scores),
    security_priority="balanced"
)

print(f"Recommended threshold: {threshold:.4f}")
```

### Application-Specific Recommendations

**DJ R3X Use Case: Personalized Greetings**
- **Priority:** Convenience (want to recognize fans, okay with occasional false positive)
- **Recommended:** Lower threshold (~0.65-0.70 for Resemblyzer)
- **Rationale:** Better to greet stranger as "fan" than ignore actual fan

**Security Use Case: Voice Authentication**
- **Priority:** Security (prevent impersonation)
- **Recommended:** Higher threshold (~0.85-0.90 for Resemblyzer)
- **Rationale:** Better to reject genuine user than accept imposter

---

## 6. Recommended Implementation Path for DJ R3X

### Phase 1: MVP (Week 1-2)
**Use Resemblyzer** for rapid prototyping:

```python
# Quick integration steps:
# 1. pip install resemblyzer
# 2. Create SpeakerRecognitionService with Resemblyzer
# 3. Add CLI commands: "enroll speaker", "verify speaker"
# 4. Store embeddings in ./speaker_profiles/
# 5. Test with 3-5 known speakers
```

**Why Resemblyzer first:**
- Simplest API (5 lines to working verification)
- Fastest inference (1000x real-time)
- Smallest dependencies
- Good enough accuracy for initial testing

### Phase 2: Production (Week 3-4)
**Migrate to SpeechBrain ECAPA-TDNN:**

```python
# Production upgrade steps:
# 1. pip install speechbrain
# 2. Replace Resemblyzer encoder with SpeechBrain classifier
# 3. Re-enroll speakers with new model (embeddings incompatible)
# 4. Tune threshold on validation set
# 5. Add batch processing for efficiency
```

**Why SpeechBrain for production:**
- Best accuracy (0.69% EER vs Resemblyzer's unknown)
- Active maintenance (Resemblyzer last updated 2020)
- Production-ready features (batch processing, GPU optimization)
- Fine-tuning support (can adapt to DJ R3X's specific acoustic environment)

### Phase 3: Advanced Features (Optional)
**Add Pyannote for speaker diarization:**

```python
# If you need temporal speaker tracking:
# 1. Use Pyannote sliding window to detect speaker changes
# 2. Combine with SpeechBrain for identification
# 3. Enable "Who's speaking right now?" feature
```

---

## 7. Practical Code Examples

### Complete DJ R3X Integration Example

```python
# cantina_os/services/speaker_recognition_service.py

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from cantina_os.core.event_payloads import BaseEventPayload
from pydantic import BaseModel
from resemblyzer import VoiceEncoder, preprocess_wav
import numpy as np
from pathlib import Path
import json
from datetime import datetime
from typing import Optional

# Event Payloads
class SpeakerEnrollmentPayload(BaseEventPayload):
    speaker_id: str
    speaker_name: str
    audio_files: list[str]

class SpeakerVerificationPayload(BaseEventPayload):
    speaker_id: Optional[str]
    speaker_name: Optional[str]
    similarity_score: float
    verified: bool
    threshold: float

class SpeakerRecognitionService(BaseService):
    """
    Speaker recognition service using Resemblyzer for voice biometrics.

    Features:
    - Speaker enrollment from multiple audio samples
    - Real-time speaker verification
    - Persistent speaker profile storage
    - Threshold-based verification
    """

    def __init__(self, event_bus, config=None):
        super().__init__(service_name="speaker_recognition", event_bus=event_bus)
        self._encoder = None
        self._speaker_profiles = {}
        self._config = config or {}
        self._profiles_dir = Path(self._config.get("profiles_dir", "./speaker_profiles"))
        self._threshold = self._config.get("similarity_threshold", 0.75)

    async def _start(self):
        """Initialize voice encoder and load speaker profiles"""
        self.logger.info("Starting SpeakerRecognitionService...")

        # Initialize encoder
        device = self._config.get("device", "cpu")
        self._encoder = VoiceEncoder(device=device)
        self.logger.info(f"Loaded voice encoder on {device}")

        # Create profiles directory
        self._profiles_dir.mkdir(exist_ok=True)

        # Load existing profiles
        await self._load_speaker_profiles()

        # Subscribe to events
        self._event_bus.on(EventTopics.SPEAKER_ENROLLMENT_REQUEST, self._handle_enrollment)
        self._event_bus.on(EventTopics.AUDIO_VERIFICATION_REQUEST, self._handle_verification)

        self.logger.info(f"Speaker recognition ready with {len(self._speaker_profiles)} profiles")

    async def _load_speaker_profiles(self):
        """Load speaker profiles from disk"""
        metadata_file = self._profiles_dir / "metadata.json"

        if not metadata_file.exists():
            self.logger.info("No existing speaker profiles found")
            return

        with open(metadata_file) as f:
            metadata = json.load(f)

        for speaker_id, info in metadata.items():
            embedding_file = self._profiles_dir / info["embedding_file"]
            if embedding_file.exists():
                embedding = np.load(embedding_file)
                self._speaker_profiles[speaker_id] = {
                    "embedding": embedding,
                    "name": info["name"],
                    "enrolled_date": info["enrolled_date"],
                    "num_samples": info.get("num_samples", 1)
                }
                self.logger.info(f"Loaded profile: {info['name']} ({speaker_id})")

    async def _handle_enrollment(self, payload: SpeakerEnrollmentPayload):
        """Enroll a new speaker from audio samples"""
        speaker_id = payload.speaker_id
        speaker_name = payload.speaker_name
        audio_files = payload.audio_files

        self.logger.info(f"Enrolling speaker: {speaker_name} ({speaker_id}) "
                        f"with {len(audio_files)} samples")

        try:
            # Extract embeddings from all samples
            embeddings = []
            for audio_file in audio_files:
                wav = preprocess_wav(Path(audio_file))
                embedding = self._encoder.embed_utterance(wav)
                embeddings.append(embedding)

            # Average embeddings for robust profile
            speaker_profile = np.mean(embeddings, axis=0)

            # Save to disk
            embedding_file = f"{speaker_id}_embedding.npy"
            np.save(self._profiles_dir / embedding_file, speaker_profile)

            # Update metadata
            metadata_file = self._profiles_dir / "metadata.json"
            metadata = {}
            if metadata_file.exists():
                with open(metadata_file) as f:
                    metadata = json.load(f)

            metadata[speaker_id] = {
                "name": speaker_name,
                "embedding_file": embedding_file,
                "enrolled_date": datetime.now().isoformat(),
                "num_samples": len(audio_files)
            }

            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)

            # Store in memory
            self._speaker_profiles[speaker_id] = {
                "embedding": speaker_profile,
                "name": speaker_name,
                "enrolled_date": metadata[speaker_id]["enrolled_date"],
                "num_samples": len(audio_files)
            }

            self.logger.info(f"Successfully enrolled: {speaker_name}")

            # Emit success event
            self._event_bus.emit(
                EventTopics.SPEAKER_ENROLLED,
                {"speaker_id": speaker_id, "speaker_name": speaker_name}
            )

        except Exception as e:
            self.logger.error(f"Enrollment failed: {e}")
            self._event_bus.emit(
                EventTopics.SPEAKER_ENROLLMENT_FAILED,
                {"speaker_id": speaker_id, "error": str(e)}
            )

    async def _handle_verification(self, payload):
        """Verify speaker from audio data"""
        audio_data = payload.audio_data  # numpy array
        conversation_id = payload.conversation_id

        try:
            # Extract embedding from query audio
            query_embedding = self._encoder.embed_utterance(audio_data)

            # Compare against all known speakers
            best_match = None
            best_similarity = -1.0

            for speaker_id, profile in self._speaker_profiles.items():
                stored_embedding = profile["embedding"]

                # Cosine similarity (embeddings are L2-normed, so dot product works)
                similarity = np.dot(query_embedding, stored_embedding)

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = speaker_id

            # Apply threshold
            verified = best_similarity >= self._threshold if best_match else False

            # Emit verification result
            result = SpeakerVerificationPayload(
                speaker_id=best_match if verified else None,
                speaker_name=self._speaker_profiles[best_match]["name"] if verified else None,
                similarity_score=float(best_similarity),
                verified=verified,
                threshold=self._threshold,
                conversation_id=conversation_id
            )

            self._event_bus.emit(EventTopics.SPEAKER_VERIFIED, result)

            if verified:
                self.logger.info(f"Speaker verified: {result.speaker_name} "
                               f"(score: {best_similarity:.3f})")
            else:
                self.logger.info(f"Speaker unknown (best score: {best_similarity:.3f})")

        except Exception as e:
            self.logger.error(f"Verification failed: {e}")
            self._event_bus.emit(
                EventTopics.SPEAKER_VERIFICATION_FAILED,
                {"error": str(e), "conversation_id": conversation_id}
            )

# CLI Commands
# cantina_os/services/cli_service.py additions:

async def _handle_enroll_speaker(self, args):
    """Enroll a new speaker: enroll speaker <name> <audio_file1> [audio_file2] ..."""
    if len(args) < 2:
        print("Usage: enroll speaker <name> <audio_file1> [audio_file2] ...")
        return

    speaker_name = args[0]
    audio_files = args[1:]

    # Generate speaker ID
    import uuid
    speaker_id = str(uuid.uuid4())

    # Emit enrollment request
    self._event_bus.emit(
        EventTopics.SPEAKER_ENROLLMENT_REQUEST,
        SpeakerEnrollmentPayload(
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            audio_files=audio_files
        )
    )

    print(f"Enrolling speaker '{speaker_name}' with {len(audio_files)} samples...")

async def _handle_list_speakers(self):
    """List all enrolled speakers"""
    metadata_file = Path("./speaker_profiles/metadata.json")

    if not metadata_file.exists():
        print("No speakers enrolled yet")
        return

    with open(metadata_file) as f:
        metadata = json.load(f)

    print(f"\nEnrolled Speakers ({len(metadata)}):")
    print("-" * 60)
    for speaker_id, info in metadata.items():
        print(f"  {info['name']}")
        print(f"    ID: {speaker_id}")
        print(f"    Enrolled: {info['enrolled_date']}")
        print(f"    Samples: {info.get('num_samples', 'unknown')}")
        print()
```

### Event Topics to Add

```python
# cantina_os/core/event_topics.py

class EventTopics(str, Enum):
    # ... existing topics ...

    # Speaker Recognition
    SPEAKER_ENROLLMENT_REQUEST = "speaker/enrollment/request"
    SPEAKER_ENROLLED = "speaker/enrolled"
    SPEAKER_ENROLLMENT_FAILED = "speaker/enrollment/failed"
    AUDIO_VERIFICATION_REQUEST = "speaker/verification/request"
    SPEAKER_VERIFIED = "speaker/verified"
    SPEAKER_VERIFICATION_FAILED = "speaker/verification/failed"
```

---

## 8. Testing & Validation

### Unit Test Example

```python
# cantina_os/tests/unit/test_speaker_recognition.py

import pytest
import numpy as np
from unittest.mock import Mock, patch
from cantina_os.services.speaker_recognition_service import SpeakerRecognitionService
from cantina_os.core.event_bus import EventBus

@pytest.fixture
def mock_event_bus():
    return Mock(spec=EventBus)

@pytest.fixture
def service(mock_event_bus):
    return SpeakerRecognitionService(mock_event_bus, config={"device": "cpu"})

@pytest.mark.asyncio
async def test_service_initialization(service):
    """Test that service initializes correctly"""
    await service.start()
    assert service._encoder is not None
    assert service._speaker_profiles == {}

@pytest.mark.asyncio
async def test_speaker_enrollment(service, mock_event_bus):
    """Test speaker enrollment with mock audio"""
    await service.start()

    # Mock audio data
    mock_audio = np.random.randn(16000 * 3)  # 3 seconds at 16kHz

    with patch('cantina_os.services.speaker_recognition_service.preprocess_wav',
               return_value=mock_audio):
        payload = {
            "speaker_id": "test123",
            "speaker_name": "Test User",
            "audio_files": ["test1.wav", "test2.wav"]
        }

        await service._handle_enrollment(payload)

        # Verify speaker was enrolled
        assert "test123" in service._speaker_profiles
        assert service._speaker_profiles["test123"]["name"] == "Test User"

@pytest.mark.asyncio
async def test_speaker_verification_threshold(service):
    """Test verification threshold logic"""
    await service.start()

    # Create mock embeddings
    enrolled_embedding = np.random.randn(256)
    enrolled_embedding /= np.linalg.norm(enrolled_embedding)  # L2 normalize

    service._speaker_profiles["user1"] = {
        "embedding": enrolled_embedding,
        "name": "User One"
    }

    # Test high similarity (should verify)
    similar_embedding = enrolled_embedding + np.random.randn(256) * 0.1
    similar_embedding /= np.linalg.norm(similar_embedding)

    similarity = np.dot(enrolled_embedding, similar_embedding)
    assert similarity > service._threshold  # Should be high similarity

    # Test low similarity (should reject)
    different_embedding = np.random.randn(256)
    different_embedding /= np.linalg.norm(different_embedding)

    similarity = np.dot(enrolled_embedding, different_embedding)
    assert similarity < service._threshold  # Should be low similarity
```

### End-to-End Test

```python
# cantina_os/tests/integration/test_speaker_e2e.py

import pytest
from cantina_os.main import CantinaOS
from cantina_os.core.event_topics import EventTopics
import numpy as np
import soundfile as sf
from pathlib import Path

@pytest.mark.asyncio
async def test_full_enrollment_verification_flow():
    """Test complete enrollment -> verification flow"""
    # Initialize CantinaOS
    cantina = CantinaOS()
    await cantina.start()

    # Create test audio files
    test_dir = Path("./test_audio")
    test_dir.mkdir(exist_ok=True)

    # Generate synthetic speech-like audio (white noise for testing)
    sample_rate = 16000
    duration = 3  # seconds
    audio1 = np.random.randn(sample_rate * duration) * 0.1
    audio2 = np.random.randn(sample_rate * duration) * 0.1

    sf.write(test_dir / "enroll1.wav", audio1, sample_rate)
    sf.write(test_dir / "enroll2.wav", audio2, sample_rate)

    # Enroll speaker
    enrollment_complete = False

    def on_enrolled(payload):
        nonlocal enrollment_complete
        enrollment_complete = True

    cantina.event_bus.on(EventTopics.SPEAKER_ENROLLED, on_enrolled)

    cantina.event_bus.emit(
        EventTopics.SPEAKER_ENROLLMENT_REQUEST,
        {
            "speaker_id": "testuser",
            "speaker_name": "Test User",
            "audio_files": [
                str(test_dir / "enroll1.wav"),
                str(test_dir / "enroll2.wav")
            ]
        }
    )

    # Wait for enrollment
    import asyncio
    await asyncio.sleep(2)
    assert enrollment_complete

    # Verify speaker
    verification_result = None

    def on_verified(payload):
        nonlocal verification_result
        verification_result = payload

    cantina.event_bus.on(EventTopics.SPEAKER_VERIFIED, on_verified)

    # Use similar audio for verification
    verify_audio = audio1 + np.random.randn(sample_rate * duration) * 0.05

    cantina.event_bus.emit(
        EventTopics.AUDIO_VERIFICATION_REQUEST,
        {
            "audio_data": verify_audio,
            "conversation_id": "test123"
        }
    )

    await asyncio.sleep(1)

    assert verification_result is not None
    assert verification_result.verified == True
    assert verification_result.speaker_name == "Test User"

    # Cleanup
    await cantina.stop()
    import shutil
    shutil.rmtree(test_dir)
```

---

## 9. Resources & References

### Documentation
- **Resemblyzer:** https://github.com/resemble-ai/Resemblyzer
- **SpeechBrain:** https://speechbrain.readthedocs.io/
- **Pyannote.audio:** https://github.com/pyannote/pyannote-audio

### Research Papers
- **Resemblyzer (GE2E):** "Generalized End-to-End Loss for Speaker Verification" (Google, 2018)
- **ECAPA-TDNN:** "ECAPA-TDNN: Emphasized Channel Attention, Propagation and Aggregation in TDNN Based Speaker Verification" (2020)
- **Pyannote:** "Pyannote.audio: Neural Building Blocks for Speaker Diarization" (ICASSP 2020)

### Model Cards
- SpeechBrain ECAPA: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- Pyannote Embedding: https://huggingface.co/pyannote/embedding
- WeSpeaker: https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM

### Community Examples
- Speaker Verification with Qdrant: https://medium.com/@karanshingde/build-an-audio-driven-speaker-recognition-system
- SpeechBrain Tutorials: https://speechbrain.readthedocs.io/en/latest/tutorials/
- Pyannote Notebooks: https://github.com/pyannote/pyannote-audio/tree/develop/tutorials

---

## 10. Conclusion

**For DJ R3X:**

1. **Start with Resemblyzer** (MVP phase)
   - Fastest integration path
   - Good enough accuracy for initial testing
   - Minimal dependencies

2. **Upgrade to SpeechBrain ECAPA-TDNN** (Production phase)
   - Best accuracy (0.69% EER)
   - Production-ready performance
   - Active maintenance

3. **Consider Pyannote** (Advanced features)
   - Only if you need diarization or temporal analysis
   - Research-grade features

**Key Takeaways:**
- All three libraries support local, offline speaker recognition
- Threshold tuning is critical for application-specific performance
- Multi-sample enrollment significantly improves robustness
- Real-time processing is achievable with all three (GPU recommended)
- Start simple, iterate based on actual performance metrics

**Next Steps:**
1. Implement SpeakerRecognitionService with Resemblyzer
2. Add CLI commands for enrollment and testing
3. Collect real-world data from DJ R3X environment
4. Tune threshold on validation set
5. Migrate to SpeechBrain when accuracy requirements increase
