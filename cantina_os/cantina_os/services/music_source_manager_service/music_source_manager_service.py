"""
Music Source Manager Service for CantinaOS
===========================================

A service that manages different music sources (local files, Spotify, etc.) and provides
a unified interface for music discovery, queuing, and playback coordination.

This service acts as an abstraction layer between music commands and the actual
music providers, enabling seamless switching between local and streaming sources.

Features:
- Multi-provider support (local, Spotify)
- Automatic fallback between providers
- Unified music discovery interface
- Provider-specific configuration management
- Event-driven provider coordination
- Provider health monitoring and automatic failover
- Unified track search and library management
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

from ...base_service import BaseService

if TYPE_CHECKING:
    from ..music_controller_service import MusicControllerService
from ...core.event_topics import EventTopics
from ...event_payloads import ServiceStatus, LogLevel
from .providers.base_provider import MusicProvider, ProviderStatus
from .providers.models import Track, TrackSearchResult
from .providers.local_music_provider import LocalMusicProvider

# Optional Spotify provider import
try:
    from .providers.spotify_music_provider import SpotifyMusicProvider

    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False


class MusicSourceManagerService(BaseService):
    """
    Music Source Manager Service for CantinaOS.

    Manages multiple music providers and provides a unified interface for
    music operations across different sources (local files, Spotify, etc.).

    Key Features:
    - Provider registry and lifecycle management
    - Unified music search across all providers
    - Automatic provider health monitoring
    - Intelligent fallback between providers
    - Event-driven provider coordination
    - Compatibility with existing MusicControllerService

    Dependencies:
    - MusicControllerService: For actual playback operations
    - Event bus: For inter-service communication

    Event Subscriptions:
    - MUSIC_COMMAND: Music control commands that need provider resolution
    - MUSIC_SEARCH: Unified search requests across providers

    Event Emissions:
    - MUSIC_SEARCH_RESULTS: Aggregated search results from providers
    - MUSIC_PROVIDER_STATUS: Provider health and availability updates
    - SERVICE_STATUS_UPDATE: Service health and status updates
    
    Direct Method Calls:
    - Routes music commands directly to MusicControllerService methods instead of re-emitting events
    """

    class _Config(BaseModel):
        """Pydantic configuration model for MusicSourceManagerService."""

        default_provider: str = Field(
            default="local", description="Default music provider"
        )
        enable_spotify: bool = Field(
            default=False, description="Enable Spotify integration"
        )
        spotify_config: Optional[Dict[str, Any]] = Field(
            default=None, description="Spotify configuration"
        )
        fallback_enabled: bool = Field(
            default=True, description="Enable auto-fallback between providers"
        )
        local_music_directory: str = Field(
            default="./music", description="Local music directory path"
        )
        provider_timeout: int = Field(
            default=30, description="Provider operation timeout in seconds"
        )
        max_retries: int = Field(
            default=3, description="Maximum retry attempts for provider operations"
        )
        health_check_interval: int = Field(
            default=300, description="Provider health check interval in seconds"
        )
        search_all_providers: bool = Field(
            default=True, description="Search all providers or just active one"
        )
        max_search_results: int = Field(
            default=50, description="Maximum search results per provider"
        )

    def __init__(self, event_bus, config=None, name="music_source_manager_service", music_controller_service: Optional["MusicControllerService"] = None):
        """
        Initialize the Music Source Manager Service.

        Args:
            event_bus: Event bus instance for service communication
            config: Optional configuration dictionary
            name: Service name for logging and identification
            music_controller_service: Optional MusicControllerService instance for direct calls
        """
        super().__init__(service_name=name, event_bus=event_bus)

        # Validate and store configuration
        try:
            self._config = self._Config(**(config or {}))
        except Exception as e:
            self.logger.error(f"Invalid configuration: {e}")
            # Use default configuration on validation error
            self._config = self._Config()

        # Store music controller service reference for direct calls
        self._music_controller_service: Optional["MusicControllerService"] = music_controller_service
        
        # Initialize provider registry
        self._providers: Dict[str, MusicProvider] = {}
        self._provider_configs: Dict[str, Dict[str, Any]] = {}
        self._current_provider: Optional[str] = None
        self._provider_status: Dict[str, ProviderStatus] = {}

        # Initialize background tasks
        self._tasks: List[asyncio.Task] = []
        self._health_check_task: Optional[asyncio.Task] = None

        # Cache for aggregated library
        self._aggregated_library: List[Track] = []
        self._library_last_updated: Optional[float] = None
        self._library_cache_duration = 300  # 5 minutes

        self.logger.info(
            f"Initialized with default provider: {self._config.default_provider}"
        )

    async def _start(self) -> None:
        """
        Start the Music Source Manager Service.

        Initializes providers, sets up event subscriptions, configures
        provider health monitoring, and prepares the service for operation.
        """
        try:
            # Set up event subscriptions first to avoid race conditions
            await self._setup_subscriptions()

            # Register and initialize music providers
            await self._register_providers()
            await self._initialize_providers()

            # Set initial active provider
            await self._select_initial_provider()

            # Start background health monitoring
            await self._start_health_monitoring()

            # Build initial aggregated library
            await self._update_aggregated_library()

            # Emit service ready status
            await self._emit_status(
                ServiceStatus.RUNNING,
                f"Music Source Manager Service started with {len(self._providers)} providers",
                severity=LogLevel.INFO,
            )

        except Exception as e:
            self.logger.error(f"Failed to start service: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Service startup failed: {e}",
                severity=LogLevel.ERROR,
            )
            raise

    async def _stop(self) -> None:
        """
        Stop the Music Source Manager Service.

        Performs cleanup of provider resources, cancels background tasks,
        and ensures graceful shutdown of all components.
        """
        try:
            # Stop health monitoring
            if self._health_check_task:
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass
                self._health_check_task = None

            # Cancel all background tasks
            for task in self._tasks:
                if not task.done():
                    task.cancel()

            # Wait for tasks to complete with timeout
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

            # Clear task list
            self._tasks.clear()

            # Clean up all providers
            await self._cleanup_providers()

            # Reset internal state
            self._providers.clear()
            self._provider_configs.clear()
            self._provider_status.clear()
            self._current_provider = None
            self._aggregated_library.clear()

            await self._emit_status(
                ServiceStatus.STOPPED,
                "Music Source Manager Service stopped",
                severity=LogLevel.INFO,
            )

        except Exception as e:
            self.logger.error(f"Error during service stop: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error during shutdown: {e}",
                severity=LogLevel.ERROR,
            )

    async def _setup_subscriptions(self) -> None:
        """
        Set up event subscriptions for the service.

        Subscribes to music commands and search requests to provide
        unified provider management.
        """
        try:
            await asyncio.gather(
                self.subscribe(EventTopics.MUSIC_COMMAND, self._handle_music_command),
                self.subscribe(
                    EventTopics.SPOTIFY_COMMAND, self._handle_spotify_command
                ),
                # Add subscription for unified search if the event exists
                # self.subscribe(EventTopics.MUSIC_SEARCH, self._handle_music_search)
            )
            self.logger.debug("Event subscriptions established")
        except Exception as e:
            self.logger.error(f"Failed to set up subscriptions: {e}")
            raise

    async def _register_providers(self) -> None:
        """
        Register all configured music providers.

        Sets up provider configurations but does not initialize them yet.
        """
        try:
            # Register local provider (always available)
            local_config = {
                "music_directory": self._config.local_music_directory,
                "supported_formats": [".mp3", ".wav", ".m4a", ".flac"],
                "recursive_scan": True,
                "cache_metadata": True,
                "auto_refresh_minutes": 60,
            }
            self._provider_configs["local"] = local_config
            self.logger.info("Registered local music provider")

            # Register Spotify provider if enabled and available
            if self._config.enable_spotify and self._config.spotify_config:
                if SPOTIFY_AVAILABLE:
                    self._provider_configs["spotify"] = self._config.spotify_config
                    self.logger.info("Registered Spotify provider")
                else:
                    self.logger.warning(
                        "Spotify provider requested but spotipy library not available"
                    )

            self.logger.info(f"Registered {len(self._provider_configs)} providers")

        except Exception as e:
            self.logger.error(f"Provider registration failed: {e}")
            raise

    async def _initialize_providers(self) -> None:
        """
        Initialize all registered music providers.

        Creates provider instances and calls their initialize() methods.
        """
        try:
            initialization_results = []

            for provider_name, config in self._provider_configs.items():
                try:
                    self.logger.info(f"Initializing provider: {provider_name}")

                    # Create provider instance
                    provider = await self._create_provider(provider_name, config)
                    if not provider:
                        continue

                    # Initialize the provider
                    success = await provider.initialize()
                    if success:
                        self._providers[provider_name] = provider
                        self._provider_status[provider_name] = (
                            await provider.get_status()
                        )
                        initialization_results.append(f"{provider_name}: success")
                        self.logger.info(
                            f"Successfully initialized provider: {provider_name}"
                        )
                    else:
                        initialization_results.append(f"{provider_name}: failed")
                        self.logger.warning(
                            f"Failed to initialize provider: {provider_name}"
                        )

                except Exception as e:
                    initialization_results.append(f"{provider_name}: error - {e}")
                    self.logger.error(
                        f"Error initializing provider {provider_name}: {e}"
                    )

            self.logger.info(
                f"Provider initialization complete: {', '.join(initialization_results)}"
            )

        except Exception as e:
            self.logger.error(f"Provider initialization failed: {e}")
            # Continue with any successfully initialized providers

    async def _create_provider(
        self, provider_name: str, config: Dict[str, Any]
    ) -> Optional[MusicProvider]:
        """
        Create a provider instance based on its type.

        Args:
            provider_name: Name of the provider to create
            config: Provider configuration

        Returns:
            Optional[MusicProvider]: Provider instance or None if creation failed
        """
        try:
            if provider_name == "local":
                return LocalMusicProvider(config, self._event_bus, self._music_controller_service)
            elif provider_name == "spotify":
                if SPOTIFY_AVAILABLE:
                    return SpotifyMusicProvider(config, self._event_bus, self._music_controller_service)
                else:
                    self.logger.error(
                        "Spotify provider requested but spotipy library not available"
                    )
                    return None
            else:
                self.logger.error(f"Unknown provider type: {provider_name}")
                return None

        except Exception as e:
            self.logger.error(f"Failed to create provider {provider_name}: {e}")
            return None

    async def _select_initial_provider(self) -> None:
        """
        Select the initial active provider based on configuration and availability.
        """
        try:
            available_providers = [
                name
                for name, provider in self._providers.items()
                if provider.is_available
            ]

            if not available_providers:
                self.logger.error("No music providers available")
                await self._emit_status(
                    ServiceStatus.DEGRADED,
                    "No music providers available",
                    severity=LogLevel.WARNING,
                )
                return

            # Try default provider first
            if self._config.default_provider in available_providers:
                self._current_provider = self._config.default_provider
                self.logger.info(f"Using default provider: {self._current_provider}")
                return

            # Fallback to first available provider
            self._current_provider = available_providers[0]
            self.logger.info(
                f"Default provider not available, using: {self._current_provider}"
            )

        except Exception as e:
            self.logger.error(f"Failed to select initial provider: {e}")

    async def _start_health_monitoring(self) -> None:
        """
        Start background health monitoring for all providers.
        """
        if self._config.health_check_interval <= 0:
            self.logger.info("Health monitoring disabled")
            return

        async def health_monitor_loop():
            while True:
                try:
                    await asyncio.sleep(self._config.health_check_interval)
                    await self._check_provider_health()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"Health monitoring error: {e}")

        self._health_check_task = asyncio.create_task(health_monitor_loop())
        self.logger.info(
            f"Started health monitoring: every {self._config.health_check_interval}s"
        )

    async def _check_provider_health(self) -> None:
        """
        Check health of all registered providers and update status.
        """
        try:
            health_changes = []

            for provider_name, provider in self._providers.items():
                try:
                    await provider.is_provider_available()
                    old_status = self._provider_status.get(provider_name)
                    new_status = await provider.get_status()

                    # Update status
                    self._provider_status[provider_name] = new_status

                    # Log significant changes
                    if (
                        old_status
                        and old_status.is_available != new_status.is_available
                    ):
                        status_str = (
                            "available" if new_status.is_available else "unavailable"
                        )
                        health_changes.append(f"{provider_name}: {status_str}")

                        # Switch providers if current provider becomes unavailable
                        if (
                            provider_name == self._current_provider
                            and not new_status.is_available
                        ):
                            await self._switch_to_fallback_provider()

                except Exception as e:
                    self.logger.warning(f"Health check failed for {provider_name}: {e}")

            if health_changes:
                self.logger.info(
                    f"Provider health changes: {', '.join(health_changes)}"
                )
                # Emit provider status update event
                await self._emit_provider_status_update()

        except Exception as e:
            self.logger.error(f"Provider health check failed: {e}")

    async def _switch_to_fallback_provider(self) -> None:
        """
        Switch to a fallback provider when current provider becomes unavailable.
        """
        try:
            available_providers = [
                name
                for name, status in self._provider_status.items()
                if status.is_available and name != self._current_provider
            ]

            if available_providers:
                old_provider = self._current_provider
                self._current_provider = available_providers[0]
                self.logger.warning(
                    f"Switched from unavailable provider {old_provider} to {self._current_provider}"
                )

                await self._emit_status(
                    ServiceStatus.DEGRADED,
                    f"Switched to fallback provider: {self._current_provider}",
                    severity=LogLevel.WARNING,
                )
            else:
                self.logger.error("No fallback providers available")
                self._current_provider = None

                await self._emit_status(
                    ServiceStatus.ERROR,
                    "All music providers unavailable",
                    severity=LogLevel.ERROR,
                )

        except Exception as e:
            self.logger.error(f"Failed to switch to fallback provider: {e}")

    async def _update_aggregated_library(self) -> None:
        """
        Update the aggregated music library from all providers.
        """
        try:
            start_time = time.time()
            all_tracks = []

            for provider_name, provider in self._providers.items():
                try:
                    if provider.is_available:
                        provider_tracks = await provider.get_library()
                        all_tracks.extend(provider_tracks)
                        self.logger.debug(
                            f"Added {len(provider_tracks)} tracks from {provider_name}"
                        )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to get library from {provider_name}: {e}"
                    )

            # Remove duplicates based on track_id and provider
            unique_tracks = {}
            for track in all_tracks:
                key = f"{track.provider}:{track.track_id}"
                unique_tracks[key] = track

            self._aggregated_library = list(unique_tracks.values())
            self._library_last_updated = time.time()

            duration = time.time() - start_time
            self.logger.info(
                f"Updated aggregated library: {len(self._aggregated_library)} tracks "
                f"from {len(self._providers)} providers in {duration:.2f}s"
            )

        except Exception as e:
            self.logger.error(f"Failed to update aggregated library: {e}")

    async def _cleanup_providers(self) -> None:
        """
        Clean up all provider resources.
        """
        try:
            cleanup_tasks = []
            for provider_name, provider in self._providers.items():
                self.logger.debug(f"Cleaning up provider: {provider_name}")
                cleanup_tasks.append(provider.cleanup())

            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        except Exception as e:
            self.logger.error(f"Error cleaning up providers: {e}")

    async def _handle_music_command(self, payload: Dict[str, Any]) -> None:
        """
        Handle incoming music commands with provider context.

        Enhances commands with provider information and routes them
        to the appropriate provider for processing.

        Args:
            payload: The event payload containing command details
        """
        try:
            self.logger.debug(f"Handling music command: {payload}")

            # Add provider context to command
            enhanced_payload = payload.copy()
            enhanced_payload["provider"] = self._current_provider
            enhanced_payload["available_providers"] = list(self._providers.keys())
            enhanced_payload["provider_status"] = {
                name: status.model_dump()
                for name, status in self._provider_status.items()
            }

            # For search commands, perform unified search
            action = payload.get("action", "")
            if action == "search" and "query" in payload:
                await self._handle_unified_search(payload["query"], enhanced_payload)
                return

            # Route command directly to MusicControllerService if available
            if self._music_controller_service:
                try:
                    # Call appropriate method based on action
                    if action == "play":
                        await self._music_controller_service.handle_play_music(enhanced_payload)
                    elif action == "stop":
                        await self._music_controller_service.handle_stop_music(enhanced_payload)
                    elif action == "list":
                        await self._music_controller_service.handle_list_music(enhanced_payload)
                    elif action == "install":
                        await self._music_controller_service.handle_install_music(enhanced_payload)
                    elif action == "debug":
                        await self._music_controller_service.handle_debug_music(enhanced_payload)
                    else:
                        # For unknown actions, use the general handler
                        await self._music_controller_service._handle_music_command(enhanced_payload)
                except AttributeError as e:
                    self.logger.warning(f"Music controller method not available: {e}")
                    # Fallback to event emission
                    await self.emit(EventTopics.MUSIC_COMMAND, enhanced_payload)
                except Exception as e:
                    self.logger.error(f"Error calling music controller directly: {e}")
                    # Fallback to event emission
                    await self.emit(EventTopics.MUSIC_COMMAND, enhanced_payload)
            else:
                # Fallback to event emission if no direct service reference
                await self.emit(EventTopics.MUSIC_COMMAND, enhanced_payload)

            self.logger.debug(
                f"Forwarded music command with provider context: {self._current_provider}"
            )

        except Exception as e:
            self.logger.error(f"Error handling music command: {e}")
            await self._emit_status(
                ServiceStatus.ERROR,
                f"Error processing music command: {e}",
                severity=LogLevel.ERROR,
            )

    async def _handle_spotify_command(self, payload: Dict[str, Any]) -> None:
        """
        Handle incoming Spotify-specific commands.

        Routes Spotify commands to appropriate providers and handles provider switching.

        Args:
            payload: The event payload containing command details
        """
        try:
            self.logger.debug(f"Handling Spotify command: {payload}")

            # Extract command info from standardized payload
            command = payload.get("command", "").lower()
            args = payload.get("args", [])
            raw_input = payload.get("raw_input", "").lower()

            # Handle provider switching commands
            if "switch to spotify" in raw_input:
                await self._switch_to_provider("spotify", payload)
                return
            elif "switch to local" in raw_input:
                await self._switch_to_provider("local", payload)
                return

            # Parse Spotify-specific commands
            spotify_action = None
            query_parts = []

            if raw_input.startswith("spotify play"):
                spotify_action = "play"
                query_parts = raw_input.replace("spotify play", "").strip().split()
            elif raw_input.startswith("play spotify"):
                spotify_action = "play"
                query_parts = raw_input.replace("play spotify", "").strip().split()
            elif raw_input.startswith("spotify search"):
                spotify_action = "search"
                query_parts = raw_input.replace("spotify search", "").strip().split()
            elif raw_input.startswith("search spotify"):
                spotify_action = "search"
                query_parts = raw_input.replace("search spotify", "").strip().split()
            elif raw_input.startswith("spotify stop"):
                spotify_action = "stop"
            elif raw_input.startswith("spotify status"):
                spotify_action = "status"
            else:
                # Fallback parsing for other formats
                if command == "spotify" and len(args) > 0:
                    spotify_action = args[0]
                    query_parts = args[1:] if len(args) > 1 else []

            if not spotify_action:
                await self._send_command_response(
                    f"Unknown Spotify command: {raw_input}. Try 'spotify play <query>', 'spotify search <query>', or 'switch to spotify'.",
                    is_error=True,
                )
                return

            # Ensure Spotify provider is available
            if "spotify" not in self._providers:
                await self._send_command_response(
                    "Spotify provider not available. Please configure Spotify integration.",
                    is_error=True,
                )
                return

            spotify_provider = self._providers["spotify"]
            if not spotify_provider.is_available:
                await self._send_command_response(
                    "Spotify provider is not currently available. Please check your Spotify configuration.",
                    is_error=True,
                )
                return

            # Handle specific Spotify actions
            if spotify_action == "play":
                query = " ".join(query_parts) if query_parts else ""
                if not query:
                    await self._send_command_response(
                        "Please specify what to play. Example: 'spotify play bohemian rhapsody'",
                        is_error=True,
                    )
                    return
                await self._handle_spotify_play(query, payload)

            elif spotify_action == "search":
                query = " ".join(query_parts) if query_parts else ""
                if not query:
                    await self._send_command_response(
                        "Please specify what to search for. Example: 'spotify search queen'",
                        is_error=True,
                    )
                    return
                await self._handle_spotify_search(query, payload)

            elif spotify_action == "stop":
                await self._handle_spotify_stop(payload)

            elif spotify_action == "status":
                await self._handle_spotify_status(payload)

            else:
                await self._send_command_response(
                    f"Unknown Spotify action: {spotify_action}. Available actions: play, search, stop, status",
                    is_error=True,
                )

        except Exception as e:
            self.logger.error(f"Error handling Spotify command: {e}")
            await self._send_command_response(
                f"Error processing Spotify command: {str(e)}", is_error=True
            )

    async def _handle_unified_search(
        self, query: str, original_payload: Dict[str, Any]
    ) -> None:
        """
        Perform unified search across all available providers.

        Args:
            query: Search query string
            original_payload: Original command payload
        """
        try:
            search_tasks = []

            if self._config.search_all_providers:
                # Search all available providers
                for provider_name, provider in self._providers.items():
                    if provider.is_available:
                        search_tasks.append(self._search_provider(provider, query))
            else:
                # Search only current provider
                if self._current_provider and self._current_provider in self._providers:
                    provider = self._providers[self._current_provider]
                    if provider.is_available:
                        search_tasks.append(self._search_provider(provider, query))

            # Execute searches in parallel
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            # Aggregate results
            all_tracks = []
            provider_results = {}

            for i, result in enumerate(search_results):
                if isinstance(result, Exception):
                    self.logger.warning(f"Search failed for provider {i}: {result}")
                    continue

                if isinstance(result, TrackSearchResult):
                    all_tracks.extend(result.tracks[: self._config.max_search_results])
                    provider_results[result.provider] = {
                        "tracks": len(result.tracks),
                        "duration_ms": result.search_duration_ms,
                    }

            # Sort by relevance (providers can implement their own scoring)
            # For now, we'll keep the order as-is

            # Emit search results
            {
                "query": query,
                "total_results": len(all_tracks),
                "tracks": [track.model_dump() for track in all_tracks],
                "provider_results": provider_results,
                "search_timestamp": time.time(),
            }

            # Emit to appropriate topic (would need to be defined in EventTopics)
            # await self.emit(EventTopics.MUSIC_SEARCH_RESULTS, search_response)

            self.logger.info(
                f"Unified search for '{query}' returned {len(all_tracks)} results"
            )

        except Exception as e:
            self.logger.error(f"Unified search failed: {e}")

    async def _search_provider(
        self, provider: MusicProvider, query: str
    ) -> TrackSearchResult:
        """
        Search a specific provider with timeout.

        Args:
            provider: Provider to search
            query: Search query

        Returns:
            TrackSearchResult: Search results
        """
        try:
            return await asyncio.wait_for(
                provider.search(query), timeout=self._config.provider_timeout
            )
        except asyncio.TimeoutError:
            self.logger.warning(f"Search timeout for provider {provider.name}")
            return TrackSearchResult(
                tracks=[], query=query, provider=provider.name, total_results=0
            )

    async def _emit_provider_status_update(self) -> None:
        """
        Emit provider status update event.
        """
        try:
            {
                "providers": {
                    name: status.model_dump()
                    for name, status in self._provider_status.items()
                },
                "current_provider": self._current_provider,
                "total_tracks": len(self._aggregated_library),
                "timestamp": time.time(),
            }

            # Emit to appropriate topic (would need to be defined in EventTopics)
            # await self.emit(EventTopics.MUSIC_PROVIDER_STATUS, status_payload)

        except Exception as e:
            self.logger.error(f"Failed to emit provider status update: {e}")

    async def get_provider_status(self) -> Dict[str, Any]:
        """
        Get current status of all providers.

        Returns:
            Dict[str, Any]: Provider status information
        """
        return {
            "providers": {
                name: status.model_dump()
                for name, status in self._provider_status.items()
            },
            "current_provider": self._current_provider,
            "total_tracks": len(self._aggregated_library),
            "library_last_updated": self._library_last_updated,
        }

    async def search_all_providers(self, query: str) -> List[Track]:
        """
        Search all available providers for tracks.

        Args:
            query: Search query string

        Returns:
            List[Track]: Aggregated search results
        """
        try:
            # Trigger unified search
            await self._handle_unified_search(
                query, {"action": "search", "query": query}
            )

            # Return immediate search from aggregated library as fallback
            matching_tracks = [
                track
                for track in self._aggregated_library
                if track.matches_query(query)
            ]

            return matching_tracks[: self._config.max_search_results]

        except Exception as e:
            self.logger.error(f"Search all providers failed: {e}")
            return []

    async def get_aggregated_library(self) -> List[Track]:
        """
        Get the aggregated music library from all providers.

        Returns cached library if recent, otherwise refreshes it.

        Returns:
            List[Track]: All tracks from all providers
        """
        try:
            # Check if cache is still valid
            if (
                self._library_last_updated
                and time.time() - self._library_last_updated
                < self._library_cache_duration
            ):
                return self._aggregated_library.copy()

            # Refresh library
            await self._update_aggregated_library()
            return self._aggregated_library.copy()

        except Exception as e:
            self.logger.error(f"Failed to get aggregated library: {e}")
            return []

    async def _switch_to_provider(
        self, provider_name: str, payload: Dict[str, Any]
    ) -> None:
        """
        Switch the active music provider.

        Args:
            provider_name: Name of the provider to switch to
            payload: Original command payload for context
        """
        try:
            if provider_name not in self._providers:
                await self._send_command_response(
                    f"Provider '{provider_name}' is not available.", is_error=True
                )
                return

            provider = self._providers[provider_name]
            if not provider.is_available:
                await self._send_command_response(
                    f"Provider '{provider_name}' is not currently available.",
                    is_error=True,
                )
                return

            old_provider = self._current_provider
            self._current_provider = provider_name

            # Emit provider change event
            from ...event_payloads import MusicProviderChangedPayload

            change_payload = MusicProviderChangedPayload(
                previous_provider=old_provider or "none",
                current_provider=provider_name,
                reason="user_request",
                available_providers=list(self._providers.keys()),
            )
            await self.emit(
                EventTopics.MUSIC_PROVIDER_CHANGED, change_payload.model_dump()
            )

            await self._send_command_response(
                f"Switched to {provider_name} music provider.", is_error=False
            )

            self.logger.info(
                f"Provider switched from {old_provider} to {provider_name}"
            )

        except Exception as e:
            self.logger.error(f"Error switching to provider {provider_name}: {e}")
            await self._send_command_response(
                f"Failed to switch to {provider_name}: {str(e)}", is_error=True
            )

    async def _handle_spotify_play(self, query: str, payload: Dict[str, Any]) -> None:
        """
        Handle Spotify play command.

        Args:
            query: Track/artist/album to search and play
            payload: Original command payload
        """
        try:
            # Switch to Spotify provider if not already active
            if self._current_provider != "spotify":
                await self._switch_to_provider("spotify", payload)

            # Create music command for playing Spotify content
            music_payload = {
                "action": "play",
                "query": query,
                "provider": "spotify",
                "source": "spotify_command",
                "args": [query],
                "raw_input": payload.get("raw_input", f"spotify play {query}"),
            }

            # Emit to music controller with Spotify provider context
            await self.emit(EventTopics.MUSIC_COMMAND, music_payload)

            await self._send_command_response(
                f"Searching Spotify for '{query}' and starting playback...",
                is_error=False,
            )

        except Exception as e:
            self.logger.error(f"Error handling Spotify play: {e}")
            await self._send_command_response(
                f"Failed to play from Spotify: {str(e)}", is_error=True
            )

    async def _handle_spotify_search(self, query: str, payload: Dict[str, Any]) -> None:
        """
        Handle Spotify search command.

        Args:
            query: Search query
            payload: Original command payload
        """
        try:
            spotify_provider = self._providers.get("spotify")
            if not spotify_provider:
                await self._send_command_response(
                    "Spotify provider not available.", is_error=True
                )
                return

            # Perform search using Spotify provider
            search_result = await self._search_provider(spotify_provider, query)

            if search_result.tracks:
                track_list = []
                for i, track in enumerate(search_result.tracks[:10], 1):  # Show top 10
                    track_list.append(f"{i}. {track.title} by {track.artist}")

                response = f"Spotify search results for '{query}':\n" + "\n".join(
                    track_list
                )
                await self._send_command_response(response, is_error=False)
            else:
                await self._send_command_response(
                    f"No results found on Spotify for '{query}'.", is_error=False
                )

        except Exception as e:
            self.logger.error(f"Error handling Spotify search: {e}")
            await self._send_command_response(
                f"Failed to search Spotify: {str(e)}", is_error=True
            )

    async def _handle_spotify_stop(self, payload: Dict[str, Any]) -> None:
        """
        Handle Spotify stop command.

        Args:
            payload: Original command payload
        """
        try:
            # Create stop command
            music_payload = {
                "action": "stop",
                "provider": "spotify",
                "source": "spotify_command",
                "raw_input": payload.get("raw_input", "spotify stop"),
            }

            # Emit to music controller
            await self.emit(EventTopics.MUSIC_COMMAND, music_payload)

            await self._send_command_response(
                "Stopping Spotify playback...", is_error=False
            )

        except Exception as e:
            self.logger.error(f"Error handling Spotify stop: {e}")
            await self._send_command_response(
                f"Failed to stop Spotify: {str(e)}", is_error=True
            )

    async def _handle_spotify_status(self, payload: Dict[str, Any]) -> None:
        """
        Handle Spotify status command.

        Args:
            payload: Original command payload
        """
        try:
            spotify_provider = self._providers.get("spotify")
            if not spotify_provider:
                await self._send_command_response(
                    "Spotify provider not available.", is_error=True
                )
                return

            # Get provider status
            status = self._provider_status.get("spotify")
            if status:
                health_score = status.health_score * 100
                status_msg = (
                    f"Spotify Provider Status:\n"
                    f"• Status: {status.status}\n"
                    f"• Health: {health_score:.1f}%\n"
                    f"• Available: {'Yes' if spotify_provider.is_available else 'No'}\n"
                    f"• Active Provider: {'Yes' if self._current_provider == 'spotify' else 'No'}"
                )

                if status.error_message:
                    status_msg += f"\n• Error: {status.error_message}"

                await self._send_command_response(status_msg, is_error=False)
            else:
                await self._send_command_response(
                    "Spotify provider status not available.", is_error=True
                )

        except Exception as e:
            self.logger.error(f"Error handling Spotify status: {e}")
            await self._send_command_response(
                f"Failed to get Spotify status: {str(e)}", is_error=True
            )

    async def _send_command_response(
        self, message: str, is_error: bool = False
    ) -> None:
        """
        Send a command response via CLI_RESPONSE event.

        Args:
            message: Response message
            is_error: Whether this is an error response
        """
        try:
            response_payload = {
                "message": message,
                "is_error": is_error,
                "source": "music_source_manager",
            }
            await self.emit(EventTopics.CLI_RESPONSE, response_payload)

        except Exception as e:
            self.logger.error(f"Failed to send command response: {e}")
