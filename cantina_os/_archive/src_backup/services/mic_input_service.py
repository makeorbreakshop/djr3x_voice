"""MicInputService for audio capture."""
from typing import Optional
import asyncio
import sounddevice as sd
import numpy as np

from ..base_service import BaseService
from ..event_topics import EventTopics

class MicInputService(BaseService):
    """Service for capturing audio input from microphone."""
    
    def __init__(self, event_bus):
        super().__init__(event_bus)
        self.stream: Optional[sd.InputStream] = None
        self.sample_rate = 16000
        self.channels = 1
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
    async def start(self):
        """Start the microphone input service."""
        await super().start()
        self._loop = asyncio.get_running_loop()
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            callback=self._audio_callback
        )
        self.stream.start()
        
    async def stop(self):
        """Stop the microphone input service."""
        await self._cleanup()
        await super().stop()
        
    async def _cleanup(self):
        """Clean up audio input resources."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
            await self.event_bus.emit(EventTopics.VOICE_INPUT_CLEANED_UP, {})
        
    def _audio_callback(self, indata, frames, time, status):
        """Handle incoming audio data."""
        if status:
            print(f"Audio callback status: {status}")
            return
            
        # Calculate audio level
        audio_level = float(np.abs(indata).mean())
        
        # Since we're in a callback, we need to use call_soon_threadsafe
        # to schedule the coroutine execution in the event loop
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(
                self.event_bus.emit(
                    EventTopics.VOICE_AUDIO_RECEIVED,
                    {
                        "audio_data": indata.tobytes(),
                        "sample_rate": self.sample_rate,
                        "channels": self.channels
                    }
                )
            )
        )
        
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(
                self.event_bus.emit(
                    EventTopics.VOICE_AUDIO_LEVEL,
                    {"audio_level": audio_level}
                )
            )
        )
        
    async def _handle_shutdown(self, topic: str, payload: dict):
        """Handle system shutdown request."""
        await self.stop() 