"""Mock implementation of ElevenLabs service."""
from typing import Dict, Any
import asyncio
from .base_mock import BaseMockService
from cantina_os.event_bus import EventBus
from cantina_os.core.event_topics import EventTopics

class ElevenLabsMock(BaseMockService):
    """Mock service for ElevenLabs TTS."""
    
    def __init__(self, event_bus: EventBus) -> None:
        """Initialize the ElevenLabs mock service."""
        super().__init__()
        self.event_bus = event_bus
        self.simulate_error_flag: bool = False
        
    async def initialize(self) -> None:
        """Initialize the mock service."""
        await super().initialize()
        self.record_call('initialize')
        await self.event_bus.on(EventTopics.SPEECH_SYNTHESIS_REQUESTED, self._handle_synthesis_request)
        
    async def shutdown(self) -> None:
        """Shutdown the mock service and cleanup resources."""
        await super().shutdown()
        self.record_call('shutdown')
        
    async def _handle_synthesis_request(self, payload: Dict[str, Any]) -> None:
        """Handle a speech synthesis request."""
        self.record_call('_handle_synthesis_request', payload)
        
        if self.simulate_error_flag:
            await self.event_bus.emit(EventTopics.SERVICE_ERROR, {
                "service": "elevenlabs",
                "error": "Simulated error",
                "conversation_id": payload.get("conversation_id")
            })
            return
            
        # Simulate processing time
        await asyncio.sleep(0.1)
        
        # Emit synthesis started
        await self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_STARTED, {
            "text": payload.get("text"),
            "conversation_id": payload.get("conversation_id")
        })
        
        # Simulate synthesis
        await asyncio.sleep(0.2)
        
        # Emit synthesis completed
        await self.event_bus.emit(EventTopics.SPEECH_SYNTHESIS_COMPLETED, {
            "conversation_id": payload.get("conversation_id"),
            "audio_data": b"mock_audio_data"  # Mock audio data
        })
        
    def simulate_error(self, error_msg: str) -> None:
        """Simulate an error condition."""
        self.record_call('simulate_error', error_msg)
        self.simulate_error_flag = True 