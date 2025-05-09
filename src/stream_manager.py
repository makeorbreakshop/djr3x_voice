"""
Stream Manager for DJ R3X.
Implements streaming voice processing pipeline with Deepgram ASR.
"""

import os
import asyncio
import logging
import numpy as np
import sounddevice as sd
from typing import Optional, Dict, Any, Callable, List
from queue import Queue
from pynput import keyboard
from deepgram import DeepgramClient, DeepgramClientOptions
from deepgram.clients.listen import LiveTranscriptionEvents
from deepgram.clients.listen.v1 import LiveOptions
import time

from src.bus import EventBus, EventTypes, SystemMode

# Configure logging
logger = logging.getLogger(__name__)

class StreamManager:
    """
    Manages streaming voice processing pipeline using Deepgram ASR.
    This implementation focuses on Phase 1 (streaming ASR) according to the strategy
    from the development log.
    """
    
    def __init__(
        self,
        event_bus: EventBus,
        deepgram_api_key: str,
        push_to_talk_mode: bool = True,
        buffer_size_ms: int = 100, 
        interim_results: bool = True,
        sample_rate: int = 16000,
        channels: int = 1,
        test_mode: bool = False,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        debug_mode: bool = False
    ):
        """Initialize the stream manager.
        
        Args:
            event_bus: Event bus instance
            deepgram_api_key: Deepgram API key
            push_to_talk_mode: Use push-to-talk instead of continuous listening
            buffer_size_ms: Size of audio buffer in milliseconds
            interim_results: Get interim results for faster processing
            sample_rate: Audio sample rate in Hz
            channels: Number of audio channels
            test_mode: Run in test mode without API calls
            loop: Explicit event loop to use for thread-safe operations
            debug_mode: Enable verbose debug logging
        """
        self.event_bus = event_bus
        self.push_to_talk_mode = push_to_talk_mode
        self.buffer_size_ms = buffer_size_ms
        self.interim_results = interim_results
        self.sample_rate = sample_rate
        self.channels = channels
        self.test_mode = test_mode
        self.debug_mode = debug_mode
        
        # Store reference to the main event loop for thread-safe operations
        self.main_loop = loop or asyncio.get_event_loop()
        
        logger.info(f"StreamManager initialized with push_to_talk_mode={push_to_talk_mode}, debug_mode={debug_mode}")
        
        # Initialize Deepgram client if not in test mode
        if not test_mode:
            # Configure Deepgram client with keepalive enabled
            config = DeepgramClientOptions(
                options={"keepalive": "true"}
            )
            self.deepgram_client = DeepgramClient(deepgram_api_key, config)
            logger.info("Deepgram client initialized successfully")
        
        # Push-to-talk state
        self.is_recording = False
        self.recording_event = asyncio.Event()
        
        # System mode flag - starts as inactive
        self.active = False
        
        # Streaming session and connection management
        self.connection = None
        self.streaming_task = None
        
        # Transcription state
        self.current_transcript = ""
        self.final_transcript = ""
        self.last_processed_time = 0
        self.min_transcript_length = 5  # Minimum characters to consider processing
        self.min_processing_interval_ms = 1000  # Minimum time between processing
        
        # Register for system mode changes
        self.event_bus.on(EventTypes.SYSTEM_MODE_CHANGED, self._handle_mode_change)
        
        # Initialize keyboard listener if using push-to-talk
        if push_to_talk_mode:
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            if self.debug_mode:
                logger.debug("Push-to-talk keyboard listener initialized (not yet started)")
    
    def _on_key_press(self, key):
        """Handle push-to-talk key press event."""
        if self.debug_mode:
            logger.debug(f"Key pressed: {key}, is_recording={self.is_recording}, active={self.active}")
        
        if key == keyboard.Key.space and self.active:
            # Toggle recording state
            if not self.is_recording:
                # Start recording
                self.is_recording = True
                logger.info("Push-to-talk: Recording STARTED (SPACEBAR pressed once)")
                
                # Use run_coroutine_threadsafe to properly schedule the start_streaming coroutine
                # from the keyboard listener thread
                future = asyncio.run_coroutine_threadsafe(
                    self.start_streaming(), 
                    self.main_loop
                )
                
                if self.debug_mode:
                    try:
                        # Wait for the result briefly to catch immediate errors
                        future.result(0.1)  # Small timeout to avoid blocking indefinitely
                    except asyncio.TimeoutError:
                        # This is expected as the coroutine may take longer
                        logger.debug("start_streaming() scheduled successfully")
                    except Exception as e:
                        logger.error(f"Error scheduling start_streaming: {e}")
                
                # Set the recording event to signal any waiting coroutines
                if self.main_loop.is_running():
                    self.main_loop.call_soon_threadsafe(self.recording_event.set)
                    if self.debug_mode:
                        logger.debug("Recording event set")
            else:
                # Stop recording
                self.is_recording = False
                logger.info("Push-to-talk: Recording STOPPED (SPACEBAR pressed again)")
                
                # Use run_coroutine_threadsafe to properly schedule the stop_streaming coroutine
                # from the keyboard listener thread
                future = asyncio.run_coroutine_threadsafe(
                    self.stop_streaming(),
                    self.main_loop
                )
                
                if self.debug_mode:
                    try:
                        # Wait for the result briefly to catch immediate errors
                        future.result(0.1)  # Small timeout to avoid blocking indefinitely
                    except asyncio.TimeoutError:
                        # This is expected as the coroutine may take longer
                        logger.debug("stop_streaming() scheduled successfully")
                    except Exception as e:
                        logger.error(f"Error scheduling stop_streaming: {e}")
                
                # Clear the recording event to signal recording has stopped
                if self.main_loop.is_running():
                    self.main_loop.call_soon_threadsafe(self.recording_event.clear)
                    if self.debug_mode:
                        logger.debug("Recording event cleared")
    
    def _on_key_release(self, key):
        """Handle push-to-talk key release event."""
        if self.debug_mode:
            logger.debug(f"Key released: {key}, is_recording={self.is_recording}, active={self.active}")
        
        # In toggle mode, we don't need to do anything on key release
        pass
    
    async def _handle_mode_change(self, data: Dict[str, Any]) -> None:
        """Handle system mode changes.
        
        Args:
            data: Event data containing old_mode and new_mode
        """
        if "new_mode" not in data:
            return
            
        new_mode = data["new_mode"]
        
        if new_mode == SystemMode.INTERACTIVE.value:
            # Enable streaming mode
            await self.activate()
        else:
            # Disable streaming mode for all other modes
            await self.deactivate()
    
    async def activate(self) -> None:
        """Activate the streaming manager."""
        if self.active:
            return
            
        logger.info("Activating streaming voice pipeline")
        self.active = True
        
        # Start keyboard listener in push-to-talk mode
        if self.push_to_talk_mode:
            # Ensure any previous listener is stopped before creating a new one
            if hasattr(self, 'keyboard_listener') and self.keyboard_listener.is_alive():
                self.keyboard_listener.stop()
                logger.info("Stopped existing keyboard listener")
                
            # Create a fresh keyboard listener
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            self.keyboard_listener.start()
            logger.info("Push-to-talk listener activated - PRESS SPACEBAR once to start recording, press again to stop")
            if self.debug_mode:
                logger.debug(f"Keyboard listener daemon: {self.keyboard_listener.daemon}, alive: {self.keyboard_listener.is_alive()}")
        else:
            # If not in push-to-talk mode, start streaming immediately
            self.is_recording = True
            logger.info("Continuous listening mode activated - starting streaming immediately")
            asyncio.create_task(self.start_streaming())
    
    async def deactivate(self) -> None:
        """Deactivate the streaming manager and clean up resources."""
        if not self.active:
            return
            
        logger.info("Deactivating streaming voice pipeline")
        
        # First, stop any active streaming
        if self.is_recording:
            await self.stop_streaming()
        
        # Stop the keyboard listener if it's running
        if hasattr(self, 'keyboard_listener') and self.keyboard_listener.is_alive():
            self.keyboard_listener.stop()
            logger.info("Push-to-talk listener deactivated")
        
        # Set active flag to False
        self.active = False
    
    async def _on_message(self, client, result, **kwargs):
        """Handle incoming transcript from Deepgram.
        
        Args:
            client: Deepgram client instance
            result: Transcript data from Deepgram
            **kwargs: Additional parameters
        """
        try:
            if self.debug_mode:
                logger.debug(f"Received Deepgram result: {result}")
                
            if not hasattr(result, "channel") or not hasattr(result.channel, "alternatives"):
                logger.warning("Invalid transcript format received")
                return
                
            transcript = result.channel.alternatives[0].transcript
            if not transcript:
                return
                
            is_final = result.is_final
            current_time_ms = int(time.time() * 1000)
            
            # Log the transcript status
            if not is_final:
                logger.debug(f"Interim transcript: {transcript}")
                self.current_transcript = transcript
            else:
                logger.info(f"Final transcript segment: {transcript}")
                self.final_transcript = transcript
                
            # Check if we need to process this transcript
            if current_time_ms - self.last_processed_time >= self.min_processing_interval_ms:
                if len(transcript) >= self.min_transcript_length:
                    # Process the transcript
                    logger.info(f"Processing final transcript: {transcript}")
                    await self._process_transcript(transcript)
                else:
                    logger.info(f"Transcript too short to process: {transcript}")
            
            # Update the last processed time
            self.last_processed_time = current_time_ms
            
        except Exception as e:
            logger.error(f"Error handling transcript: {e}")
            
    async def _process_transcript(self, transcript: str) -> None:
        """Process a received transcript and emit events."""
        try:
            # Emit event with the final transcript
            await self.event_bus.emit(EventTypes.VOICE_LISTENING_STOPPED, {"transcript": transcript})
            
            # Also emit processing started to notify other components
            await self.event_bus.emit(EventTypes.VOICE_PROCESSING_STARTED)
            
        except Exception as e:
            logger.error(f"Error processing transcript: {e}")
            await self.event_bus.emit(EventTypes.SYSTEM_ERROR, {
                "source": "stream_manager",
                "msg": f"Transcript processing error: {e}"
            })
    
    def _on_error(self, client, error, **kwargs):
        """Handle errors from Deepgram.
        
        Args:
            client: Deepgram client instance
            error: Error data from Deepgram
            **kwargs: Additional parameters
        """
        error_msg = str(error)
        logger.error(f"Deepgram error: {error_msg}")
        
        # Schedule error event on the main event loop
        if self.main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.event_bus.emit(EventTypes.SYSTEM_ERROR, {
                    "source": "stream_manager",
                    "msg": f"Deepgram error: {error_msg}"
                }),
                self.main_loop
            )
    
    def _on_close(self, client, close, **kwargs):
        """Handle connection close from Deepgram.
        
        Args:
            client: Deepgram client instance
            close: Close event data
            **kwargs: Additional parameters
        """
        logger.info("Deepgram connection closed")
        if self.debug_mode:
            logger.debug(f"Close details: {close}")
        
        # Reset connection reference
        self.connection = None
    
    def _on_open(self, client, open, **kwargs):
        """Handle WebSocket connection open event.
        
        Args:
            client: Deepgram client instance
            open: Open event data
            **kwargs: Additional parameters
        """
        logger.info("Deepgram WebSocket connection opened")
        if self.debug_mode:
            logger.debug(f"Open details: {open}")
    
    async def start_streaming(self) -> None:
        """Start the streaming ASR session."""
        if self.test_mode:
            logger.info("Test mode: Simulating stream start")
            await self.event_bus.emit(EventTypes.VOICE_LISTENING_STARTED)
            return
        
        try:
            # Emit event that we've started listening
            await self.event_bus.emit(EventTypes.VOICE_LISTENING_STARTED)
            
            # Clear any previous transcript
            self.current_transcript = ""
            self.final_transcript = ""
            
            # Use the websocket connection from DeepgramClient's listen API
            self.connection = self.deepgram_client.listen.websocket.v("1")
            
            if self.debug_mode:
                logger.debug("Created Deepgram websocket connection object")
            
            # Set up event handlers
            self.connection.on(LiveTranscriptionEvents.Open, self._on_open)
            self.connection.on(LiveTranscriptionEvents.Close, self._on_close)
            # For async handler, we need to use a wrapper function
            self.connection.on(LiveTranscriptionEvents.Transcript, 
                lambda client, result, **kwargs: asyncio.create_task(self._on_message(client, result, **kwargs)))
            self.connection.on(LiveTranscriptionEvents.Error, self._on_error)
            
            if self.debug_mode:
                logger.debug("Registered event handlers for Deepgram connection")
            
            # Configure live transcription options
            options = LiveOptions(
                model="nova-3",
                language="en",
                smart_format=True,
                interim_results=self.interim_results,
                endpointing=True,  # Auto-detect when speech ends
                vad_events=True,   # Enable Voice Activity Detection events
                punctuate=True,
                utterance_end_ms="1000",  # End utterance after 1000ms of silence
                encoding="linear16",
                channels=self.channels,
                sample_rate=self.sample_rate
            )
            
            if self.debug_mode:
                logger.debug(f"Created LiveOptions with: model=nova-3, interim_results={self.interim_results}, "
                            f"sample_rate={self.sample_rate}, channels={self.channels}")
            
            # Start the connection
            self.connection.start(options)
            
            if self.debug_mode:
                logger.debug("Deepgram connection started")
            
            # Start the audio stream task
            self.streaming_task = asyncio.create_task(self._stream_microphone())
            
            logger.info("Deepgram streaming session started")
            
        except Exception as e:
            logger.error(f"Error starting Deepgram streaming: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await self.event_bus.emit(EventTypes.SYSTEM_ERROR, {
                "source": "stream_manager",
                "msg": f"Streaming start error: {e}"
            })
    
    async def _stream_microphone(self) -> None:
        """Stream microphone audio to Deepgram in a separate task."""
        try:
            # Calculate buffer size based on sample rate and buffer_size_ms
            buffer_size = int(self.sample_rate * self.buffer_size_ms / 1000)
            
            if self.debug_mode:
                logger.debug(f"Starting microphone stream with buffer_size={buffer_size}, "
                           f"sample_rate={self.sample_rate}, channels={self.channels}")
            
            def audio_callback(indata, frames, time, status):
                """Callback function to process audio chunks from sounddevice."""
                if status:
                    logger.warning(f"Audio input status: {status}")
                
                # Convert to float32 in the range [-1.0, 1.0]
                # Then convert to int16 for Deepgram
                audio_data = indata.copy()  # Make a copy to avoid modifying the original
                audio_data = (audio_data * 32767).astype(np.int16).tobytes()
                
                # Send the audio data to Deepgram
                if self.connection and self.is_recording:
                    try:
                        self.connection.send(audio_data)
                        if self.debug_mode and frames % 10 == 0:  # Only log every 10th chunk to avoid flooding
                            logger.debug(f"Sent {len(audio_data)} bytes of audio data to Deepgram")
                    except Exception as e:
                        logger.error(f"Error sending audio to Deepgram: {e}")
                elif self.debug_mode and self.is_recording and not self.connection:
                    logger.debug("Audio captured but no active Deepgram connection")
            
            # Start streaming audio from the microphone
            with sd.InputStream(
                callback=audio_callback,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.float32,
                blocksize=buffer_size
            ):
                logger.info("Microphone stream started")
                # Keep streaming until the task is cancelled or connection is closed
                while self.is_recording:
                    await asyncio.sleep(0.1)  # Small sleep to avoid busy waiting
                    if self.debug_mode and not self.connection:
                        logger.debug("Warning: Microphone streaming but Deepgram connection is None")
        
        except asyncio.CancelledError:
            logger.info("Microphone streaming task cancelled")
        except Exception as e:
            logger.error(f"Error in microphone streaming: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def stop_streaming(self) -> None:
        """Stop the streaming ASR session and process the final result."""
        if self.test_mode:
            logger.info("Test mode: Simulating stream stop")
            await self.event_bus.emit(EventTypes.VOICE_LISTENING_STOPPED, {"transcript": "Test mode transcription"})
            await self.event_bus.emit(EventTypes.VOICE_PROCESSING_STARTED)
            return
            
        try:
            logger.info("Stopping Deepgram streaming session")
            
            # Cancel the streaming task if it's running
            if self.streaming_task and not self.streaming_task.done():
                if self.debug_mode:
                    logger.debug("Cancelling microphone streaming task")
                self.streaming_task.cancel()
                try:
                    await self.streaming_task
                except asyncio.CancelledError:
                    pass
                self.streaming_task = None
            
            # Close the connection gracefully
            if self.connection:
                if self.debug_mode:
                    logger.debug("Finishing Deepgram connection")
                self.connection.finish()
                self.connection = None
            
            # Determine what transcript to use
            transcript_to_use = self.final_transcript
            
            # Emit event with the final transcript
            if transcript_to_use:
                logger.info(f"Final processed transcript: {transcript_to_use}")
                await self.event_bus.emit(EventTypes.VOICE_LISTENING_STOPPED, {"transcript": transcript_to_use})
                
                # Also emit processing started to notify other components
                await self.event_bus.emit(EventTypes.VOICE_PROCESSING_STARTED)
            else:
                logger.info("No speech detected in this session")
                await self.event_bus.emit(EventTypes.VOICE_LISTENING_STOPPED, {"transcript": ""})
            
        except Exception as e:
            logger.error(f"Error stopping Deepgram streaming: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await self.event_bus.emit(EventTypes.SYSTEM_ERROR, {
                "source": "stream_manager",
                "msg": f"Streaming stop error: {e}"
            })
    
    async def stop(self) -> None:
        """Stop the stream manager and release resources."""
        logger.info("Stopping Stream Manager")
        
        # First deactivate to handle the task and keyboard listener
        await self.deactivate() 