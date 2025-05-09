"""
Event Topics for CantinaOS

This module defines the hierarchical event topics used throughout the CantinaOS system.
Each topic is a string-based path that represents a specific type of event.
"""

class EventTopics:
    """
    Hierarchical event topics for the CantinaOS system.
    Topics are organized by domain and functionality.
    """
    # Audio Processing Events
    AUDIO_TRANSCRIPTION_FINAL = "/audio/transcription/final"
    AUDIO_TRANSCRIPTION_INTERIM = "/audio/transcription/interim"
    AUDIO_RAW_CHUNK = "/audio/raw/chunk"
    
    # Speech Synthesis Events
    SPEECH_SYNTHESIS_STARTED = "/speech/synthesis/started"
    SPEECH_SYNTHESIS_AMPLITUDE = "/speech/synthesis/amplitude"
    SPEECH_SYNTHESIS_ENDED = "/speech/synthesis/ended"
    
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
    
    # Tool Execution Events
    TOOL_CALL_REQUEST = "/tools/execution/request"
    TOOL_CALL_RESULT = "/tools/execution/result"
    TOOL_CALL_ERROR = "/tools/execution/error"
    TOOL_REGISTRATION_REQUEST = "/tools/registration/request"
    
    # System Events
    SYSTEM_MODE_CHANGE = "/system/mode/change"
    SYSTEM_SET_MODE_REQUEST = "/system/mode/set_request"
    SYSTEM_STARTUP = "/system/lifecycle/startup"
    SYSTEM_SHUTDOWN = "/system/lifecycle/shutdown"
    
    # Service Status Events
    SERVICE_STATUS_UPDATE = "/system/diagnostics/status_update"
    SERVICE_ERROR = "/system/diagnostics/error"
    
    # Eye Control Events
    EYES_COMMAND = "/eyes/command/set"
    EYES_STATE_CHANGE = "/eyes/state/change"
    EYES_ERROR = "/eyes/error"
    
    # Music Control Events
    MUSIC_PLAY_REQUEST = "/music/command/play"
    MUSIC_STOP_REQUEST = "/music/command/stop"
    MUSIC_STATE_CHANGE = "/music/state/change"
    MUSIC_ERROR = "/music/error"
    
    # CLI Events
    CLI_COMMAND = "/cli/command"
    CLI_RESPONSE = "/cli/response"
    
    # System events
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN_REQUESTED = "system.shutdown.requested"
    SYSTEM_SHUTDOWN_COMPLETED = "system.shutdown.completed"
    
    # Service events
    SERVICE_STARTED = "service.started"
    SERVICE_STOPPED = "service.stopped"
    SERVICE_ERROR = "service.error"
    SERVICE_RECOVERED = "service.recovered"
    SERVICE_STATE_CHANGED = "service.state.changed"
    
    # Mode events
    MODE_CHANGED = "mode.changed"
    MODE_TRANSITION_STARTED = "mode.transition.started"
    MODE_TRANSITION_COMPLETED = "mode.transition.completed"
    
    # Voice events
    VOICE_AUDIO_RECEIVED = "voice.audio.received"
    VOICE_AUDIO_LEVEL = "voice.audio.level"
    VOICE_TRANSCRIPTION_STARTED = "voice.transcription.started"
    VOICE_TRANSCRIPTION_COMPLETED = "voice.transcription.completed"
    VOICE_PROCESSING_STARTED = "voice.processing.started"
    VOICE_PROCESSING_COMPLETED = "voice.processing.completed"
    VOICE_INPUT_CLEANED_UP = "voice.input.cleaned_up"
    
    # Speech events
    SPEECH_SYNTHESIS_REQUESTED = "speech.synthesis.requested"
    SPEECH_SYNTHESIS_STARTED = "speech.synthesis.started"
    SPEECH_SYNTHESIS_COMPLETED = "speech.synthesis.completed"
    SPEECH_SYNTHESIS_CLEANED_UP = "speech.synthesis.cleaned_up"
    
    # Music events
    MUSIC_PLAYBACK_REQUESTED = "music.playback.requested"
    MUSIC_PLAYBACK_STOP_REQUESTED = "music.playback.stop_requested"
    MUSIC_PLAYBACK_STARTED = "music.playback.started"
    MUSIC_PLAYBACK_STOPPED = "music.playback.stopped"
    MUSIC_VOLUME_CHANGED = "music.volume.changed"
    
    # Conversation events
    CONVERSATION_STARTED = "conversation.started"
    CONVERSATION_ENDED = "conversation.ended"
    CONVERSATION_STATE_CHANGED = "conversation.state.changed"
    
    # LED events
    LED_PATTERN_CHANGED = "led.pattern.changed"
    LED_BRIGHTNESS_CHANGED = "led.brightness.changed"
    
    # Service lifecycle events
    SERVICE_STATUS = "service.status"
    
    # Voice processing events
    VOICE_LISTENING_STARTED = "voice.listening.started"
    VOICE_LISTENING_STOPPED = "voice.listening.stopped"
    VOICE_TRANSCRIPTION_INTERIM = "voice.transcription.interim"
    VOICE_TRANSCRIPTION_FINAL = "voice.transcription.final" 