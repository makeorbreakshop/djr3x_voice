"""
Event Topics for CantinaOS

This module defines all event topics used in the system.
Topics are organized hierarchically for clarity and maintainability.
"""

class EventTopics:
    """Event topics for system-wide communication."""
    
    # System events
    SYSTEM_STARTUP = "/system/lifecycle/startup"
    SYSTEM_SHUTDOWN = "/system/lifecycle/shutdown"
    SYSTEM_MODE_CHANGE = "/system/mode/change"
    SYSTEM_SET_MODE_REQUEST = "/system/mode/set_request"
    SYSTEM_ERROR = "/system/error"
    
    # Service events
    SERVICE_STATUS = "/service/status"  # Primary service status topic
    SERVICE_STATUS_UPDATE = "/service/status/update"  # For incremental updates
    SERVICE_STATE_CHANGED = "/service/state/changed"  # For tracking service state transitions
    SERVICE_ERROR = "/service/error"
    
    # CLI events
    CLI_COMMAND = "/cli/command"
    CLI_RESPONSE = "/cli/response"
    CLI_HELP_REQUEST = "/cli/help/request"
    CLI_STATUS_REQUEST = "/cli/status/request"
    REGISTER_COMMAND = "/cli/register/command"  # New topic for registering commands
    
    # Mode events
    MODE_COMMAND = "/mode/command"
    MODE_TRANSITION_STARTED = "/mode/transition/started"
    MODE_TRANSITION_COMPLETE = "/mode/transition/complete"
    MODE_TRANSITION_COMPLETED = "/mode/transition/completed"
    MODE_TRANSITION_FAILED = "/mode/transition/failed"
    
    # Eye events
    EYE_COMMAND = "/eye/command"  # Added for eye control commands
    
    # LED events
    LED_COMMAND = "/led/command"  # Added for LED control commands
    LED_COMMAND_SUCCESS = "/led/command/success"
    LED_COMMAND_FAILURE = "/led/command/failure"
    LED_RESPONSE = "/led/response"
    LED_PATTERN_STARTED = "/led/pattern/started"
    LED_PATTERN_STOPPED = "/led/pattern/stopped"
    LED_ERROR = "/led/error"
    
    # Arduino events
    ARDUINO_COMMAND = "/arduino/command"  # Added for Arduino control commands
    ARDUINO_RESPONSE = "/arduino/response"
    
    # Voice events
    VOICE_COMMAND = "/voice/command"
    VOICE_RESPONSE = "/voice/response"
    VOICE_LISTENING_STARTED = "voice.listening.started"  # Used by services
    VOICE_LISTENING_STOPPED = "voice.listening.stopped"  # Used by services
    VOICE_PROCESSING_STARTED = "/voice/processing/started"
    VOICE_PROCESSING_COMPLETE = "/voice/processing/complete"
    VOICE_ERROR = "/voice/error"
    VOICE_AUDIO_RECEIVED = "/voice/audio/received"
    VOICE_AUDIO_LEVEL = "/voice/audio/level"
    VOICE_INPUT_CLEANED_UP = "/voice/input/cleaned_up"
    
    # Music events
    MUSIC_COMMAND = "/music/command"
    MUSIC_RESPONSE = "/music/response"
    MUSIC_PLAYBACK_STARTED = "/music/playback/started"
    MUSIC_PLAYBACK_STOPPED = "/music/playback/stopped"
    MUSIC_VOLUME_CHANGED = "/music/volume/changed"
    MUSIC_ERROR = "/music/error"
    MUSIC_PLAY = "/music/play"  # Added for backward compatibility
    MUSIC_STOP = "/music/stop"  # Added for backward compatibility
    MUSIC_LIBRARY_UPDATED = "/music/library/updated"  # New topic for library updates
    
    # Audio events
    AUDIO_DUCKING_START = "/audio/ducking/start"
    AUDIO_DUCKING_STOP = "/audio/ducking/stop"
    AUDIO_ERROR = "/audio/error"
    
    # Audio Processing Events
    AUDIO_TRANSCRIPTION_FINAL = "/audio/transcription/final"
    AUDIO_TRANSCRIPTION_INTERIM = "/audio/transcription/interim"
    AUDIO_RAW_CHUNK = "/audio/raw/chunk"
    
    # Speech Synthesis Events
    SPEECH_SYNTHESIS_STARTED = "/speech/synthesis/started"
    SPEECH_SYNTHESIS_AMPLITUDE = "/speech/synthesis/amplitude"
    SPEECH_SYNTHESIS_ENDED = "/speech/synthesis/ended"
    SPEECH_SYNTHESIS_REQUESTED = "/speech/synthesis/requested"
    SPEECH_SYNTHESIS_COMPLETED = "/speech/synthesis/completed"
    SPEECH_SYNTHESIS_CLEANED_UP = "/speech/synthesis/cleaned_up"
    
    # Speech Generation Events (ElevenLabs)
    SPEECH_GENERATION_REQUEST = "/speech/generation/request"
    SPEECH_GENERATION_STARTED = "/speech/generation/started"
    SPEECH_GENERATION_COMPLETE = "/speech/generation/complete"
    SPEECH_GENERATION_ERROR = "/speech/generation/error"
    
    # LLM Events
    LLM_RESPONSE = "/llm/response/complete"
    LLM_RESPONSE_CHUNK = "/llm/response/chunk"
    LLM_RESPONSE_TEXT = "/llm/response/text"
    LLM_SENTIMENT_ANALYZED = "/llm/sentiment/analyzed"
    LLM_PROCESSING_STARTED = "/llm/processing/started"
    LLM_PROCESSING_ENDED = "/llm/processing/ended"
    INTENT_DETECTED = "/llm/intent/detected"  # New event for detected intents from LLM
    INTENT_EXECUTION_RESULT = "/llm/intent/execution_result"  # New event for intent execution results
    
    # Transcription Events
    TRANSCRIPTION_FINAL = "/transcription/final"
    TRANSCRIPTION_INTERIM = "/transcription/interim"
    TRANSCRIPTION_ERROR = "/transcription/error"
    TRANSCRIPTION_METRICS = "/transcription/metrics"
    
    # Tool Execution Events
    TOOL_REGISTRATION_REQUEST = "/tools/registration/request"
    TOOL_REGISTRATION_COMPLETE = "/tools/registration/complete"
    TOOL_CALL_REQUEST = "/tools/execution/request"
    TOOL_CALL_RESULT = "/tools/execution/result"
    TOOL_CALL_ERROR = "/tools/execution/error"
    
    # System mode events
    SYSTEM_SHUTDOWN_REQUESTED = "/system/shutdown/requested"
    
    # Debug topics
    DEBUG_LOG = "/debug/log"
    DEBUG_COMMAND = "/debug/command"  # Added for debug level commands
    DEBUG_COMMAND_TRACE = "/debug/command/trace"
    DEBUG_PERFORMANCE = "/debug/performance"
    DEBUG_STATE_TRANSITION = "/debug/state_transition"
    DEBUG_CONFIG = "/debug/config"
    DEBUG_SET_GLOBAL_LEVEL = "/debug/set_global_level" # New event for global log level

    # Mouse Input Events
    MIC_RECORDING_START = "/mic/recording/start"
    MIC_RECORDING_STOP = "/mic/recording/stop"
    MOUSE_RECORDING_STOPPED = "/mouse/recording/stopped"  # New event for immediate eye pattern changes

    # New topics from the code block
    SERVICE_STATUS = "service.status"
    VOICE_LISTENING_STARTED = "voice.listening.started"
    VOICE_LISTENING_STOPPED = "voice.listening.stopped"
    VOICE_TRANSCRIPTION_INTERIM = "voice.transcription.interim"
    VOICE_TRANSCRIPTION_FINAL = "voice.transcription.final"
    DEBUG_LOG = "debug.log"
    DEBUG_COMMAND_TRACE = "debug.command.trace"
    DEBUG_PERFORMANCE = "debug.performance"
    DEBUG_STATE_TRANSITION = "debug.state.transition"
    DEBUG_CONFIG = "debug.config"

    # Speech synthesis events
    SPEECH_SYNTHESIS_REQUESTED = "/speech/synthesis/requested"
    SPEECH_SYNTHESIS_COMPLETED = "/speech/synthesis/completed"
    SPEECH_SYNTHESIS_CLEANED_UP = "/speech/synthesis/cleaned_up"
    
    # Speech synthesis events
    SPEECH_SYNTHESIS_REQUESTED = "/speech/synthesis/requested"
    SPEECH_SYNTHESIS_COMPLETED = "/speech/synthesis/completed"
    SPEECH_SYNTHESIS_CLEANED_UP = "/speech/synthesis/cleaned_up"
    
    # Debug topics
    DEBUG_LOG = "/debug/log"
    DEBUG_COMMAND_TRACE = "/debug/command/trace"
    DEBUG_PERFORMANCE = "/debug/performance"
    DEBUG_STATE_TRANSITION = "/debug/state/transition"
    DEBUG_CONFIG = "/debug/config"

    # Mouse Input Events
    MIC_RECORDING_START = "/mic/recording/start"
    MIC_RECORDING_STOP = "/mic/recording/stop"

    # Plan and timeline events
    PLAN_READY = "/plan/ready"
    PLAN_STARTED = "/plan/started"
    STEP_READY = "/plan/step/ready"
    STEP_EXECUTED = "/plan/step/executed"
    PLAN_ENDED = "/plan/ended"
    
    # Memory events
    MEMORY_UPDATED = "/memory/updated"
    MEMORY_GET = "/memory/get"
    MEMORY_SET = "/memory/set"
    MEMORY_VALUE = "/memory/value"
    
    # Intent management events
    INTENT_CONSUMED = "/llm/intent/consumed"
    
    # TTS events for Timeline usage
    TTS_GENERATE_REQUEST = "/tts/generate/request"
    TTS_PREGENERATE_REQUEST = "/tts/pregenerate/request"
    
    # Direct Brain service communication events
    BRAIN_MUSIC_REQUEST = "/brain/music_request"
    BRAIN_MUSIC_STOP = "/brain/music_stop"

    # DJ Mode Events
    DJ_MODE_CHANGED = "/dj/mode/changed"
    DJ_MODE_START = "/dj/mode/start"
    DJ_MODE_STOP = "/dj/mode/stop"
    DJ_NEXT_TRACK = "/dj/track/next"
    DJ_QUEUE_TRACK = "/dj/track/queue"
    DJ_TRACK_ENDING_SOON = "/dj/track/ending_soon"
    DJ_CROSSFADE_STARTED = "/dj/crossfade/started"
    DJ_CROSSFADE_COMPLETE = "/dj/crossfade/complete"
    DJ_TRACK_QUEUED = "/dj/track/queued"
    
    # Music-specific events for DJ mode
    TRACK_ENDING_SOON = "/music/track/ending_soon"
    CROSSFADE_STARTED = "/music/crossfade/started"
    
    # Cached Speech Events
    SPEECH_CACHE_REQUEST = "/speech/cache/request"
    SPEECH_CACHE_READY = "/speech/cache/ready"
    SPEECH_CACHE_ERROR = "/speech/cache/error"
    SPEECH_CACHE_CLEANUP = "/speech/cache/cleanup"
    SPEECH_CACHE_PLAYBACK_REQUEST = "/speech/cache/playback/request"
    SPEECH_CACHE_PLAYBACK_STARTED = "/speech/cache/playback/started"
    SPEECH_CACHE_PLAYBACK_COMPLETED = "/speech/cache/playback/completed"
    
    # TTS Direct events for CachedSpeechService
    TTS_REQUEST = "/tts/request"
    TTS_AUDIO_DATA = "/tts/audio_data" 