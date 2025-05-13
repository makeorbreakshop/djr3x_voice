from services.base import BaseService
import time

class AudioService(BaseService):
    async def initialize(self):
        await self.debug_log("Initializing AudioService", level="INFO")
        
        # Track initialization time
        start_time = time.time()
        # ... initialization code ...
        duration_ms = (time.time() - start_time) * 1000
        await self.debug_performance_metric("audio_init", duration_ms)
        
    async def process_audio(self, audio_data):
        await self.debug_log(f"Processing audio chunk of size {len(audio_data)}", level="DEBUG")
        await self.debug_state_transition("idle", "processing")
        
        start_time = time.time()
        try:
            # ... audio processing code ...
            await self.debug_log("Audio processing complete", level="INFO")
        except Exception as e:
            await self.debug_log(f"Audio processing failed: {str(e)}", level="ERROR")
        finally:
            duration_ms = (time.time() - start_time) * 1000
            await self.debug_performance_metric("audio_processing", duration_ms)
            await self.debug_state_transition("processing", "idle") 