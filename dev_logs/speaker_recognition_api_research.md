# Cloud-Based Speaker Recognition API Research Report
**Date**: November 14, 2025
**Purpose**: Evaluate cloud-based voice biometric and speaker recognition APIs for real-time voice assistant integration

---

## Executive Summary

This report analyzes cloud-based speaker recognition solutions suitable for integration into real-time voice assistant systems like DJ R3X. The research reveals a critical distinction between **speaker diarization** (identifying "who spoke when" within a session) and **speaker identification/verification** (recognizing specific individuals across sessions using biometric voiceprints).

### Key Findings:

1. **Azure Speaker Recognition API** - RETIRED (September 30, 2025) - no longer viable
2. **AWS Amazon Transcribe** - Provides speaker diarization only, NOT persistent speaker identification
3. **Google Cloud Speech-to-Text** - Provides speaker diarization only, NOT persistent speaker identification
4. **Alternative Solutions** - Amazon Connect Voice ID, Picovoice Eagle, third-party services required for true speaker biometrics

---

## 1. Azure Cognitive Services Speaker Recognition API

### Status: RETIRED (September 30, 2025)

**CRITICAL**: This service is no longer available. Microsoft retired the Speaker Recognition API on September 30, 2025, and applications can no longer access the API endpoints.

### Previous Capabilities (Historical Reference Only):

#### Speaker Verification
- **Text-dependent**: Required specific passphrase during enrollment and verification
- **Text-independent**: Free-form speech for enrollment and verification
- **Use Cases**: Customer identity verification in call centers, contactless facility access

#### Speaker Identification
- Identified unknown speaker from a group of enrolled speakers (up to 50 per request)
- 1:N matching against enrolled voice profiles
- **Use Cases**: Attribution of speech to individuals, forensic analysis

### How It Worked:
1. **Enrollment Phase**: Collected voice samples to create unique voice signature
2. **Verification/Identification Phase**: Compared new audio against stored voice signatures
3. **Cross-Session Persistence**: YES - voice profiles were stored in Azure tenancy and could be reused across sessions

### Migration Path:

Organizations previously using Azure Speaker Recognition must migrate to alternatives:
- Amazon Connect Voice ID
- Google SpeakerID
- Phonexia Voice Verify
- IDVoice
- Sensory
- Open-source: pyannote, SpeechBrain, WeSpeaker, Kaldi, Nvidia NeMo

### Verdict for DJ R3X: NOT AVAILABLE

---

## 2. AWS Amazon Transcribe with Speaker Diarization

### Core Capability: Speaker Diarization (NOT Speaker Identification)

**CRITICAL DISTINCTION**: Amazon Transcribe provides speaker diarization, which separates different voices within an audio session but does NOT provide persistent speaker identification across sessions.

### How It Works:

#### Speaker Diarization Process:
1. **Audio Input**: Real-time streaming (HTTP/2, WebSocket) or batch processing
2. **Speaker Segmentation**: Identifies distinct voices and assigns temporary labels (spk_0, spk_1, etc.)
3. **Output**: Transcription with speaker labels for each utterance

#### Technical Specifications:
- **Maximum Speakers**: Up to 30 unique speakers per session
- **Optimal Range**: 2-5 speakers for best accuracy
- **Label Format**: spk_0 through spk_9 (labels are session-specific only)

### Real-Time vs Batch Processing:

#### Real-Time Streaming:
- **Protocols**: HTTP/2 or WebSocket
- **Configuration**: Set `show-speaker-label=true` parameter
- **Latency**: Near real-time with progressive transcription
- **Speaker Labels**: Only appear on fully-transcribed segments (not partial results)

#### Batch Processing:
- **Configuration**: `ShowSpeakerLabels=true` and `MaxSpeakerLabels` parameter
- **Processing Time**: Minutes to hours depending on audio length
- **Accuracy**: Generally higher than streaming due to full context

### API Integration:

```python
# Example streaming configuration
response = transcribe_client.start_stream_transcription(
    LanguageCode='en-US',
    MediaSampleRateHertz=16000,
    MediaEncoding='pcm',
    ShowSpeakerLabel=true,
    MaxSpeakerLabels=5
)
```

