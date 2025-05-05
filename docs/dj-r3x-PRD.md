Product Requirements Document (PRD)

Project: DJ Rex Voice-Only Demo (Mac Desktop MVP)Owner: Brandon CullumDraft Date: May 2, 2025

1. Purpose & Vision

Build a 100% voice-first "mini-assistant" that listens, thinks, and speaks back in a custom DJ Rex-inspired voice. The demo will run entirely on a Mac (no moving hardware) and serve as the foundation for a future full-droid build.

Vision: Create a friendly, slightly quirky Star Wars-themed assistant prototype that can capture live speech, convert it to text, generate a conversational reply, and output that reply through a custom text-to-speech voice.

2. Problem Statement

Current smart assistants lack:

Star Wars personality and customization.

Easy integration with hobbyist Python stacks.

Transferability to robotics platforms.

3. Goals & Success Metrics

Goal

Metric

End-to-end voice conversation works locally

Responds < 3s round-trip for < 15-word query

Uses custom ElevenLabs voice

Synthesized audio is clearly non-generic

Setup time

< 1 hour on clean Mac

4. Scope

4.1 In-Scope (MVP)

CLI interface (python rex_talk.py).

Push-to-talk (press Enter to talk).

Speech-to-text (Google Web Speech API).

Chat response (OpenAI ChatCompletion).

Text-to-speech (generic ElevenLabs voice).

Basic terminal logging.

4.2 Out-of-Scope (Future Phases)

Wake-word activation.

LED mouth bar sync.

Physical interface.

Offline large-model inference.

ROS 2 integration.

5. Personas & User Stories

Persona

User Story

Maker Brandon

"As a builder, I want to install a simple script and verify two-way voice in < 1 hour so I can demo it before work."

Future Fan

"As a Star Wars fan, I want replies in DJ Rex's tone so the experience feels authentic."

6. Functional Requirements

ID

Requirement

Acceptance Criteria

F-1

Capture microphone input on command

Audio capture initiates when Enter is pressed and stops after 1s silence

F-2

Convert speech to text

95%+ accuracy in quiet room

F-3

Generate chat response

OpenAI API returns valid reply

F-4

Synthesize reply with ElevenLabs voice

Audio playback uses ElevenLabs voice

F-5

Round-trip latency

≤ 3s for < 15-word exchange

F-6

Error handling

Descriptive errors, no script crashes

7. Non-Functional Requirements

Category

Requirement

Performance

< 3s end-to-end latency

Reliability

Graceful retry on API timeouts (max 2 retries)

Privacy

Audio not stored unless debugging

Maintainability

Environment keys loaded from .env or shell exports

Portability

Runs on macOS 13+ with Python ≥ 3.11

8. Interaction & UX

CLI Flow (MVP)

$ python rex_talk.py
🎧 Press ENTER to talk → speak into mic
[Silence detected] → "You: <transcript>"
🤖 Rex responds (printed)
🔊 Rex voice plays via system speakers
(loop)

Future Interaction

Wake-word activation.

LED sync.

ROS 2 service integration.

9. Technical Architecture (MVP)

[Mic] → SpeechRecognition (Google STT)
→ OpenAI ChatCompletion (GPT)
→ ElevenLabs TTS (custom voice)
→ Mac speakers

Language: Python 3.11

Packages: speech_recognition, pyaudio, openai, elevenlabs

Config: Environment variables for API keys and voice ID

10. Dependencies & Risks

Risk

Mitigation

Network outage

Fallback error message

API limits

Use free tier cautiously, consider paid tier if needed

Key leakage

Load from environment, never commit keys

11. MVP Timeline (≈ 90 min)

Time

Task

0–15 min

Install dependencies

15–25 min

ElevenLabs voice creation

25–45 min

Write rex_talk.py skeleton

45–60 min

Error handling and logging

60–70 min

Latency testing

70–85 min

Polish persona prompt

85–90 min

Final test run

12. Future Iterations

Wake-word detection.

LED sync for mouth movement.

Offline Whisper for STT.

Local GPT inference.

ROS 2 node integration.

Intent-to-action hooks.

13. Web Interface (Frontend)

13.1 Confirmed Design Outline

Interaction:
- Push to talk button → starts listening.
- Auto playback of Rex reply (no user click required).
- Text transcript: both user speech and Rex response shown.

UI:
- Star Wars / Cantina-themed → using Tailwind for base + custom styling.
- Star Wars-style speaker panel animation during Rex reply playback.

User:
- Single-user.
- Local run only (no auth or backend user management yet).

Tech:
- React / NextJS
- TailwindUI / Shadcn for base components.
- ElevenLabs audio playback.
- Web Speech API for STT via backend.

13.2 Architecture

[User Voice] → (Push-to-Talk Button) → Microphone Access (Web Audio API)
              → Stream to local Python backend (current setup)
              → STT → GPT → ElevenLabs Audio
              → Send audio + transcript back to frontend
              → Auto playback + text display
              → Visualizer (speaker panel animation)

13.3 Frontend Component Map

Component | Role
--------- | ----
MicButton | Starts/stops audio capture
TranscriptDisplay | Shows user speech + Rex reply
AudioPlayer | Auto-plays response audio
VisualizerPanel | Star Wars style animated speaker graphic
MainLayout | Arranges components, handles push-to-talk logic

13.4 Data Flow (Frontend)

1. User clicks "Talk"
2. Browser captures mic audio
3. Audio sent to local backend
4. Backend returns transcript + audio
5. Frontend displays text + plays audio
6. Visualizer animates while audio plays

13.5 Technical Implementation

Task | Implementation
---- | -------------
Audio capture | getUserMedia API
Audio transfer | REST POST with FormData WAV blob
Audio playback | <audio> tag or Howler.js
Visualizer | CSS animation or Canvas API tied to AudioContext
API calls | fetch or Axios
Browser compatibility | Desktop Chrome only

Final Note

STT: Google Web Speech chosen for MVP simplicity and speed.TTS: ElevenLabs generic voice for now.Push-to-talk: Confirmed as acceptable for MVP.No hardware integration in this version.

Ready to proceed to script creation.

