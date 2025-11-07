"""
Service Status Models

Defines the models and enums for service status tracking and reporting.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel

class ServiceStatus(str, Enum):
    """Service status states."""
    INITIALIZING = "INITIALIZING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"

class ServiceStatusPayload(BaseModel):
    """
    Payload for service status update events.
    
    Attributes:
        service: Name of the service
        status: Current service status
        message: Optional status message
    """
    service: str
    status: ServiceStatus
    message: Optional[str] = None 