### Output Structure:

```json
{
  "speaker_labels": {
    "speakers": 2,
    "segments": [
      {
        "start_time": "0.0",
        "speaker_label": "spk_0",
        "end_time": "3.5",
        "items": [
          {"start_time": "0.0", "speaker_label": "spk_0", "end_time": "0.5"}
        ]
      }
    ]
  }
}
```

### Cross-Session Persistence:

**NO** - Speaker IDs do NOT persist across sessions. Each transcription job assigns new arbitrary labels (spk_0, spk_1) with no connection to previous sessions. If the same person speaks in two different audio files, they will receive different random speaker labels.

### Pricing (2025):

- **Standard Rate**: ~$0.00017/second (~$0.0102/minute)
- **Free Tier**: 60 minutes/month for first 12 months (new AWS customers)
- **Billing Increments**: 1-second increments, 15-second minimum per request
- **Speaker Diarization Surcharge**: Additional cost (exact amount not publicly disclosed in standard pricing)
- **Volume Discounts**: Available for large workloads (contact AWS)

### Accuracy and Requirements:

- **Audio Quality**: Higher quality audio produces better speaker separation
- **Speaker Count**: Accuracy decreases beyond 5 speakers
- **Background Noise**: Can negatively impact diarization accuracy
- **Overlapping Speech**: May struggle with simultaneous speakers
- **No Published Accuracy Metrics**: AWS does not publish specific accuracy percentages

### Implementation Complexity:

**Moderate** - Integration requires:
1. AWS SDK setup and credentials
2. Audio streaming pipeline (for real-time)
3. WebSocket or HTTP/2 connection management
4. Response parsing and speaker label extraction
5. No voice enrollment needed (this is diarization, not identification)

### Use Cases:

- Meeting transcription (identifying different speakers in a conversation)
- Podcast transcription (separating host and guests)
- Call center analytics (distinguishing agent vs customer)
- **NOT SUITABLE**: User authentication, persistent speaker recognition across sessions

### Verdict for DJ R3X:

**NOT SUITABLE** for persistent speaker identification. Could be useful for identifying multiple people in a single conversation (e.g., "User A vs User B in one interaction"), but cannot recognize "oh, User A is back again" across multiple sessions.

---

## 3. Google Cloud Speech-to-Text with Speaker Diarization

### Core Capability: Speaker Diarization (NOT Speaker Identification)

Similar to AWS Transcribe, Google Cloud Speech-to-Text provides speaker diarization within sessions but NOT persistent speaker identification across sessions.

### How It Works:

#### Speaker Diarization Process:
1. **Configuration**: Enable `diarization_config` with `min_speaker_count` and `max_speaker_count`
2. **Processing**: Distinguishes different voices and assigns numerical labels (Speaker 1, Speaker 2, etc.)
3. **Output**: Each word tagged with a speaker label number

#### Technical Specifications:
- **Speaker Labels**: Session-specific numerical assignments
- **Count Requirement**: Must specify expected min/max speaker count
- **Language Support**: Check language support page (not all languages supported)
- **Maximum Speakers**: No hard limit stated, but "as many as Speech-to-Text can uniquely identify"

### Real-Time vs Batch Processing:

#### Streaming Recognition:
- **Cumulative Results**: Each result includes words from previous results (final output contains complete diarized content)
- **Configuration**: Same `diarization_config` as batch processing
- **Latency**: Real-time with progressive refinement

#### Batch Recognition:
- **Methods**: `speech:recognize` (synchronous) or `speech:longrunningrecognize` (asynchronous)
- **Configuration**: Identical to streaming
- **Processing**: Slightly better accuracy due to full context

### API Integration:

```python
# Example configuration
from google.cloud import speech

config = speech.RecognitionConfig(
    encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
    sample_rate_hertz=16000,
    language_code="en-US",
    enable_speaker_diarization=True,
    diarization_speaker_count=2,  # Or use min/max
)
```

### Output Structure:

```json
{
  "results": [
    {
      "alternatives": [
        {
          "words": [
            {
              "word": "Hello",
              "speakerTag": 1,
              "startTime": "0s",
              "endTime": "0.5s"
            }
          ]
        }
      ]
    }
  ]
}
```

