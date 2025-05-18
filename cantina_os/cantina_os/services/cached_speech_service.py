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
from typing import Dict, Optional, Any
from dataclasses import dataclass
from collections import OrderedDict
import sounddevice as sd
import numpy as np
from pydantic import BaseModel, Field

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    ServiceStatus,
    LogLevel,
    SpeechCacheRequestPayload,
    SpeechCacheReadyPayload,
    SpeechCacheErrorPayload
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
    """Service for caching and managing pre-rendered speech audio."""

    def __init__(self, event_bus, config=None, name="cached_speech_service"):
        """Initialize the service with proper event bus and config."""
        super().__init__(service_name=name, event_bus=event_bus)
        
        # Convert config dict to Pydantic model
        self._config = CachedSpeechServiceConfig(**(config or {}))
        
        # Initialize cache as OrderedDict for LRU functionality
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._cache_lock = threading.Lock()
        
        # Audio thread management
        self._audio_thread = None
        self._audio_queue = asyncio.Queue(maxsize=100)
        self._stop_flag = threading.Event()
        
        # Store event loop for thread-safe operations
        self._event_loop = None
        
        # Track active operations
        self._active_requests: Dict[str, asyncio.Event] = {}
        self._tasks = []

    async def _start(self) -> None:
        """Start the service and initialize resources."""
        self._event_loop = asyncio.get_running_loop()
        await self._setup_subscriptions()
        
        # Start cache cleanup task
        cleanup_task = asyncio.create_task(self._cache_cleanup_loop())
        self._tasks.append(cleanup_task)
        
        await self.emit(
            EventTopics.SERVICE_STATUS,
            {
                "service_name": self.name,
                "status": ServiceStatus.RUNNING,
                "message": "CachedSpeechService started successfully"
            }
        )
        self.logger.info("CachedSpeechService started successfully")

    async def stop(self) -> None:
        """Stop the service and clean up resources."""
        self.logger.info("Stopping CachedSpeechService")
        
        # Signal audio thread to stop
        self._stop_flag.set()
        
        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                
        # Wait for tasks to complete with timeout
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5.0)
        
        # Clear cache
        with self._cache_lock:
            self._cache.clear()
        
        await self.emit(
            EventTopics.SERVICE_STATUS,
            {
                "service_name": self.name,
                "status": ServiceStatus.STOPPED,
                "message": "CachedSpeechService stopped successfully"
            }
        )
        self.logger.info("CachedSpeechService stopped successfully")

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        await self.subscribe(
            EventTopics.SPEECH_CACHE_REQUEST,
            self._handle_cache_request
        )
        
        await self.subscribe(
            EventTopics.SPEECH_CACHE_CLEANUP,
            self._handle_cache_cleanup
        )
        
        await self.subscribe(
            EventTopics.SPEECH_CACHE_PLAYBACK_REQUEST,
            self._handle_playback_request
        )
        
        await self.subscribe(
            EventTopics.TTS_AUDIO_DATA,
            self._handle_tts_audio_data
        )

    async def _handle_cache_request(self, payload: Dict[str, Any]) -> None:
        """Handle a request to cache speech audio."""
        try:
            request = SpeechCacheRequestPayload(**payload)
            
            # Check if already cached
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
            
            # Add to cache with LRU management
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
        with self._cache_lock:
            # Remove oldest entries if at capacity
            while len(self._cache) >= self._config.max_cache_entries:
                self._cache.popitem(last=False)
            
            # Add new entry
            self._cache[key] = entry

    def _get_cached_entry(self, key: str) -> Optional[CacheEntry]:
        """Get a cached entry with thread safety."""
        with self._cache_lock:
            if key in self._cache:
                entry = self._cache[key]
                # Update access time and move to end (most recently used)
                entry.last_access = time.time()
                self._cache.move_to_end(key)
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
                with self._cache_lock:
                    for key in keys:
                        if key in self._cache:
                            del self._cache[key]
                    self.logger.info(f"Cleaned up {len(keys)} specific cache entries")
                return
            
            # Check for max age cleanup
            max_age_seconds = payload.get("max_age_seconds")
            if max_age_seconds is not None:
                await self._cleanup_expired_entries(max_age_seconds)
                return
            
            # If no specific cleanup parameters, clean up everything
            with self._cache_lock:
                cache_size = len(self._cache)
                self._cache.clear()
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
        
        with self._cache_lock:
            # Create list of expired keys
            expired_keys = [
                key for key, entry in self._cache.items()
                if now - entry.creation_time > ttl
            ]
            
            # Remove expired entries
            for key in expired_keys:
                del self._cache[key]
                
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
                    # Unsubscribe after receiving the response
                    await self.unsubscribe(EventTopics.TTS_AUDIO_DATA, handle_tts_response)
            
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
                
            # Check if we have this in cache
            cached_entry = self._get_cached_entry(cache_key)
            if not cached_entry:
                self.logger.error(f"Cache entry not found for key: {cache_key}")
                await self.emit(
                    EventTopics.SPEECH_CACHE_ERROR,
                    {
                        "cache_key": cache_key,
                        "error": "Cache entry not found",
                        "operation": "playback"
                    }
                )
                return
                
            # Play the audio
            self.logger.info(f"Playing cached speech for key: {cache_key}")
            asyncio.create_task(self._play_audio(cached_entry, payload))
            
        except Exception as e:
            self.logger.error(f"Error handling playback request: {e}")
            await self.emit(
                EventTopics.SPEECH_CACHE_ERROR,
                {
                    "cache_key": payload.get("cache_key", "unknown"),
                    "error": str(e),
                    "operation": "playback"
                }
            )
            
    async def _play_audio(self, entry: CacheEntry, payload: Dict[str, Any]) -> None:
        """Play audio from a cache entry.
        
        Args:
            entry: The cache entry containing audio data
            payload: Original playback request payload with parameters
        """
        try:
            # Use sounddevice to play audio
            request_id = str(uuid.uuid4())
            
            # Emit playback started event
            await self.emit(
                EventTopics.SPEECH_CACHE_PLAYBACK_STARTED,
                {
                    "cache_key": payload.get("cache_key"),
                    "request_id": request_id,
                    "duration_ms": entry.duration_ms,
                    "metadata": entry.metadata
                }
            )
            
            # Play the audio
            sd.play(entry.audio_data, entry.sample_rate)
            sd.wait()  # Wait for playback to complete
            
            # Emit playback completed event
            await self.emit(
                EventTopics.SPEECH_CACHE_PLAYBACK_COMPLETED,
                {
                    "cache_key": payload.get("cache_key"),
                    "request_id": request_id,
                    "metadata": entry.metadata
                }
            )
            
        except Exception as e:
            self.logger.error(f"Error playing cached audio: {e}")
            await self.emit(
                EventTopics.SPEECH_CACHE_ERROR,
                {
                    "cache_key": payload.get("cache_key", "unknown"),
                    "error": str(e),
                    "operation": "playback"
                }
            )

    async def _handle_tts_audio_data(self, payload: Dict[str, Any]) -> None:
        """Handle TTS audio data received from ElevenLabs service.
        
        This method processes audio data coming from the ElevenLabs service
        in response to a TTS_REQUEST event.
        
        Args:
            payload: The TTS audio data payload
        """
        try:
            request_id = payload.get("request_id")
            if not request_id:
                self.logger.warning("Received TTS audio data without request_id")
                return
                
            # Check if we have a pending request for this ID
            if request_id in self._active_requests:
                # Get the future and resolve it with the payload
                future = self._active_requests[request_id]
                if not future.done():
                    self.logger.info(f"Resolving TTS request future for request_id: {request_id}")
                    future.set_result(payload)
                else:
                    self.logger.warning(f"Future for request_id {request_id} already resolved")
            else:
                self.logger.warning(f"Received TTS audio data for unknown request_id: {request_id}")
                
        except Exception as e:
            self.logger.error(f"Error handling TTS audio data: {e}") 