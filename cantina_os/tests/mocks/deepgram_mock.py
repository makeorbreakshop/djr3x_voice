"""Mock implementation of the Deepgram streaming transcription service."""
import asyncio
from typing import Any, Callable, Dict, Optional
from .base_mock import BaseMockService
from cantina_os.event_bus import EventBus

class DeepgramMock(BaseMockService):
    """Mock implementation of Deepgram's streaming transcription service.
    
    This mock simulates the behavior of Deepgram's WebSocket-based streaming API,
    including transcript events, connection lifecycle, and error conditions.
    """
    
    def __init__(self, event_bus: EventBus) -> None:
        """Initialize the Deepgram mock service."""
        super().__init__()
        self.event_bus = event_bus
        self.websocket_connected: bool = False
        self.streaming_active: bool = False
        self._on_transcript: Optional[Callable[[Dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._transcript_task: Optional[asyncio.Task] = None
        self.simulate_error_flag: bool = False
        
    async def initialize(self) -> None:
        """Initialize the mock service."""
        await super().initialize()
        self.record_call('initialize')
        
    async def shutdown(self) -> None:
        """Shutdown the mock service and cleanup resources."""
        if self.streaming_active:
            await self.stop_streaming()
        await super().shutdown()
        self.record_call('shutdown')
        
    async def connect(self) -> None:
        """Simulate WebSocket connection establishment."""
        self.record_call('connect')
        self.websocket_connected = True
        
    async def disconnect(self) -> None:
        """Simulate WebSocket disconnection."""
        self.record_call('disconnect')
        self.websocket_connected = False
        
    def on_transcript(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register transcript callback."""
        self.record_call('on_transcript', callback)
        self._on_transcript = callback
        
    def on_error(self, callback: Callable[[str], None]) -> None:
        """Register error callback."""
        self.record_call('on_error', callback)
        self._on_error = callback
        
    async def start_streaming(self) -> None:
        """Start simulated streaming transcription."""
        if not self.websocket_connected:
            raise RuntimeError("Must connect before streaming")
            
        self.record_call('start_streaming')
        self.streaming_active = True
        self._transcript_task = asyncio.create_task(self._simulate_transcripts())
        
    async def stop_streaming(self) -> None:
        """Stop simulated streaming transcription."""
        self.record_call('stop_streaming')
        self.streaming_active = False
        if self._transcript_task:
            self._transcript_task.cancel()
            try:
                await self._transcript_task
            except asyncio.CancelledError:
                pass
            self._transcript_task = None
            
    async def _simulate_transcripts(self) -> None:
        """Simulate transcript events based on configured responses."""
        try:
            while self.streaming_active:
                if self._on_transcript:
                    transcript = self.get_response('transcript')
                    if transcript:
                        self._on_transcript(transcript)
                await asyncio.sleep(0.1)  # Simulate processing time
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._on_error:
                self._on_error(str(e))
                
    def simulate_error(self, error_msg: str) -> None:
        """Simulate an error condition."""
        self.record_call('simulate_error', error_msg)
        if self._on_error:
            self._on_error(error_msg) 