### Cross-Session Persistence:

**NO** - Speaker labels are session-specific only. Documentation does not mention any cross-session identity persistence. Speaker tags are regenerated for each transcription request.

### Pricing (2025):

#### Standard Transcription:
- **Standard Processing**: ~$0.016/minute (results in 1-2x real-time)
- **Dynamic Batch**: ~$0.004/minute (75% discount, results within 24 hours)
- **Free Tier**: 60 minutes/month for new users

#### Speaker Diarization Surcharge:
- **Additional Cost**: $0.006 per 15 seconds
- **Calculation**: ~$0.024/minute additional for diarization

#### Total Cost Example (with diarization):
- Standard processing: $0.016/min + $0.024/min = $0.040/min
- 1 hour of audio: ~$2.40

#### Additional GCP Infrastructure Costs:
- Cloud Storage: ~$2-5/month
- Cloud Functions: ~$3-8/month
- Pub/Sub: ~$9-15/month
- Egress: ~$5-20/month
- Logging: ~$2-5/month
- **Total infrastructure overhead**: ~$21-53/month

### Accuracy and Requirements:

- **No Published Metrics**: Google does not provide specific accuracy percentages
- **Best Practices**: Accurately specify expected speaker count for better results
- **Language Limitations**: Feature availability varies by language
- **Audio Quality**: Higher quality improves diarization accuracy

### Implementation Complexity:

**Moderate** - Integration requires:
1. Google Cloud project setup and authentication
2. Speech-to-Text API enablement
3. Audio streaming pipeline configuration
4. Speaker tag parsing from results
5. Infrastructure costs for supporting services

### Use Cases:

- Meeting transcription with multiple participants
- Interview transcription
- Multi-speaker audio analysis
- **NOT SUITABLE**: User authentication, persistent speaker recognition

### Verdict for DJ R3X:

**NOT SUITABLE** for persistent speaker identification. Same limitations as AWS Transcribe - useful only for separating speakers within a single session, not for recognizing returning users.

---

## 4. Alternative Solutions for TRUE Speaker Identification

Since the three services researched do not provide persistent cross-session speaker identification, here are viable alternatives:

### A. Amazon Connect Voice ID

**Status**: Active (AWS service, separate from Transcribe)

#### Capabilities:
- **Speaker Enrollment**: Creates persistent voice profiles (voiceprints)
- **Speaker Verification**: 1:1 matching (is this Person A?)
- **Speaker Identification**: 1:N matching (who is this among known speakers?)
- **Fraud Detection**: Identifies known fraudsters
- **Real-time**: Low-latency streaming authentication

#### Limitations:
- **Tightly Coupled to Amazon Connect**: Designed for contact center use cases
- **Standalone API**: Unclear if available outside Amazon Connect environment
- **Documentation Access**: Limited public documentation found during research

#### Pricing:
- Not clearly documented in public pricing pages
- Likely charges per enrollment, verification transaction
- Contact AWS for detailed pricing

#### Use Case Fit for DJ R3X:
- **Potentially Suitable** IF it can be used standalone (requires further investigation)
- Would enable "Welcome back, [User Name]" functionality
- Need to verify API availability outside contact center context

---

### B. Picovoice Eagle Speaker Recognition

**Status**: Active (on-device + cloud options)

#### Capabilities:
- **Text-independent**: No specific phrases required
- **Language-agnostic**: Works across languages
- **Real-time Streaming**: Compares incoming audio frames to voiceprints in real-time
- **Seamless Enrollment**: Just a few seconds of speech required
- **Cross-platform**: Works on mobile, server, embedded systems
- **Persistent Profiles**: Eagle Profile objects can be stored and reused

#### How It Works:
1. **Enrollment**: Analyzes utterances to create Eagle Profile (voiceprint)
2. **Recognition**: Compares incoming audio frames to enrolled profiles in real-time
3. **Similarity Scores**: Returns match confidence for each enrolled speaker

#### Pricing:
- **Free Tier**: Available for non-commercial personal projects
- **Free Trial**: For enterprise evaluation
- **Paid Plans**: Required for commercial use (specific pricing not disclosed publicly)
- **Usage-based**: Some engines charge per character of processed text

