"""
Debug Service Configuration
"""

DEBUG_CONFIG = {
    # Default log level for all components
    "default_level": "INFO",
    
    # Component-specific log levels
    "component_levels": {
        "AudioService": "DEBUG",  # More detailed logging for audio
        "NetworkService": "WARNING",  # Less noise from network
        "StateService": "INFO"
    },
    
    # Feature flags
    "trace_commands": True,  # Enable command tracing
    "collect_metrics": True,  # Enable performance metrics
    
    # Queue settings
    "max_queue_size": 1000,  # Maximum number of pending log messages
    "flush_interval": 0.1,  # Seconds between queue flushes
    
    # Performance thresholds (in milliseconds)
    "performance_thresholds": {
        "audio_processing": 100,  # Alert if audio processing takes longer
        "network_request": 500,   # Alert if network requests are slow
        "state_transition": 50    # Alert if state transitions are delayed
    }
} 