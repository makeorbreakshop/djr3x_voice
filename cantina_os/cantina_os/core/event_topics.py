"""Event topics for CantinaOS."""

from enum import Enum


class EventTopics(str, Enum):
    """Event topics used throughout the system."""

    # System events
    SERVICE_STATUS = "service.status"
    SERVICE_STATUS_UPDATE = (
        "service.status.update"  # Added back - needed by MusicController
    )
    SERVICE_STATUS_REQUEST = "service.status.request"  # Request for all services to emit their current status
    SERVICE_ERROR = "service.error"  # Added back
    SERVICE_READY = (
        "service.ready"  # Service has fully started and is ready to handle requests
    )
    SERVICE_STARTING = "service.starting"  # Service is in the process of starting up
    SYSTEM_STARTUP = "system.startup"  # Added back - needed by main.py
    SYSTEM_SHUTDOWN = "system.shutdown"  # Added back
    SYSTEM_MODE_CHANGE = "system.mode.change"
    SYSTEM_MODE_CHANGED = (
        "system.mode.changed"  # Event emitted *after* mode change is complete
    )
    SYSTEM_SHUTDOWN_REQUESTED = "system.shutdown.requested"
    SYSTEM_SET_MODE_REQUEST = "system.set.mode.request"
    SYSTEM_ERROR = "system.error"  # Added back
    MODE_TRANSITION_STARTED = "mode.transition.started"
    MODE_TRANSITION_COMPLETE = "mode.transition.complete"
    MODE_TRANSITION_COMPLETED = "mode.transition.completed"  # Added back
    MODE_TRANSITION_FAILED = "mode.transition.failed"  # Added back
    LOG_MESSAGE = "log.message"
    PERFORMANCE_METRIC = "performance.metric"
    DEBUG_COMMAND = "debug.command"
    DEBUG_CONFIG = "debug.config"
    DEBUG_SET_GLOBAL_LEVEL = "debug.set.global.level"
    DEBUG_LOG = "debug.log"  # Added back - needed by debug service
    DEBUG_COMMAND_TRACE = "debug.command.trace"  # Added back - needed by debug service
    DEBUG_PERFORMANCE = "debug.performance"  # Added back - needed by debug service
    DEBUG_STATE_TRANSITION = "debug.state.transition"  # Added back

    # Input events
    AUDIO_CHUNK = "audio.chunk"
    AUDIO_RAW_CHUNK = "audio.raw.chunk"  # Added back
    TRANSCRIPTION_TEXT = "transcription.text"
    TRANSCRIPTION_FINAL = "transcription.final"
    TRANSCRIPTION_INTERIM = "transcription.interim"  # Added back
    TRANSCRIPTION_ERROR = "transcription.error"  # Added back
    TRANSCRIPTION_METRICS = (
        "transcription.metrics"  # Added back - needed by deepgram_direct_mic
    )
    VOICE_LISTENING_STARTED = "voice.listening.started"
    VOICE_LISTENING_STOPPED = "voice.listening.stopped"
    VOICE_TRANSCRIPTION_INTERIM = "voice.transcription.interim"
    VOICE_TRANSCRIPTION_FINAL = "voice.transcription.final"
    VOICE_PROCESSING_STARTED = "voice.processing.started"  # Added back
    VOICE_PROCESSING_COMPLETE = "voice.processing.complete"  # Added back
    VOICE_ERROR = "voice.error"  # Added back
    VOICE_AUDIO_RECEIVED = "voice.audio.received"  # Added back
    VOICE_AUDIO_LEVEL = "voice.audio.level"  # Added back
    VOICE_INPUT_CLEANED_UP = "voice.input.cleaned_up"  # Added back
    INTENT_DETECTED = "intent.detected"
    CLI_COMMAND = "cli.command"
    CLI_RESPONSE = "cli.response"  # Added back - needed by cli_service
    CLI_STATUS_REQUEST = "cli.status.request"
    CLI_HELP_REQUEST = "cli.help.request"
    REGISTER_COMMAND = "register.command"
    TOOL_REGISTRATION = "tool.registration"
    TOOL_REGISTRATION_REQUEST = "tool.registration.request"  # Added back
    TOOL_REGISTRATION_COMPLETE = "tool.registration.complete"  # Added back
    TOOL_EXECUTION_REQUEST = "tool.execution.request"
    TOOL_EXECUTION_RESULT = "tool.execution.result"
    TOOL_CALL_REQUEST = "tool.call.request"  # Added back
    TOOL_CALL_RESULT = "tool.call.result"  # Added back
    TOOL_CALL_ERROR = "tool.call.error"  # Added back
    COMMAND_RESPONSE = "command.response"
    SENTIMENT_ANALYSIS = "sentiment.analysis"
    COMMAND_CALL = "command.call"
    COMMAND_RESULT = "command.result"
    MIC_RECORDING_START = (
        "mic.recording.start"  # Added back - needed by deepgram_direct_mic
    )
    MIC_RECORDING_STOP = (
        "mic.recording.stop"  # Added back - needed by deepgram_direct_mic
    )
    MOUSE_RECORDING_STOPPED = "mouse.recording.stopped"  # Added back

    # Output events
    SPEECH_SYNTHESIS = "speech.synthesis"
    SPEECH_SYNTHESIS_STARTED = (
        "speech.synthesis.started"  # Added back - needed by music_controller
    )
    SPEECH_SYNTHESIS_ENDED = "speech.synthesis.ended"  # Added back - needed by music_controller and eye_light_controller
    SPEECH_SYNTHESIS_AMPLITUDE = "speech.synthesis.amplitude"  # Added back
    SPEECH_SYNTHESIS_REQUESTED = "speech.synthesis.requested"  # Added back
    SPEECH_SYNTHESIS_COMPLETED = "speech.synthesis.completed"  # Added back
    SPEECH_SYNTHESIS_CLEANED_UP = "speech.synthesis.cleaned_up"  # Added back
    SPEECH_GENERATION_REQUEST = "speech.generation.request"
    SPEECH_GENERATION_STARTED = "speech.generation.started"  # Added back
    SPEECH_GENERATION_COMPLETE = "speech.generation.complete"
    SPEECH_GENERATION_ERROR = "speech.generation.error"  # Added back
    SPEECH_AMPLITUDE = "speech.amplitude"
    LLM_RESPONSE = "llm.response"
    LLM_RESPONSE_CHUNK = "llm.response.chunk"  # Added back
    LLM_RESPONSE_TEXT = "llm.response.text"  # Added back
    LLM_SENTIMENT_ANALYZED = (
        "llm.sentiment.analyzed"  # Added back - needed by eye_light_controller
    )
    LLM_PROCESSING_STARTED = "llm.processing.started"  # Added back
    LLM_PROCESSING_ENDED = "llm.processing.ended"  # Added back
    INTENT_EXECUTION_RESULT = (
        "intent.execution.result"  # Added back - needed by gpt service
    )
    INTENT_CONSUMED = "intent.consumed"  # Added back
    EYE_COMMAND = "eye.command"
    LED_COMMAND = "led.command"  # Added back - needed by eye_light_controller
    LED_COMMAND_SUCCESS = "led.command.success"  # Added back
    LED_COMMAND_FAILURE = "led.command.failure"  # Added back
    LED_RESPONSE = "led.response"  # Added back
    LED_PATTERN_STARTED = "led.pattern.started"  # Added back
    LED_PATTERN_STOPPED = "led.pattern.stopped"  # Added back
    LED_ERROR = "led.error"  # Added back
    ARDUINO_COMMAND = "arduino.command"  # Added back
    ARDUINO_RESPONSE = "arduino.response"  # Added back
    MUSIC_COMMAND = "music.command"
    MUSIC_RESPONSE = "music.response"  # Added back
    MUSIC_PLAYBACK_STARTED = "music.playback.started"  # Added back
    MUSIC_PLAYBACK_STOPPED = "music.playback.stopped"  # Added back
    MUSIC_PLAYBACK_PAUSED = "music.playback.paused"  # Music was paused
    MUSIC_PLAYBACK_RESUMED = "music.playback.resumed"  # Music was resumed
    MUSIC_QUEUE_UPDATED = "music.queue.updated"  # Play queue was updated
    MUSIC_PROGRESS = "music.progress"  # Real-time playback progress updates
    MUSIC_VOLUME_CHANGED = "music.volume.changed"  # Added back
    MUSIC_ERROR = "music.error"  # Added back
    MUSIC_PLAY = "music.play"  # Added back
    MUSIC_STOP = "music.stop"  # Added back
    AUDIO_DUCKING_START = "audio.ducking.start"  # Added back
    AUDIO_DUCKING_STOP = "audio.ducking.stop"  # Added back
    AUDIO_ERROR = "audio.error"  # Added back
    MODE_CHANGED = "mode.changed"
    MODE_COMMAND = "mode.command"  # Added back
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
    MEMORY_VALUE = "memory.value"  # Response event for MEMORY_GET requests

    # MusicController events
    MUSIC_LIBRARY_UPDATED = (
        "music.library.updated"  # Emitted when the music library is loaded or changes
    )
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
    TRACK_ENDING_SOON = "track.ending.soon"  # Emitted by MusicController when a track is nearing its end

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
    SPEECH_CACHE_CLEANUP = "speech.cache.cleanup"  # For triggering manual cleanup

    # DJ Mode events
    DJ_COMMAND = "dj.command"  # Incoming CLI/voice commands for DJ mode (e.g., 'dj start', 'dj stop')
    DJ_MODE_START = "dj.mode.start"  # Emitted when DJ mode successfully starts
    DJ_MODE_STOP = "dj.mode.stop"  # Emitted when DJ mode successfully stops
    DJ_MODE_CHANGED = "dj.mode.changed"  # Emitted when DJ mode state changes
    DJ_NEXT_TRACK = "dj.next_track"  # Emitted to trigger next track transition
    DJ_NEXT_TRACK_SELECTED = "dj.next_track.selected"  # Emitted by BrainService when the next track is chosen
    DJ_COMMENTARY_REQUEST = (
        "dj.commentary.request"  # Emitted by BrainService to request GPT commentary
    )
    GPT_COMMENTARY_RESPONSE = (
        "gpt.commentary.response"  # Emitted by GPTService with generated commentary
    )
    DJ_QUEUE_TRACK = "dj.queue.track"  # Added back
    DJ_TRACK_ENDING_SOON = "dj.track.ending.soon"  # Added back
    DJ_CROSSFADE_STARTED = "dj.crossfade.started"  # Added back
    DJ_CROSSFADE_COMPLETE = "dj.crossfade.complete"  # Added back
    DJ_TRACK_QUEUED = "dj.track.queued"

    # Brain service events
    BRAIN_MUSIC_REQUEST = "brain.music.request"  # Added back
    BRAIN_MUSIC_STOP = "brain.music.stop"  # Added back

    # TTS events
    TTS_REQUEST = "tts.request"  # Request text-to-speech
    TTS_AUDIO_DATA = "tts.audio.data"  # Provide generated audio data
    TTS_GENERATE_REQUEST = "tts.generate.request"  # Added back
    TTS_PREGENERATE_REQUEST = "tts.pregenerate.request"  # Added back

    # ElevenLabsService Events
    VOICE_COMMAND = "voice.command"  # Added back
    VOICE_RESPONSE = "voice.response"  # Added back

    # Speech Cache events
    CLEAR_SPEECH_CACHE = "speech.cache.clear"

    # Dashboard events
    DASHBOARD_LOG = "/dashboard/log"
