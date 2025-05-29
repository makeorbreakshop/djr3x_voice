"""
CachedSpeechService for DJ R3X

This service handles pre-rendering and caching of speech for precise timing control
in DJ mode transitions. It follows the audio threading standards and provides
lookahead caching capabilities.
"""

import asyncio
import logging
import threading
import time
import uuid
from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from collections import OrderedDict
import sounddevice as sd
import numpy as np
from pydantic import BaseModel, Field, ValidationError

from ..base_service import BaseService
from cantina_os.core.event_topics import EventTopics
from ..event_payloads import (
    ServiceStatus,
    LogLevel,
    SpeechCacheRequestPayload,
    SpeechCacheReadyPayload,
    SpeechCacheErrorPayload,
    SpeechCacheUpdatedPayload,
    SpeechCacheHitPayload,
    SpeechCacheClearedPayload,
    SpeechCachePlaybackCompletedPayload
)

class CacheEntry:
    """Represents a cached speech entry with metadata."""
    def __init__(
        self,
        audio_data: np.ndarray,
        sample_rate: int,
        duration_ms: int,
        metadata: Dict[str, Any],
        creation_time: float = None
    ):
        self.audio_data = audio_data
        self.sample_rate = sample_rate
        self.duration_ms = duration_ms
        self.metadata = metadata
        self.creation_time = creation_time or time.time()
        self.last_access = self.creation_time

class CachedSpeechServiceConfig(BaseModel):
    """Configuration for CachedSpeechService."""
    max_cache_entries: int = Field(default=10, description="Maximum number of cached entries")
    max_cache_size_mb: int = Field(default=100, description="Maximum cache size in megabytes")
    default_ttl_seconds: int = Field(default=300, description="Default time-to-live for cached entries")
    cache_cleanup_interval: int = Field(default=60, description="Cache cleanup interval in seconds")
    audio_device: Optional[str] = Field(default=None, description="Audio device to use")
    sample_rate: int = Field(default=44100, description="Audio sample rate")

