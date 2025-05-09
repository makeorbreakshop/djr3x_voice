"""Event topics for CantinaOS."""

class EventTopics:
    """Event topics used throughout the system."""
    
    # Service lifecycle events
    SERVICE_STATUS = "service.status"
    
    # Voice processing events
    VOICE_LISTENING_STARTED = "voice.listening.started"
    VOICE_LISTENING_STOPPED = "voice.listening.stopped"
    VOICE_TRANSCRIPTION_INTERIM = "voice.transcription.interim"
    VOICE_TRANSCRIPTION_FINAL = "voice.transcription.final" 