"""Event constants for CantinaOS."""

# Voice control events
VOICE_LISTENING_STARTED = "voice_listening_started"
VOICE_LISTENING_STOPPED = "voice_listening_stopped"

# Transcription events
TRANSCRIPTION_INTERIM = "transcription_interim"
TRANSCRIPTION_FINAL = "transcription_final"
TRANSCRIPTION_ERROR = "transcription_error"
TRANSCRIPTION_METRICS = "transcription_metrics"  # Performance metrics event

# Service status events
SERVICE_STATUS_CHANGED = "service_status_changed"
SERVICE_ERROR = "service_error" 