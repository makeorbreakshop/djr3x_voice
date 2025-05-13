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
    
    # Mode events
    MODE_COMMAND = "/mode/command"
    MODE_TRANSITION_STARTED = "/mode/transition/started"
    MODE_TRANSITION_COMPLETE = "/mode/transition/complete"
    MODE_TRANSITION_COMPLETED = "/mode/transition/completed"
    MODE_TRANSITION_FAILED = "/mode/transition/failed"
    
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
    
    # LED events
    LED_COMMAND = "/led/command"
    LED_RESPONSE = "/led/response"
    LED_PATTERN_STARTED = "/led/pattern/started"
    LED_PATTERN_STOPPED = "/led/pattern/stopped"
    LED_ERROR = "/led/error"
    
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