"""
CantinaOS Core Package

This package contains the core components of the CantinaOS system.
"""

from .base_service import BaseService
from .event_topics import EventTopics
from .event_payloads import (
    BaseEventPayload,
    TranscriptionTextPayload,
    LLMResponsePayload,
    ServiceStatus,
    LogLevel
)

__version__ = "0.1.0"
__author__ = "DJ R3X Development Team"
__license__ = "MIT"

# Version information tuple
VERSION_INFO = tuple(map(int, __version__.split(".")))

"""CantinaOS - DJ R3X Voice Control System.""" 