#### Advantages:
- On-device processing eliminates network latency
- No cloud API dependency (privacy-friendly)
- Fast, resource-efficient
- Cross-session persistence built-in

#### Implementation Complexity:
- **Low to Moderate**
- SDKs available for Python, Node.js, Web, and other platforms
- Well-documented API
- Example enrollment in seconds

#### Use Case Fit for DJ R3X:
- **HIGHLY SUITABLE** for persistent speaker identification
- Enables true "Welcome back" personalization
- Low latency for real-time voice assistant
- Can run on-device or server-side

---

### C. Third-Party Voice Biometric Services

Several commercial services offer cross-session speaker identification:

#### Phonexia Voice Verify
- **Enrollment**: 20 seconds of speech
- **Verification**: Few seconds of audio
- **Accuracy**: Enterprise-grade
- **Integration**: REST API

#### ID R&D (IDVoice)
- **Cross-channel**: Works across devices and applications
- **No retraining**: Profiles work across integrations
- **REST API**: Cloud-based or on-premise
- **Deployment**: Mobile, server, cloud, embedded

#### Features Common to Enterprise Solutions:
- Persistent voice profiles across sessions
- Text-independent verification
- Anti-spoofing protection
- Liveness detection
- High accuracy (typically >95%)

#### Pricing:
- Typically enterprise-only (contact sales)
- Usage-based or seat-based licensing
- Often not publicly disclosed

#### Implementation Complexity:
- **Moderate to High**
- Requires API integration and key management
- Voice profile storage and management
- Security considerations for biometric data

---

## 5. Comparative Analysis

| Service | Diarization | Cross-Session ID | Real-Time | Pricing | Complexity | DJ R3X Fit |
|---------|-------------|------------------|-----------|---------|------------|------------|
| **Azure Speaker Recognition** | No | YES (retired) | Yes | N/A | Moderate | NOT AVAILABLE |
| **AWS Transcribe** | YES | NO | Yes | ~$0.01/min+ | Moderate | NOT SUITABLE |
| **Google Speech-to-Text** | YES | NO | Yes | ~$0.04/min | Moderate | NOT SUITABLE |
| **Amazon Connect Voice ID** | No | YES | Yes | Unknown | Unknown | MAYBE (needs research) |
| **Picovoice Eagle** | No | YES | Yes | Free/Paid | Low-Moderate | EXCELLENT |
| **Phonexia/ID R&D** | No | YES | Yes | Enterprise | Moderate-High | GOOD (if budget allows) |

---

## 6. Recommendations for DJ R3X Integration

### For Persistent Speaker Identification (Recognizing Returning Users):

**Primary Recommendation: Picovoice Eagle**

**Reasons:**
1. Explicitly designed for cross-session speaker identification
2. Real-time streaming support (perfect for voice assistant)
3. Free tier for testing/personal projects
4. On-device processing eliminates cloud latency and privacy concerns
5. Seamless enrollment (seconds of audio)
6. Well-documented SDK with Python support
7. Can integrate with existing CantinaOS event-driven architecture

**Implementation Approach:**
```python
# Conceptual integration with CantinaOS
class EagleService(BaseService):
    async def _start(self):
        # Initialize Eagle with enrolled speaker profiles
        # Listen to TRANSCRIPTION_FINAL events
        # Compare voice to known profiles in real-time
        # Emit SPEAKER_IDENTIFIED event with user ID
        self._event_bus.on(EventTopics.TRANSCRIPTION_FINAL, self._identify_speaker)

    async def _identify_speaker(self, payload):
        # Compare audio against enrolled profiles
        # Emit speaker ID if match found
        if match_found:
            self._event_bus.emit(
                EventTopics.SPEAKER_IDENTIFIED,
                SpeakerIdentifiedPayload(
                    speaker_id="user_123",
                    confidence=0.95
                )
            )
```

### For Within-Session Speaker Separation (Multiple People in One Conversation):

**Recommendation: Deepgram (already integrated) + Diarization Feature**

DJ R3X already uses Deepgram for transcription. Deepgram supports speaker diarization which could be enabled for multi-person interactions.

