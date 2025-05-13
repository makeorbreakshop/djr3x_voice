#!/usr/bin/env python3
"""
Deepgram Live Test - Troubleshooting Version

This script is specifically designed to debug and test Deepgram's transcription
service with minimal dependencies. It outputs detailed debug information and
handles errors gracefully to help diagnose connectivity issues.

Usage:
python test_deepgram_live.py
"""

import asyncio
import os
import logging
import time
import json
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
import queue
import threading
from deepgram import (
    DeepgramClient, 
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents
)

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("deepgram_debug")

# Set specific loggers to DEBUG level
logging.getLogger("websockets").setLevel(logging.DEBUG)
logging.getLogger("deepgram").setLevel(logging.DEBUG)

# Audio settings - using standard settings known to work with Deepgram
SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 1024
DTYPE = np.int16

class DeepgramTester:
    """Class for testing Deepgram live transcription."""
    
    def __init__(self, api_key):
        """Initialize with Deepgram API key."""
        self.api_key = api_key
        self.client = None
        self.connection = None
        self.stream = None
        self.received_transcripts = []
        self.audio_queue = queue.Queue()
        self.processing_thread = None
        self.thread_running = False
        self.is_recording = False
        
    async def setup_connection(self):
        """Set up Deepgram connection with detailed logging."""
        logger.info("Initializing Deepgram client")
        
        # Configure client with keepalive
        options = DeepgramClientOptions(options={"keepalive": "true"})
        self.client = DeepgramClient(self.api_key, options)
        logger.info(f"Created Deepgram client using SDK version: {self.client.__module__}")
        
        # Create websocket connection
        logger.info("Creating websocket connection")
        self.connection = self.client.listen.websocket.v("1")
        
        # Set up event handlers
        logger.info("Registering event handlers")
        self.connection.on(LiveTranscriptionEvents.Open, self._on_open)
        self.connection.on(LiveTranscriptionEvents.Close, self._on_close)
        self.connection.on(LiveTranscriptionEvents.Error, self._on_error)
        self.connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        
        # Configure transcription options
        logger.info("Configuring transcription options")
        options = LiveOptions(
            model="nova-3",
            smart_format=True,
            language="en-US",
            encoding="linear16",
            channels=CHANNELS,
            sample_rate=SAMPLE_RATE,
            interim_results=True,
            utterance_end_ms="1000"
        )
        
        # Start the connection
        logger.info("Starting Deepgram connection")
        self.connection.start(options)
        logger.info("Connection started")
    
    # Event handlers - using flexible parameter handling
    def _on_open(self, *args, **kwargs):
        logger.info("✅ Deepgram connection opened")
        logger.debug(f"Open event details - args: {args}, kwargs: {kwargs}")
    
    def _on_close(self, *args, **kwargs):
        logger.info("Deepgram connection closed")
        logger.debug(f"Close event details - args: {args}, kwargs: {kwargs}")
    
    def _on_error(self, *args, **kwargs):
        logger.error("⚠️ Deepgram error occurred")
        logger.debug(f"Error event details - args: {args}, kwargs: {kwargs}")
        
        # Try to extract error message from various sources
        if kwargs and 'error' in kwargs:
            logger.error(f"Error from kwargs: {kwargs['error']}")
        elif args and len(args) > 0:
            logger.error(f"Error from args[0]: {args[0]}")
    
    def _on_transcript(self, *args, **kwargs):
        """Handle transcript event with robust error handling."""
        try:
            logger.debug(f"Transcript callback received - args: {len(args)}, kwargs keys: {kwargs.keys()}")
            
            result = None
            # Check different possible locations for the transcript data
            if 'result' in kwargs:
                result = kwargs['result']
                logger.debug("Found result in kwargs['result']")
            elif args and len(args) > 1:
                result = args[1]
                logger.debug("Found result in args[1]")
            elif args and len(args) > 0 and hasattr(args[0], 'result'):
                result = args[0].result
                logger.debug("Found result in args[0].result")
            
            if not result:
                logger.warning("No result found in transcript callback")
                return
            
            logger.debug(f"Result type: {type(result)}")
            
            # First try object-style access
            try:
                if hasattr(result, 'channel') and hasattr(result.channel, 'alternatives'):
                    alternatives = result.channel.alternatives
                    if alternatives and len(alternatives) > 0 and hasattr(alternatives[0], 'transcript'):
                        text = alternatives[0].transcript.strip()
                        is_final = getattr(result, 'is_final', False)
                        
                        if text:
                            self.received_transcripts.append((text, is_final))
                            tag = "FINAL" if is_final else "Interim"
                            logger.info(f"{tag}: {text}")
                            return
                    else:
                        logger.debug("No transcript in object-style alternatives")
            except Exception as e:
                logger.debug(f"Object-style access failed: {e}")
            
            # Then try dictionary-style access
            try:
                if isinstance(result, dict):
                    if "channel" in result and "alternatives" in result["channel"]:
                        alternatives = result["channel"]["alternatives"]
                        if alternatives and "transcript" in alternatives[0]:
                            text = alternatives[0]["transcript"].strip()
                            is_final = result.get("is_final", False)
                            
                            if text:
                                self.received_transcripts.append((text, is_final))
                                tag = "FINAL" if is_final else "Interim"
                                logger.info(f"{tag}: {text}")
                                return
                    else:
                        logger.debug("No channel or alternatives in dict-style result")
            except Exception as e:
                logger.debug(f"Dict-style access failed: {e}")
            
            # Log the data structure for debugging
            logger.warning(f"Could not extract transcript from result: {str(result)[:200]}")
            
        except Exception as e:
            logger.error(f"Error processing transcript: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def process_audio_thread(self):
        """Process audio data from the queue with robust error handling."""
        logger.info("Audio processing thread started")
        chunks_processed = 0
        errors = 0
        start_time = time.time()
        
        try:
            while self.thread_running:
                try:
                    # Get audio data with timeout
                    audio_data = self.audio_queue.get(timeout=0.1)
                    
                    # Convert to bytes
                    audio_bytes = audio_data.tobytes()
                    
                    # Send to Deepgram
                    if self.connection:
                        try:
                            self.connection.send(audio_bytes)
                            errors = 0  # Reset error count on success
                        except Exception as e:
                            errors += 1
                            logger.error(f"Error sending audio: {e}")
                            if errors > 5:
                                logger.error("Too many errors, stopping audio processing")
                                break
                    else:
                        logger.warning("No active connection to send audio")
                        time.sleep(0.5)
                    
                    chunks_processed += 1
                    if chunks_processed % 10 == 0:
                        elapsed = time.time() - start_time
                        rate = chunks_processed / elapsed if elapsed > 0 else 0
                        logger.info(f"Processed {chunks_processed} chunks ({rate:.1f} chunks/sec)")
                        
                except queue.Empty:
                    # Queue is empty, just continue
                    pass
                except Exception as e:
                    logger.error(f"Error in audio processing loop: {e}")
                    errors += 1
                    if errors > 5:
                        logger.error("Too many errors, stopping audio processing")
                        break
        
        except Exception as e:
            logger.error(f"Fatal error in audio processing thread: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            logger.info(f"Audio processing thread exited. Processed {chunks_processed} chunks.")
    
    def audio_callback(self, indata, frames, time_info, status):
        """Process audio from the microphone."""
        if status:
            logger.warning(f"Audio status: {status}")
        
        # Add to queue if recording
        if self.is_recording:
            self.audio_queue.put(indata.copy())
        
        return (indata, 0)  # 0 = paContinue
    
    async def start_recording(self):
        """Start audio recording with detailed device info."""
        logger.info(f"Starting audio recording (rate={SAMPLE_RATE}Hz, channels={CHANNELS})")
        
        # Print detailed info about available audio devices
        devices = sd.query_devices()
        logger.info("All available audio devices:")
        for i, device in enumerate(devices):
            logger.info(f"  {i}: {device['name']} (in: {device['max_input_channels']}, out: {device['max_output_channels']})")
        
        input_devices = [d for d in devices if d['max_input_channels'] > 0]
        logger.info("Available input devices:")
        for i, device in enumerate(input_devices):
            logger.info(f"  {i}: {device['name']} (channels: {device['max_input_channels']})")
        
        try:
            # Start the processing thread first
            logger.info("Starting audio processing thread")
            self.thread_running = True
            self.processing_thread = threading.Thread(target=self.process_audio_thread)
            self.processing_thread.daemon = True
            self.processing_thread.start()
            
            # Then start recording
            logger.info("Opening audio input stream")
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                blocksize=BLOCKSIZE,
                dtype=DTYPE,
                callback=self.audio_callback
            )
            
            logger.info("Starting audio stream")
            self.stream.start()
            self.is_recording = True
            logger.info("✅ Audio recording started")
        except Exception as e:
            logger.error(f"Error starting audio recording: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.is_recording = False
            self.thread_running = False
            raise
    
    async def stop_recording(self):
        """Stop audio recording."""
        logger.info("Stopping audio recording")
        
        # First stop the stream
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
                self.stream = None
                logger.info("Audio stream closed successfully")
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
        
        self.is_recording = False
        logger.info("Audio recording stopped")
        
        # Then stop the processing thread
        if self.thread_running:
            self.thread_running = False
            if self.processing_thread and self.processing_thread.is_alive():
                logger.info("Waiting for audio processing thread to terminate")
                self.processing_thread.join(timeout=2.0)
                if self.processing_thread.is_alive():
                    logger.warning("Processing thread did not terminate cleanly")
                else:
                    logger.info("Audio processing thread terminated")
    
    async def cleanup(self):
        """Clean up resources."""
        logger.info("Cleaning up resources")
        
        # Stop recording if still active
        if self.is_recording:
            await self.stop_recording()
        
        # Close Deepgram connection
        if self.connection:
            try:
                logger.info("Closing Deepgram connection")
                self.connection.finish()
                await asyncio.sleep(0.5)  # Wait for clean closure
                logger.info("Deepgram connection closed")
            except Exception as e:
                logger.error(f"Error closing Deepgram connection: {e}")
        
        # Print summary
        if self.received_transcripts:
            logger.info("\nTranscription Summary:")
            logger.info(f"  Total segments: {len(self.received_transcripts)}")
            final_segments = sum(1 for _, is_final in self.received_transcripts if is_final)
            logger.info(f"  Final segments: {final_segments}")
            
            # Show all final transcripts concatenated
            final_text = " ".join([text for text, is_final in self.received_transcripts if is_final])
            logger.info(f"\nCombined transcript:\n{final_text}")
        else:
            logger.warning("No transcripts were received during the test")

async def main():
    """Run the Deepgram test with robust error handling."""
    # Load environment variables
    load_dotenv()
    
    # Get Deepgram API key
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        logger.error("DEEPGRAM_API_KEY environment variable not set")
        return
    
    logger.info(f"Using Deepgram API key: {api_key[:5]}...{api_key[-5:]}")
    
    # Create and run test
    tester = DeepgramTester(api_key)
    
    try:
        # Set up Deepgram
        logger.info("=== Starting Deepgram setup ===")
        await tester.setup_connection()
        
        # Wait for connection to be established
        logger.info("Waiting for connection to establish...")
        await asyncio.sleep(1)
        
        # Start recording
        logger.info("=== Starting audio recording ===")
        await tester.start_recording()
        
        # Run test for 30 seconds
        logger.info("\n=== Speak into your microphone to test transcription ===\n")
        
        test_duration = 30
        for i in range(test_duration):
            await asyncio.sleep(1)
            if i % 5 == 0:
                logger.info(f"Test running: {i+1}/{test_duration} seconds...")
                logger.info(f"Received {len(tester.received_transcripts)} transcript segments so far")
        
        # Clean up
        logger.info("=== Test completed, cleaning up ===")
        await tester.stop_recording()
        
        # Final cleanup
        await tester.cleanup()
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.info("=== Test completed ===")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n=== Test interrupted by user ===")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc()) 