class CachedSpeechService(BaseService):
    """Service for caching and managing speech audio data."""
    
    def __init__(self, event_bus, config=None, name="cached_speech_service"):
        """Initialize the service with proper event bus and config.
        
        Args:
            event_bus: The event bus to use
            config: Optional config dict
            name: Service name, defaults to "cached_speech_service"
        """
        super().__init__(service_name=name, event_bus=event_bus)
        
        # Store name as property for access from other methods
        self.name = name
        
        # Convert config dict to Pydantic model
        self._config = CachedSpeechServiceConfig(**(config or {}))
        
        # Initialize cache
        self._speech_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._tasks: List[asyncio.Task] = [] # Initialize list to hold background tasks
        self._active_requests: Dict[str, asyncio.Future] = {}  # Track active TTS requests
        
    async def _handle_tts_audio_data(self, payload: Dict[str, Any]) -> None:
        """Handle TTS audio data from ElevenLabsService.
        
        Args:
            payload: Dict containing audio data and metadata
        """
        try:
            request_id = payload.get("request_id")
            if not request_id:
                self.logger.warning("Received TTS audio data without request ID")
                return
                
            success = payload.get("success", False)
            if not success:
                self.logger.warning(f"TTS request {request_id} failed")
                return
                
            audio_data = payload.get("audio_data")
            sample_rate = payload.get("sample_rate")
            
            if not audio_data or not sample_rate:
                self.logger.warning(f"Missing audio data or sample rate for request {request_id}")
                return
                
            # Cache the audio data
            async with self._cache_lock:
                self._speech_cache[request_id] = {
                    "audio_data": audio_data,
                    "sample_rate": sample_rate
                }
                
            self.logger.debug(f"Cached audio data for request {request_id}")
            
            await self.emit(
                EventTopics.SPEECH_CACHE_UPDATED,
                SpeechCacheUpdatedPayload(
                    cache_key=request_id,
                    success=True
                ).model_dump()
            )
            
        except Exception as e:
            self.logger.error(f"Error handling TTS audio data: {e}", exc_info=True)
            
    async def _handle_speech_cache_request(self, payload: Dict[str, Any]) -> None:
        """Handle request for cached speech data.
        
        Args:
            payload: Dict containing request ID
        """
        try:
            request_id = payload.get("request_id")
            if not request_id:
                self.logger.warning("Received cache request without request ID")
                return
                
            # Check if we have the requested audio in cache
            async with self._cache_lock:
                cached_data = self._speech_cache.get(request_id)
                
            if not cached_data:
                self.logger.warning(f"No cached audio found for request {request_id}")
                # Using SpeechCacheErrorPayload for cache misses as they indicate an error state
                await self.emit(
                    EventTopics.SPEECH_CACHE_MISS,
                    SpeechCacheErrorPayload(
                        cache_key=request_id,
                        error="Audio not found in cache"
                    ).model_dump()
                )
                return
                
            # TODO: Define a proper Pydantic payload (SpeechCacheHitPayload) for returning cached data.
            await self.emit(
                EventTopics.SPEECH_CACHE_HIT,
                SpeechCacheHitPayload(
                    cache_key=request_id,
                    duration_ms=cached_data.get("duration_ms"),
                    sample_rate=cached_data.get("sample_rate"),
                    metadata=cached_data.get("metadata", {})
                ).model_dump()
            )
            
        except Exception as e:
            self.logger.error(f"Error handling speech cache request: {e}", exc_info=True)
            
    async def _handle_clear_cache_request(self, payload: Dict[str, Any]) -> None:
        """Handle request to clear the speech cache.
        
        Args:
            payload: Dict containing optional request ID to clear specific entry
        """
        try:
            request_id = payload.get("request_id")
            
            async with self._cache_lock:
                if request_id:
                    # Clear specific cache entry
                    if request_id in self._speech_cache:
                        del self._speech_cache[request_id]
                        self.logger.info(f"Cleared cache entry for request {request_id}")
                else:
                    # Clear entire cache
                    self._speech_cache.clear()
                    self.logger.info("Cleared entire speech cache")
                    
            # TODO: Define a proper Pydantic payload for SPEECH_CACHE_CLEARED event.
            await self.emit(
                EventTopics.SPEECH_CACHE_CLEARED,
                SpeechCacheClearedPayload(
                    cache_key=request_id,
                    success=True
                ).model_dump()
            )
            
        except Exception as e:
            self.logger.error(f"Error handling clear cache request: {e}", exc_info=True)
            
    async def stop(self) -> None:
        """Stop the service and clean up resources."""
        try:
            # Cancel all background tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete cancellation
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
                
            # Cancel any active TTS requests
            for request_id, future in self._active_requests.items():
                if not future.done():
                    future.cancel()
            
            # Clear the cache
            async with self._cache_lock:
                self._speech_cache.clear()
                
            self.logger.info(f"Stopped {self.name}")
            
        except Exception as e:
            self.logger.error(f"Error stopping {self.name}: {e}", exc_info=True)
            raise

    async def _start(self) -> None:
        """Start the service and initialize resources."""
        self._event_loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        
        # Start cache cleanup task
        cleanup_task = asyncio.create_task(self._cache_cleanup_loop())
        self._tasks.append(cleanup_task)
        cleanup_task.add_done_callback(self._handle_task_exception) # Add exception handling
        
        await self._emit_status(
            ServiceStatus.RUNNING,
            "CachedSpeechService started successfully",
            severity=LogLevel.INFO # Explicitly set severity for clarity
        )
        self.logger.info("CachedSpeechService started successfully")

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Use asyncio.create_task for all subscriptions per architectural standards
        
        # Subscriptions already present
        asyncio.create_task(self.subscribe(
            EventTopics.SPEECH_CACHE_REQUEST,
            self._handle_cache_request
        ))
        
        asyncio.create_task(self.subscribe(
            EventTopics.SPEECH_CACHE_CLEANUP,
            self._handle_cache_cleanup
        ))
        
        asyncio.create_task(self.subscribe(
            EventTopics.SPEECH_CACHE_PLAYBACK_REQUEST,
            self._handle_playback_request
        ))
        
        asyncio.create_task(self.subscribe(
            EventTopics.TTS_AUDIO_DATA,
            self._handle_tts_audio_data
        ))

        # Add subscription from removed start method
        asyncio.create_task(self.subscribe(
            EventTopics.CLEAR_SPEECH_CACHE,
            self._handle_clear_cache_request
        ))

    async def _handle_cache_request(self, payload: Dict[str, Any]) -> None:
        """Handle a request to cache speech audio."""
        try:
            try:
                request = SpeechCacheRequestPayload(**payload)
            except ValidationError as e:
                self.logger.error(f"Validation error in cache request payload: {e}")
                await self.emit(
                    EventTopics.SPEECH_CACHE_ERROR,
                    SpeechCacheErrorPayload(
                        cache_key=payload.get("cache_key", "unknown"),
                        error=f"Validation error: {str(e)}"
                    ).model_dump()
                )
                return
            
            # Check if already cached with proper async locking
            async with self._cache_lock:
                cached_entry = self._get_cached_entry(request.cache_key)
            
            if cached_entry:
                await self._emit_cache_ready(request.cache_key, cached_entry)
                return
                
            # Start caching process
            await self._cache_speech(request)
            
        except Exception as e:
            self.logger.error(f"Error handling cache request: {e}")
            await self.emit(
                EventTopics.SPEECH_CACHE_ERROR,
                SpeechCacheErrorPayload(
                    cache_key=payload.get("cache_key", "unknown"),
                    error=str(e)
                ).model_dump()
            )

    async def _cache_speech(self, request: SpeechCacheRequestPayload) -> None:
        """Cache speech audio for the given request."""
        try:
            # Generate speech audio (implementation depends on TTS service)
            audio_data, sample_rate = await self._generate_speech_audio(request.text)
            
            # Calculate duration
            duration_ms = int((len(audio_data) / sample_rate) * 1000)
            
            # Create cache entry
            entry = CacheEntry(
                audio_data=audio_data,
                sample_rate=sample_rate,
                duration_ms=duration_ms,
                metadata=request.metadata
            )
            
            # Add to cache with proper async locking
            async with self._cache_lock:
                self._add_to_cache(request.cache_key, entry)
            
            # Emit ready event
            await self._emit_cache_ready(request.cache_key, entry)
            
        except Exception as e:
            self.logger.error(f"Error caching speech: {e}")
            await self.emit(
                EventTopics.SPEECH_CACHE_ERROR,
                SpeechCacheErrorPayload(
                    cache_key=request.cache_key,
                    error=str(e)
                ).model_dump()
            )

    def _add_to_cache(self, key: str, entry: CacheEntry) -> None:
        """Add an entry to the cache with thread safety."""
        # NOTE: This method should only be called from async contexts where proper locking is handled
        
        # Remove oldest entries if at capacity
        while len(self._speech_cache) >= self._config.max_cache_entries:
            oldest_key = next(iter(self._speech_cache))
            del self._speech_cache[oldest_key]
        
        # Add new entry as CacheEntry object (not dict)
        self._speech_cache[key] = entry

    def _get_cached_entry(self, key: str) -> Optional[CacheEntry]:
        """Get a cached entry with thread safety."""
        # NOTE: This method should only be called from async contexts where proper locking is handled
        
        if key in self._speech_cache:
            entry = self._speech_cache[key]
            # Update access time and move to end (most recently used)
            entry.last_access = time.time()
            # Move to end in OrderedDict (if we were using one) or just update access time
            return entry
        return None

    async def _cache_cleanup_loop(self) -> None:
        """Periodically clean up expired cache entries."""
        while True:
            try:
                await asyncio.sleep(self._config.cache_cleanup_interval)
                # Use the default TTL for periodic cleanup
                await self._cleanup_expired_entries()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in cache cleanup loop: {e}")

    async def _handle_cache_cleanup(self, payload: Dict[str, Any]) -> None:
        """Handle a request to clean up the speech cache.
        
        This can be used to:
        1. Clean up all cache entries
        2. Clean up entries with specific keys
        3. Clean up entries older than a specific time
        
        Args:
            payload: The cleanup request parameters
        """
        try:
            self.logger.info(f"Received cache cleanup request: {payload}")
            
            # Check for specific keys to clean up
            keys = payload.get("keys", [])
            if keys:
                async with self._cache_lock:
                    for key in keys:
                        if key in self._speech_cache:
                            del self._speech_cache[key]
                    self.logger.info(f"Cleaned up {len(keys)} specific cache entries")
                return
            
            # Check for max age cleanup
            max_age_seconds = payload.get("max_age_seconds")
            if max_age_seconds is not None:
                await self._cleanup_expired_entries(max_age_seconds)
                return
            
            # If no specific cleanup parameters, clean up everything
            async with self._cache_lock:
                cache_size = len(self._speech_cache)
                self._speech_cache.clear()
                self.logger.info(f"Cleaned up all {cache_size} cache entries")
            
        except Exception as e:
            self.logger.error(f"Error handling cache cleanup request: {e}")
            
    async def _cleanup_expired_entries(self, max_age_seconds: Optional[float] = None) -> None:
        """Clean up expired cache entries.
        
        Args:
            max_age_seconds: Optional maximum age in seconds. If None, uses the default TTL.
        """
        now = time.time()
        ttl = max_age_seconds if max_age_seconds is not None else self._config.default_ttl_seconds
        
        async with self._cache_lock:
            # Create list of expired keys
            expired_keys = []
            for key, entry in self._speech_cache.items():
                # Check if entry is a CacheEntry object or dict for backwards compatibility
                if hasattr(entry, 'creation_time'):
                    # CacheEntry object
                    if now - entry.creation_time > ttl:
                        expired_keys.append(key)
                elif isinstance(entry, dict) and 'creation_time' in entry:
                    # Dict format (backwards compatibility)
                    if now - entry['creation_time'] > ttl:
                        expired_keys.append(key)
            
            # Remove expired entries
            for key in expired_keys:
                del self._speech_cache[key]
                
            if expired_keys:
                self.logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
                
        return len(expired_keys)

    async def _emit_cache_ready(self, cache_key: str, entry: CacheEntry) -> None:
        """Emit cache ready event."""
        await self.emit(
            EventTopics.SPEECH_CACHE_READY,
            SpeechCacheReadyPayload(
                cache_key=cache_key,
                duration_ms=entry.duration_ms,
                size_bytes=entry.audio_data.nbytes,
                metadata=entry.metadata
            ).model_dump()
        )

    async def _generate_speech_audio(self, text: str) -> tuple[np.ndarray, int]:
        """Generate speech audio using ElevenLabs TTS service.
        
        This method sends a request to ElevenLabs and receives audio data,
        then processes it for caching and playback.
        
        Args:
            text: The text to convert to speech
            
        Returns:
            tuple of (audio_data, sample_rate)
        """
        try:
            self.logger.info(f"Generating speech audio for text: {text[:50]}...")
            
            # Request TTS generation via ElevenLabs service
            # We'll use the event system to request TTS and receive the audio data
            request_id = str(uuid.uuid4())
            
            # Create a future to wait for the response
            response_future = asyncio.Future()
            self._active_requests[request_id] = response_future
            
            # Subscribe to TTS response event temporarily
            async def handle_tts_response(payload):
                if payload.get("request_id") == request_id:
                    response_future.set_result(payload)
                    # No need to unsubscribe - the subscription will be cleaned up automatically
            
            await self.subscribe(EventTopics.TTS_AUDIO_DATA, handle_tts_response)
            
            # Request TTS generation
            await self.emit(
                EventTopics.TTS_REQUEST, 
                {
                    "text": text,
                    "request_id": request_id,
                    "non_streaming": True,  # We need the whole audio data for caching
                    "source": "cached_speech_service"
                }
            )
            
            # Wait for response with timeout
            try:
                payload = await asyncio.wait_for(response_future, timeout=30.0)
                
                # Process audio data
                audio_data = np.frombuffer(payload.get("audio_data"), dtype=np.float32)
                sample_rate = payload.get("sample_rate", self._config.sample_rate)
                
                self.logger.info(f"Successfully generated audio: {len(audio_data)} samples at {sample_rate}Hz")
                return audio_data, sample_rate
                
            except asyncio.TimeoutError:
                self.logger.error("Timeout waiting for TTS response")
                raise RuntimeError("Timeout waiting for TTS response")
                
            finally:
                # Clean up
                if request_id in self._active_requests:
                    del self._active_requests[request_id]
        
        except Exception as e:
            self.logger.error(f"Error generating speech audio: {e}")
            # Return empty audio as fallback (this allows the system to continue)
            sample_rate = self._config.sample_rate
            duration = 1.0  # 1 second of silence
            samples = int(duration * sample_rate)
            self.logger.warning(f"Returning fallback empty audio ({samples} samples)")
            return np.zeros(samples, dtype=np.float32), sample_rate 

    async def _handle_playback_request(self, payload: Dict[str, Any]) -> None:
        """Handle a request to play cached speech.
        
        Args:
            payload: The playback request containing cache_key and playback parameters
        """
        try:
            cache_key = payload.get("cache_key")
            if not cache_key:
                self.logger.error("Missing cache_key in playback request")
                return
                
            # Check if we have this in cache with proper async locking
            async with self._cache_lock:
                cached_entry = self._get_cached_entry(cache_key)
                
            if not cached_entry:
                self.logger.error(f"Cache entry not found for key: {cache_key}")
                await self.emit(
                    EventTopics.SPEECH_CACHE_ERROR,
                    SpeechCacheErrorPayload(
                        cache_key=cache_key,
                        error="Cache entry not found"
                    ).model_dump()
                )
                return
                
            # Play the audio
            self.logger.info(f"Playing cached speech for key: {cache_key}")
            asyncio.create_task(self._play_audio(cached_entry, payload))
            
        except Exception as e:
            self.logger.error(f"Error handling playback request: {e}")
            await self.emit(
                EventTopics.SPEECH_CACHE_ERROR,
                SpeechCacheErrorPayload(
                    cache_key=payload.get("cache_key", "unknown"),
                    error=str(e)
                ).model_dump()
            )
            
    async def _play_audio(self, entry: CacheEntry, payload: Dict[str, Any]) -> None:
        """Play audio from a cache entry.
        
        Plays audio in a separate thread to avoid blocking the asyncio event loop.
        
        Args:
            entry: The cache entry containing audio data
            payload: Original playback request payload with parameters
        """
        try:
            # Use the playback_id from the request payload instead of generating a new one
            # This ensures the completion event uses the same ID that Timeline Executor is waiting for
            playback_id = payload.get("playback_id")
            if not playback_id:
                # Fallback to generating new UUID if not provided, but log warning
                playback_id = str(uuid.uuid4())
                self.logger.warning(f"No playback_id in request payload, generated fallback: {playback_id}")
            
            cache_key = payload.get("cache_key")
            
            # Emit playback started event
            await self.emit(
                EventTopics.SPEECH_CACHE_PLAYBACK_STARTED,
                {
                    "cache_key": cache_key,
                    "playback_id": playback_id,  # Use the same playback_id from request
                    "duration_ms": entry.duration_ms,
                    "metadata": entry.metadata
                }
            )
            
            # Define the blocking playback function to run in the executor
            def blocking_playback():
                try:
                    sd.play(entry.audio_data, entry.sample_rate)
                    sd.wait()  # Wait for playback to complete
                except Exception as e:
                    # Log the error within the thread
                    self.logger.error(f"Error during blocking audio playback: {e}")
                    # Re-raise the exception so run_in_executor can propagate it
                    raise
            
            # Run the blocking playback function in the thread pool
            await self._event_loop.run_in_executor(None, blocking_playback)
            
            # Emit playback completed event after successful playback in the thread
            # CRITICAL FIX: Use the same playback_id that was in the original request
            await self.emit(
                EventTopics.SPEECH_CACHE_PLAYBACK_COMPLETED,
                SpeechCachePlaybackCompletedPayload(
                    timestamp=time.time(),
                    cache_key=cache_key,
                    playback_id=playback_id,  # Use the same playback_id from request
                    completion_status="completed",
                    metadata=entry.metadata
                ).model_dump()
            )
            
        except Exception as e:
            self.logger.error(f"Error handling playback task in _play_audio: {e}")
            # Emit error event with the same playback_id for consistency
            playback_id = payload.get("playback_id", "unknown")
            await self.emit(
                EventTopics.SPEECH_CACHE_PLAYBACK_COMPLETED,
                SpeechCachePlaybackCompletedPayload(
                    timestamp=time.time(),
                    cache_key=payload.get("cache_key", "unknown"),
                    playback_id=playback_id,
                    completion_status="error",
                    error=str(e),
                    metadata={}
                ).model_dump()
            )

    def _handle_task_exception(self, task: asyncio.Task) -> None:
        """Handle exceptions raised by background tasks."""
        try:
            exception = task.exception()
            if exception:
                self.logger.error(f"Background task failed with exception: {exception}")
                # Use asyncio.create_task to emit status asynchronously
                asyncio.create_task(self._emit_status(
                    ServiceStatus.ERROR,
                    f"Background task error: {exception}",
                    severity=LogLevel.ERROR
                ))
        except asyncio.CancelledError:
            pass # Task was cancelled, this is expected during shutdown 