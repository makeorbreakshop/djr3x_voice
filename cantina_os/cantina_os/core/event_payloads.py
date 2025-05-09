"""Event payload models for CantinaOS."""

from typing import Optional
from pydantic import BaseModel

class TranscriptionEventPayload(BaseModel):
    """Payload for transcription-related events."""
    conversation_id: str
    is_final: bool
    transcript: str
    confidence: float 