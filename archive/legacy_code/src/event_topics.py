"""
Event Topics Constants

This module defines all event topics used in the system.
"""

class EventTopics:
    """Event topic constants."""
    
    # Service Status Events
    SERVICE_STATUS_UPDATE = "service/status"
    
    # Mode Transition Events
    MODE_TRANSITION_STARTED = "mode/transition/started"
    MODE_TRANSITION_COMPLETE = "mode/transition/complete"
    MODE_TRANSITION_FAILED = "mode/transition/failed"
    SYSTEM_MODE_CHANGE = "system/mode/change"
    
    # Mode Control Events
    SYSTEM_SET_MODE_REQUEST = "system/set_mode/request"
    
    # CLI Events
    CLI_COMMAND = "cli/command"
    CLI_RESPONSE = "cli/response" 