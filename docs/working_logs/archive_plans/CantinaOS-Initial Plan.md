CantinaOS — Full System Architecture & Implementation Plan

📖 Overview

CantinaOS is the upgraded architecture for DJ R3X, designed to replace the previous bus-based system with a scalable, modular, Python-native framework inspired by ROS2 concepts. This document outlines all components, services, data flow, session memory strategy, and MVP vs. future features. It is intended for direct implementation and reference by Cursor or any developer working on the project.

🧠 Core Design Principles

Modular: Independent services for each function.

Agentic: GPT-4o integrated with tool call capabilities.

Session memory: Maintained inside GPT Service.

Expressive: Sentiment-driven eye animations synced with speech.

Future-proof: Designed to accommodate upgrades like long-term memory, vector memory, head movement, and web interface.

🔎 System Services

Input Layer

Mic Input Service

Captures audio from microphone.

Sends raw audio stream over Redis Pub/Sub or ZeroMQ.

Transcription Service (Deepgram/Whisper)

Listens to audio stream.

Publishes transcription_text events/messages.

GPT & Memory Layer

GPT Service

Listens to transcription_text.

Maintains session history (messages list).

Sends full history to GPT-4o.

Analyzes sentiment (VADER or similar).

Publishes:

response_text.

sentiment.

command_calls (JSON for tool calls).

Tool calls:

play_song → triggers Music Controller.

web_search → executed internally.

memory_save / memory_recall → future.

Output & Expression Layer

ElevenLabs Service

Listens to response_text.

Sends to ElevenLabs API (custom DJ R3X voice).

Publishes:

speech_start event.

speech_end event.

speech_amplitude.

Eye Light Controller Service

Listens to:

current_mode.

sentiment.

speech_start / speech_end.

speech_amplitude.

response_text (for future advanced effects).

Sends JSON commands to Arduino Bridge.

Music Controller Service

Listens to command_calls and current_mode.

Publishes music_song_started and music_song_ended.

Arduino Bridge Service

Converts events/messages to JSON-over-serial.

Communicates with Arduino for LED (eyes) control and future head movement.

Mode & System Control Layer

Yoda Mode Manager Service

Publishes current_mode.

Listens to set_mode.

Publishes audio_duck for music volume control.

CLI Service

Accepts plain English commands.

Publishes:

set_mode.

command_calls.

Web queries routed to GPT Service.

📡 Message/Event Bus

All services communicate over Redis Pub/Sub, ZeroMQ, or simple Python queues.

Message/Event

Purpose

transcription_text

Voice transcription text.

response_text

GPT reply text.

sentiment

Sentiment label (positive, neutral, negative).

command_calls

JSON tool requests.

speech_start

TTS speaking start.

speech_end

TTS speaking end.

speech_amplitude

Audio amplitude for brightness pulsing.

current_mode

Current system mode.

set_mode

Mode change requests.

eye_light_command

Eye color/brightness/animation.

music_play

Song play requests.

📝 Session History (Short-Term Memory)

Maintained inside GPT Service as a messages list.

Full history sent with each GPT-4o request.

Older turns trimmed or summarized if token limits are exceeded.

🔮 Future Memory Expansion

Type

Description

Long-term memory

Save facts/preferences to persist across sessions.

Vector memory

Store conversation summaries/facts as embeddings for efficient recall.

🖌 Eye Light Behavior Flow

GPT response → sentiment event published.

ElevenLabs speech starts → speech_start event.

Eye Light Controller:

Sets color/animation based on sentiment.

Starts brightness pulsing based on speech_amplitude.

ElevenLabs speech ends → speech_end event.

Eyes return to idle/ambient behavior.

Word-level color changes possible with ElevenLabs SSML timestamps in a future phase.

🚀 Build Order (MVP Priority)

Mic Input Service + Transcription Service.

GPT Service (session history, sentiment analysis, tool call handling).

ElevenLabs Service.

Eye Light Controller Service + Arduino Bridge.

Music Controller Service.

Yoda Mode Manager Service + CLI Service.

Sentiment → Eye light behavior mapping.

Speech amplitude-driven brightness pulsing.

✅ Final Summary

CantinaOS will provide a robust, modular framework for DJ R3X with scalable short-term memory, agentic tool call execution, real-time sentiment-driven expressiveness, and a clean architecture ready for future web interface and long-term memory integration. The design retains best practices from robotics, AI agent development, and expressive animatronics — without being tied to ROS.