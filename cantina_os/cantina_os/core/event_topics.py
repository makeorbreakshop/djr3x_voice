"""Event topics for CantinaOS."""

from enum import Enum

class EventTopics(str, Enum):
    """Event topics used throughout the system."""
    
    # System events
    SERVICE_STATUS = "service.status"
    SYSTEM_MODE_CHANGE = "system.mode.change"
    SYSTEM_MODE_CHANGED = "system.mode.changed" # Event emitted *after* mode change is complete
    MODE_TRANSITION_STARTED = "mode.transition.started"
    MODE_TRANSITION_COMPLETE = "mode.transition.complete"
    LOG_MESSAGE = "log.message"
    PERFORMANCE_METRIC = "performance.metric"
    DEBUG_COMMAND = "debug.command"
    DEBUG_CONFIG = "debug.config"

    # Input events
    AUDIO_CHUNK = "audio.chunk"
    TRANSCRIPTION_TEXT = "transcription.text"
    TRANSCRIPTION_FINAL = "transcription.final"
    VOICE_LISTENING_STARTED = "voice.listening.started"
    VOICE_LISTENING_STOPPED = "voice.listening.stopped"
    INTENT_DETECTED = "intent.detected"
    CLI_COMMAND = "cli.command"
    CLI_STATUS_REQUEST = "cli.status.request"
    TOOL_REGISTRATION = "tool.registration"
    TOOL_EXECUTION_REQUEST = "tool.execution.request"
    TOOL_EXECUTION_RESULT = "tool.execution.result"
    COMMAND_RESPONSE = "command.response"
    SENTIMENT_ANALYSIS = "sentiment.analysis"
    COMMAND_CALL = "command.call"
    COMMAND_RESULT = "command.result"

    # Output events
    SPEECH_SYNTHESIS = "speech.synthesis"
    SPEECH_GENERATION_REQUEST = "speech.generation.request"
    SPEECH_GENERATION_COMPLETE = "speech.generation.complete"
    SPEECH_AMPLITUDE = "speech.amplitude"
    LLM_RESPONSE = "llm.response"
    EYE_COMMAND = "eye.command"
    MUSIC_COMMAND = "music.command"
    MODE_CHANGED = "mode.changed"
    MODE_TRANSITION = "mode.transition"
    PLAN_READY = "plan.ready"
    PLAN_STARTED = "plan.started"
    STEP_READY = "step.ready"
    STEP_EXECUTED = "step.executed"
    PLAN_ENDED = "plan.ended"
    MEMORY_UPDATED = "memory.updated"

    # MemoryService events
    MEMORY_GET = "memory.get"
    MEMORY_SET = "memory.set"

    # MusicController events
    MUSIC_LIBRARY_UPDATED = "music.library.updated" # Emitted when the music library is loaded or changes
    TRACK_PLAYING = "track.playing"
    TRACK_PAUSED = "track.paused"
    TRACK_STOPPED = "track.stopped"
    TRACK_ENDED = "track.ended"
    TRACK_SEEK = "track.seek"
    TRACK_VOLUME_CHANGED = "track.volume.changed"
    CROSSFADE_STARTED = "crossfade.started"
    CROSSFADE_COMPLETE = "crossfade.complete"
    TRACK_METADATA_REQUEST = "track.metadata.request"
    TRACK_METADATA_RESPONSE = "track.metadata.response"
    TRACK_ENDING_SOON = "track.ending.soon" # Emitted by MusicController when a track is nearing its end

    # CachedSpeechService events
    SPEECH_CACHE_REQUEST = "speech.cache.request"
    SPEECH_CACHE_READY = "speech.cache.ready"
    SPEECH_CACHE_ERROR = "speech.cache.error"
    SPEECH_CACHE_MISS = "speech.cache.miss"
    SPEECH_CACHE_PLAYBACK_REQUEST = "speech.cache.playback.request"
    SPEECH_CACHE_PLAYBACK_STARTED = "speech.cache.playback.started"
    SPEECH_CACHE_PLAYBACK_COMPLETED = "speech.cache.playback.completed"
    SPEECH_CACHE_UPDATED = "speech.cache.updated"
    SPEECH_CACHE_HIT = "speech.cache.hit"
    SPEECH_CACHE_CLEARED = "speech.cache.cleared"
    SPEECH_CACHE_CLEANUP = "speech.cache.cleanup" # For triggering manual cleanup

    # DJ Mode events
    DJ_COMMAND = "dj.command" # Incoming CLI/voice commands for DJ mode (e.g., 'dj start', 'dj stop')
    DJ_MODE_START = "dj.mode.start" # Emitted when DJ mode successfully starts
    DJ_MODE_STOP = "dj.mode.stop"   # Emitted when DJ mode successfully stops
    DJ_NEXT_TRACK_SELECTED = "dj.next_track.selected" # Emitted by BrainService when the next track is chosen
    DJ_COMMENTARY_REQUEST = "dj.commentary.request" # Emitted by BrainService to request GPT commentary
    GPT_COMMENTARY_RESPONSE = "gpt.commentary.response" # Emitted by GPTService with generated commentary

    # DJ Mode events related to queue
    DJ_TRACK_QUEUED = "dj.track.queued"

    # ElevenLabsService Events
    TTS_REQUEST = "tts.request" # Request text-to-speech
    TTS_AUDIO_DATA = "tts.audio.data" # Provide generated audio data

    # Voice processing events
    VOICE_TRANSCRIPTION_INTERIM = "voice.transcription.interim"

    # Speech Cache events
    CLEAR_SPEECH_CACHE = "speech.cache.clear"

    # TTS events