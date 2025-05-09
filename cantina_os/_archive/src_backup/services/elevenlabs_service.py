"""ElevenLabs service for speech synthesis."""
from typing import Optional
import asyncio
import tempfile
import os
import sounddevice as sd
import numpy as np

from ..base_service import BaseService
from ..event_topics import EventTopics

class ElevenLabsService(BaseService):
    """Service for ElevenLabs text-to-speech synthesis."""
    
    def __init__(self, event_bus):
        """Initialize the ElevenLabs service."""
        super().__init__(event_bus)
        self._temp_dir = tempfile.mkdtemp(prefix="elevenlabs_")
        self._current_conversation_id: Optional[str] = None
        
    async def start(self):
        """Start the ElevenLabs service."""
        await super().start()
        self.event_bus.subscribe(EventTopics.SPEECH_SYNTHESIS_REQUESTED, self._handle_synthesis_request)
        
    async def stop(self):
        """Stop the ElevenLabs service and clean up resources."""
        await self._cleanup()
        await super().stop()
        
    async def _cleanup(self):
        """Clean up synthesis resources."""
        # Clean up temp files
        if os.path.exists(self._temp_dir):
            for file in os.listdir(self._temp_dir):
                try:
                    os.remove(os.path.join(self._temp_dir, file))
                except OSError:
                    pass
            try:
                os.rmdir(self._temp_dir)
            except OSError:
                pass
                
        # Emit cleanup event if we have an active conversation
        if self._current_conversation_id:
            await self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_CLEANED_UP, {
                "conversation_id": self._current_conversation_id
            })
            self._current_conversation_id = None
        
    async def _handle_synthesis_request(self, topic: str, payload: dict):
        """Handle a speech synthesis request."""
        text = payload.get("text")
        conversation_id = payload.get("conversation_id")
        
        if not text:
            await self.event_bus.emit(EventTopics.SERVICE_ERROR, {
                "service_name": self.__class__.__name__,
                "error": "No text provided for synthesis",
                "conversation_id": conversation_id
            })
            return
            
        try:
            self._current_conversation_id = conversation_id
            
            # Emit synthesis started event
            await self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_STARTED, {
                "text": text,
                "conversation_id": conversation_id
            })
            
            # In a real implementation, we would call ElevenLabs API
            # For now, we just simulate synthesis with a sine wave
            sample_rate = 16000
            duration = len(text) * 0.1  # 100ms per character
            t = np.linspace(0, duration, int(sample_rate * duration))
            audio_data = np.sin(2 * np.pi * 440 * t).astype(np.float32)
            
            # Simulate API delay
            await asyncio.sleep(duration * 0.1)
            
            # Emit synthesis completed event
            await self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_COMPLETED, {
                "conversation_id": conversation_id,
                "audio_data": audio_data.tobytes()
            })
            
        except Exception as e:
            await self.event_bus.emit(EventTopics.SERVICE_ERROR, {
                "service_name": self.__class__.__name__,
                "error": str(e),
                "conversation_id": conversation_id
            }) 