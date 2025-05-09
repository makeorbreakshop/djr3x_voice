"""Mock Deepgram service for testing."""

from typing import Optional, Dict, Any, Callable
import asyncio
from pydantic import BaseModel
from cantina_os.core import BaseService, EventTopics, TranscriptionEventPayload

class MockDeepgramService(BaseService):
    """Mock implementation of Deepgram's streaming transcription service for testing."""
    
    def __init__(self, event_bus: Any):
        super().__init__(name="MockDeepgramService", event_bus=event_bus)
        self.is_connected: bool = False
        self.is_listening: bool = False
        self.current_conversation_id: Optional[str] = None
        self._ws_client = None
        self._audio_queue = asyncio.Queue()
        
    async def _start(self) -> None:
        """Start the mock service."""
        await super()._start()
        self.is_connected = True
        await self._emit_event(
            EventTopics.SERVICE_STATUS,
            {"status": "started", "service": self.name}
        )
        
    async def _stop(self) -> None:
        """Stop the mock service."""
        self.is_connected = False
        self.is_listening = False
        await self._audio_queue.put(None)  # Signal to stop processing
        await super()._stop()
        
    async def _cleanup(self) -> None:
        """Clean up resources."""
        self.is_connected = False
        self.is_listening = False
        self._ws_client = None
        await super()._cleanup()
        
    async def start_streaming(self, conversation_id: str) -> None:
        """Start streaming transcription for a conversation."""
        if not self.is_connected:
            raise RuntimeError("Service not connected")
            
        self.current_conversation_id = conversation_id
        self.is_listening = True
        await self._emit_event(
            EventTopics.VOICE_LISTENING_STARTED,
            TranscriptionEventPayload(
                conversation_id=conversation_id,
                is_final=False,
                transcript="",
                confidence=1.0
            )
        )
        
    async def stop_streaming(self) -> None:
        """Stop streaming transcription."""
        if not self.is_listening:
            return
            
        self.is_listening = False
        if self.current_conversation_id:
            await self._emit_event(
                EventTopics.VOICE_LISTENING_STOPPED,
                TranscriptionEventPayload(
                    conversation_id=self.current_conversation_id,
                    is_final=True,
                    transcript="mock transcription complete",
                    confidence=1.0
                )
            )
        self.current_conversation_id = None
        
    async def process_audio(self, audio_data: bytes) -> None:
        """Process incoming audio data."""
        if not self.is_listening:
            return
            
        # Simulate processing delay
        await asyncio.sleep(0.1)
        
        # Emit intermediate transcription result
        if self.current_conversation_id:
            await self._emit_event(
                EventTopics.VOICE_TRANSCRIPTION_INTERIM,
                TranscriptionEventPayload(
                    conversation_id=self.current_conversation_id,
                    is_final=False,
                    transcript="mock interim transcription",
                    confidence=0.8
                )
            )
            
    async def _simulate_final_transcription(self) -> None:
        """Simulate a final transcription result."""
        if not self.is_listening or not self.current_conversation_id:
            return
            
        await self._emit_event(
            EventTopics.VOICE_TRANSCRIPTION_FINAL,
            TranscriptionEventPayload(
                conversation_id=self.current_conversation_id,
                is_final=True,
                transcript="mock final transcription",
                confidence=0.95
            )
        )
        
    def _on_open(self, client: Any) -> None:
        """Handle WebSocket connection open."""
        self._ws_client = client
        self.is_connected = True
        
    def _on_close(self) -> None:
        """Handle WebSocket connection close."""
        self._ws_client = None
        self.is_connected = False
        self.is_listening = False
        
    def _on_error(self, error: Exception) -> None:
        """Handle WebSocket error."""
        self.logger.error(f"Mock Deepgram WebSocket error: {error}")
        self._ws_client = None
        self.is_connected = False
        self.is_listening = False 