**Implementation:**
- Enable diarization in DeepgramDirectMicService
- Emit separate transcription events per speaker
- Use for scenarios like "DJ R3X talking with a group of people"

### Hybrid Approach (Best of Both Worlds):

1. **Eagle** for persistent speaker identification ("Welcome back, Brandon!")
2. **Deepgram diarization** for separating multiple speakers in one session
3. **MemoryService** integration to store speaker profiles and preferences

---

## 7. Technical Implementation Considerations

### Voice Profile Management:
- **Storage**: Store Eagle profiles in MemoryService or separate database
- **Enrollment Flow**: Dedicated enrollment mode or passive enrollment during first interactions
- **Privacy**: Biometric data requires secure storage and compliance considerations

### Event-Driven Integration:
```
TRANSCRIPTION_FINAL
    ↓
EagleService
    ↓ (if match found)
SPEAKER_IDENTIFIED
    ↓
GPTService / BrainService
    ↓ (personalized response)
LLM_RESPONSE_TEXT ("Welcome back, Brandon! Ready for some tunes?")
```

### Enrollment UX:
- "I don't recognize your voice. What's your name?"
- Collect 10-15 seconds of natural speech during initial interaction
- Store profile with user preferences (favorite music, mood preferences)

### Security Considerations:
- Encrypt voice profiles at rest
- Consider GDPR/privacy implications of biometric storage
- Provide easy profile deletion ("forget my voice")

---

## 8. Cost Analysis for DJ R3X Use Case

### Scenario: Personal home installation (non-commercial)

**Picovoice Eagle:**
- **Cost**: $0 (free tier for personal projects)
- **Processing**: On-device (no per-use charges)
- **Storage**: Minimal (profiles are small binary files)

**Winner**: Picovoice Eagle (free + perfect for use case)

### Scenario: Commercial deployment (multiple installations)

**Picovoice Eagle (Paid):**
- Contact for enterprise pricing
- Likely usage-based or per-device licensing

**Deepgram Diarization (for comparison):**
- Already paying for Deepgram transcription
- Diarization adds minimal cost per minute

**Recommendation**: Still Picovoice Eagle for speaker ID, supplement with Deepgram diarization if multi-speaker support needed

---

## 9. Next Steps for Implementation

### Phase 1: Research & Testing (Week 1-2)
1. Sign up for Picovoice Eagle free trial
2. Test enrollment process with sample audio
3. Evaluate recognition accuracy with different speakers
4. Measure latency in real-time scenario

### Phase 2: Integration (Week 3-4)
1. Create EagleService extending BaseService
2. Implement enrollment flow via CLI or voice command
3. Store profiles in MemoryService
4. Emit SPEAKER_IDENTIFIED events

### Phase 3: Personalization (Week 5-6)
1. Link speaker IDs to user preferences in MemoryService
2. Update GPTService to use speaker context in prompts
3. Implement "Welcome back" greetings
4. Store music preferences per speaker

### Phase 4: Testing & Refinement (Week 7-8)
1. Test with multiple users
2. Evaluate enrollment UX
3. Tune confidence thresholds
4. Add fallback for unknown speakers

---

## 10. Conclusion

### Key Findings:

1. **Azure Speaker Recognition is RETIRED** - no longer an option
2. **AWS Transcribe and Google Speech-to-Text provide diarization ONLY** - they separate speakers within a session but cannot recognize individuals across sessions
3. **True speaker identification requires dedicated biometric services** like Amazon Connect Voice ID, Picovoice Eagle, or enterprise solutions

### Best Choice for DJ R3X:

**Picovoice Eagle Speaker Recognition**
- Cross-session speaker identification
- Real-time performance
- Free for personal use
- Easy integration with existing CantinaOS architecture
- Privacy-friendly on-device processing

### Implementation Priority:

**HIGH** - Speaker identification would significantly enhance DJ R3X's personalization capabilities:
- "Welcome back, Brandon! Want to continue the 90s hip-hop playlist?"
- "Hey Sarah! Ready for some jazz tonight?"
- Remembering individual music preferences
- Personalized conversation history

This feature aligns perfectly with DJ R3X's goal of creating an engaging, personalized DJ experience.

---

**End of Report**
