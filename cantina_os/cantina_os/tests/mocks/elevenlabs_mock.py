"""Mock implementation of ElevenLabs service for testing."""
import asyncio
from typing import Optional, Dict, Any
from cantina_os.base_service import BaseService
from cantina_os.event_topics import EventTopics
from cantina_os.event_payloads import ServiceStatus, BaseEventPayload

class ElevenLabsMock(BaseService):
    """Mock ElevenLabs service for testing."""
    
    def __init__(self, event_bus):
        """Initialize the mock service."""
        super().__init__(service_name="ElevenLabsMock", event_bus=event_bus)
        self.synthesis_count = 0
        self.current_synthesis: Optional[asyncio.Task] = None
        
    async def start(self):
        """Start the mock service."""
        await super().start()
        await self.event_bus.on(EventTopics.SPEECH_SYNTHESIS_REQUESTED, self._handle_synthesis_request)
        await self._emit_status(ServiceStatus.RUNNING, "Mock service started")
        
    async def stop(self):
        """Stop the mock service."""
        if self.current_synthesis and not self.current_synthesis.done():
            self.current_synthesis.cancel()
            try:
                await self.current_synthesis
            except asyncio.CancelledError:
                pass
        await super().stop()
        
    async def _handle_synthesis_request(self, payload: Dict[str, Any]):
        """Handle a speech synthesis request."""
        try:
            # Emit started event
            await self.emit(
                EventTopics.SPEECH_SYNTHESIS_STARTED,
                BaseEventPayload(conversation_id=payload.get("conversation_id"))
            )
            
            # Simulate synthesis time
            await asyncio.sleep(0.1)
            
            # Emit completed event
            await self.emit(
                EventTopics.SPEECH_SYNTHESIS_COMPLETED,
                BaseEventPayload(conversation_id=payload.get("conversation_id"))
            )
            
            # Emit cleanup event
            await self.emit(
                EventTopics.SPEECH_SYNTHESIS_CLEANED_UP,
                BaseEventPayload(conversation_id=payload.get("conversation_id"))
            )
            
            self.synthesis_count += 1
            
        except Exception as e:
            self.logger.error(f"Error in mock synthesis: {e}")
            self._emit_status(ServiceStatus.ERROR, f"Mock synthesis error: {e}")
            raise 